"""Opt-in real Chromium checks against PostgreSQL and the local HTTP server."""
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import psycopg
from werkzeug.serving import make_server

from app import app


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Set RUN_BROWSER_TESTS=1 for Chromium checks")
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()
        cls.server = make_server("127.0.0.1", 0, app, threaded=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.database = os.environ["TEST_DATABASE_URL"]
        environment = patch.dict(os.environ, {"DATABASE_URL": self.database})
        environment.start()
        self.addCleanup(environment.stop)
        with psycopg.connect(self.database) as connection:
            self.event_id = connection.execute(
                "INSERT INTO events(name, starts_at) VALUES ('Browser test', '2026-12-04T19:00:00+05:30') RETURNING id"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO seats(event_id, label) VALUES (%s, 'A1'), (%s, 'A2')",
                (self.event_id, self.event_id),
            )
        self.addCleanup(self.remove_event)
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.url = f"http://127.0.0.1:{self.server.server_port}/events/{self.event_id}"

    def remove_event(self):
        with psycopg.connect(self.database) as connection:
            connection.execute("DELETE FROM seats WHERE event_id = %s", (self.event_id,))
            connection.execute("DELETE FROM events WHERE id = %s", (self.event_id,))

    def screenshot(self, name):
        directory = os.environ.get("BROWSER_SCREENSHOT_DIR")
        if directory:
            target = Path(directory)
            target.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(target / name), full_page=True)

    def test_hold_refresh_confirm_and_receipt_refresh(self):
        from playwright.sync_api import expect

        self.page.goto(self.url)
        self.page.get_by_role("button", name="A1 Available").click()
        expect(self.page.locator("#hold-countdown")).to_contain_text("Time remaining")
        self.page.reload()
        expect(self.page.locator("#reservation")).to_be_visible()
        self.page.get_by_role("button", name="Confirm booking").click()
        expect(self.page.locator("#reservation-title")).to_have_text("Booking confirmed")
        ticket = self.page.locator("#ticket").inner_text()
        self.assertIn("Ticket ", ticket)
        self.page.reload()
        expect(self.page.locator("#ticket")).to_have_text(ticket)
        self.screenshot("desktop-confirmed.png")
        self.assertEqual(self.errors, [])

    def test_mobile_seat_layout(self):
        from playwright.sync_api import expect

        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.url)
        expect(self.page.get_by_role("button", name="A1 Available")).to_be_enabled()
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        self.page.get_by_role("button", name="A2 Available").click()
        expect(self.page.locator("#reservation")).to_be_visible()
        self.screenshot("mobile-held.png")
        self.assertEqual(self.errors, [])

    def test_stale_seat_conflict_and_lost_confirmation_response(self):
        from playwright.sync_api import expect

        self.page.goto(self.url)
        competitor = self.context.new_page()
        competitor.goto(self.url)
        self.page.get_by_role("button", name="A1 Available").click()
        expect(self.page.locator("#reservation")).to_be_visible()
        competitor.get_by_role("button", name="A1 Available").click()
        expect(competitor.locator("#booking-message")).to_contain_text("Refresh availability")
        expect(competitor.get_by_role("button", name="A1 · Unavailable")).to_be_disabled()

        def lose_response(route):
            route.fetch()
            route.abort()

        endpoint = f"**/events/{self.event_id}/bookings"
        self.page.route(endpoint, lose_response)
        self.page.get_by_role("button", name="Confirm booking").click()
        expect(self.page.get_by_role("button", name="Retry confirmation")).to_be_visible()
        with psycopg.connect(self.database) as connection:
            ticket = connection.execute(
                "SELECT booking_id FROM seats WHERE event_id = %s AND label = 'A1'", (self.event_id,)
            ).fetchone()[0]
        self.assertIsNotNone(ticket)
        self.page.unroute(endpoint, lose_response)
        self.page.get_by_role("button", name="Retry confirmation").click()
        expect(self.page.locator("#ticket")).to_have_text("Ticket " + str(ticket))
        self.assertEqual(self.errors, [])
