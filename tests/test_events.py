import os
import unittest
from unittest.mock import patch

import psycopg

from app import app


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a dedicated test database")
class CreateEventTests(unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ["TEST_DATABASE_URL"]
        self.environment = patch.dict(os.environ, {"DATABASE_URL": self.database_url})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.runner = app.test_cli_runner()

    def create(self, *extra):
        return self.runner.invoke(args=[
            "create-event", "--name", "Test gig",
            "--starts-at", "2026-12-04T19:00:00+05:30", *extra,
        ])

    def test_creates_event_and_seats(self):
        result = self.create("--seat", " A1 ", "--seat", "A2")
        self.assertEqual(result.exit_code, 0, result.output)
        event_id = int(result.output.split()[2])
        try:
            with psycopg.connect(self.database_url) as connection:
                labels = connection.execute(
                    "SELECT label FROM seats WHERE event_id = %s ORDER BY label", (event_id,)
                ).fetchall()
                self.assertEqual(labels, [("A1",), ("A2",)])
                self.assertIn("Test gig", app.test_client().get("/").get_data(as_text=True))
        finally:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("DELETE FROM seats WHERE event_id = %s", (event_id,))
                connection.execute("DELETE FROM events WHERE id = %s", (event_id,))

    def test_invalid_inputs_leave_no_event(self):
        with psycopg.connect(self.database_url) as connection:
            before = connection.execute("SELECT count(*) FROM events").fetchone()[0]
        cases = [
            ["--seat", "A1", "--seat", " A1 "],
            ["--seat", " "],
            ["--seat", "A1", "--name", " "],
            ["--seat", "A1", "--starts-at", "2026-12-04T19:00"],
            ["--seat", "A1", "--starts-at", "invalid"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assertNotEqual(self.create(*arguments).exit_code, 0)
        with psycopg.connect(self.database_url) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM events").fetchone()[0], before)

    def test_failed_seat_insert_rolls_back_event(self):
        # A temporary constraint fails the second seat after the first was inserted.
        with psycopg.connect(self.database_url) as connection:
            connection.execute("ALTER TABLE seats ADD CONSTRAINT test_rejected_label CHECK (label <> '__FAIL__')")
            before = connection.execute("SELECT count(*) FROM events").fetchone()[0]
        try:
            result = self.create("--seat", "A1", "--seat", "__FAIL__")
            self.assertNotEqual(result.exit_code, 0)
            with psycopg.connect(self.database_url) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM events").fetchone()[0], before)
                self.assertEqual(connection.execute("SELECT count(*) FROM seats WHERE label = '__FAIL__'").fetchone()[0], 0)
        finally:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("ALTER TABLE seats DROP CONSTRAINT test_rejected_label")

    def test_event_page_isolates_seats_and_escapes_labels(self):
        first = self.create("--seat", "<b>A1</b>", "--seat", "A2")
        second = self.create("--seat", "OTHER_EVENT_ONLY")
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        event_ids = [int(result.output.split()[2]) for result in (first, second)]
        try:
            client = app.test_client()
            home = client.get("/").get_data(as_text=True)
            self.assertIn(f'href="/events/{event_ids[0]}"', home)
            page = client.get(f"/events/{event_ids[0]}")
            self.assertEqual(page.status_code, 200)
            body = page.get_data(as_text=True)
            self.assertIn("&lt;b&gt;A1&lt;/b&gt;", body)
            self.assertNotIn("<b>A1</b>", body)
            self.assertNotIn("OTHER_EVENT_ONLY", body)
            self.assertIn("A2", body)
            self.assertIn('href="/"', body)
        finally:
            with psycopg.connect(self.database_url) as connection:
                for event_id in event_ids:
                    connection.execute("DELETE FROM seats WHERE event_id = %s", (event_id,))
                    connection.execute("DELETE FROM events WHERE id = %s", (event_id,))

    def test_event_without_seats_and_missing_event(self):
        with psycopg.connect(self.database_url) as connection:
            event_id = connection.execute(
                "INSERT INTO events(name, starts_at) VALUES (%s, %s) RETURNING id",
                ("No seats", "2026-12-04T19:00:00+05:30"),
            ).fetchone()[0]
        try:
            response = app.test_client().get(f"/events/{event_id}")
            self.assertEqual(response.status_code, 200)
            self.assertIn("No seats have been added", response.get_data(as_text=True))
        finally:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("DELETE FROM events WHERE id = %s", (event_id,))
        self.assertEqual(app.test_client().get(f"/events/{event_id}").status_code, 404)
        self.assertEqual(app.test_client().get("/events/99999999999999999999").status_code, 404)

    def test_seat_page_database_failure(self):
        with patch.dict(os.environ, {"DATABASE_URL": "host=127.0.0.1 port=1 dbname=lastseat"}):
            response = app.test_client().get("/events/1")
        self.assertEqual(response.status_code, 503)
        body = response.get_data(as_text=True)
        self.assertIn("temporarily unavailable", body)
        self.assertNotIn("connection failed", body)
