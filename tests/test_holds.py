from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import psycopg
from werkzeug.serving import make_server

from app import app


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a dedicated test database")
class HoldTests(unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ["TEST_DATABASE_URL"]
        environment = patch.dict(os.environ, {"DATABASE_URL": self.database_url})
        environment.start()
        self.addCleanup(environment.stop)
        with psycopg.connect(self.database_url) as connection:
            self.event_id = connection.execute(
                "INSERT INTO events(name, starts_at) VALUES (%s, %s) RETURNING id",
                ("Hold test", "2026-12-04T19:00:00+05:30"),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO seats(event_id, label) VALUES (%s, %s)", (self.event_id, "A1")
            )
        self.addCleanup(self.remove_event)
        self.path = f"/events/{self.event_id}/holds"

    def remove_event(self):
        with psycopg.connect(self.database_url) as connection:
            connection.execute("DELETE FROM seats WHERE event_id = %s", (self.event_id,))
            connection.execute("DELETE FROM events WHERE id = %s", (self.event_id,))

    def test_hold_persists_and_competitor_cannot_replace_it(self):
        client = app.test_client()
        response = client.post(self.path, json={"seat": "A1"})
        self.assertEqual(response.status_code, 201)
        hold = response.get_json()
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        datetime.fromisoformat(hold["expires_at"])
        self.assertEqual(client.post(self.path, json={"seat": "A1"}).status_code, 409)
        with psycopg.connect(self.database_url) as connection:
            token, remaining = connection.execute(
                "SELECT hold_token, hold_expires_at - clock_timestamp() FROM seats WHERE event_id = %s",
                (self.event_id,),
            ).fetchone()
        self.assertEqual(str(token), hold["hold_token"])
        self.assertTrue(0 < remaining.total_seconds() <= 120)

    def test_expired_hold_can_be_replaced(self):
        client = app.test_client()
        first = client.post(self.path, json={"seat": "A1"}).get_json()
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "UPDATE seats SET hold_expires_at = clock_timestamp() - interval '1 second' WHERE event_id = %s",
                (self.event_id,),
            )
        second = client.post(self.path, json={"seat": "A1"})
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first["hold_token"], second.get_json()["hold_token"])

    def test_invalid_and_missing_seats_do_not_create_holds(self):
        client = app.test_client()
        for payload in (None, [], {}, {"seat": 1}, {"seat": ""}, {"seat": "x" * 21}):
            with self.subTest(payload=payload):
                self.assertEqual(client.post(self.path, json=payload).status_code, 400)
        self.assertEqual(client.post(self.path, json={"seat": "missing"}).status_code, 409)
        with psycopg.connect(self.database_url) as connection:
            self.assertIsNone(connection.execute(
                "SELECT hold_token FROM seats WHERE event_id = %s", (self.event_id,)
            ).fetchone()[0])

    def test_simultaneous_http_requests_have_one_winner(self):
        server = make_server("127.0.0.1", 0, app, threaded=True)
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.start()
        barrier = threading.Barrier(8)

        def reserve(_):
            barrier.wait(timeout=10)
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}{self.path}",
                data=json.dumps({"seat": "A1"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as response:
                with response:
                    return response.code, json.load(response)

        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(reserve, range(8)))
            self.assertEqual(sorted(status for status, _ in responses), [201] + [409] * 7)
            winner = next(body for status, body in responses if status == 201)
            with psycopg.connect(self.database_url) as connection:
                stored = connection.execute(
                    "SELECT hold_token FROM seats WHERE event_id = %s", (self.event_id,)
                ).fetchone()[0]
            self.assertEqual(str(stored), winner["hold_token"])
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()

    def test_database_failure_returns_503(self):
        with patch.dict(os.environ, {"DATABASE_URL": "host=127.0.0.1 port=1 dbname=lastseat"}):
            response = app.test_client().post(self.path, json={"seat": "A1"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("connection failed", response.get_data(as_text=True))
