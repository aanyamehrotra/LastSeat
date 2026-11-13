from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import socket
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


    def test_page_reflects_hold_expiry_without_exposing_token(self):
        client = app.test_client()
        page_path = f"/events/{self.event_id}"
        self.assertIn("Available", client.get(page_path).get_data(as_text=True))
        hold = client.post(self.path, json={"seat": "A1"}).get_json()
        response = client.get(page_path)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Held", response.get_data(as_text=True))
        self.assertNotIn(hold["hold_token"], response.get_data(as_text=True))
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "UPDATE seats SET hold_expires_at = clock_timestamp() - interval '1 second' WHERE event_id = %s",
                (self.event_id,),
            )
        self.assertIn("Available", client.get(page_path).get_data(as_text=True))
        replacement = client.post(self.path, json={"seat": "A1"})
        self.assertEqual(replacement.status_code, 201)
        self.assertNotEqual(hold["hold_token"], replacement.get_json()["hold_token"])

    def test_confirmation_persists_and_booked_seat_stays_unavailable(self):
        client = app.test_client()
        hold = client.post(self.path, json={"seat": "A1"}).get_json()
        response = client.post(
            f"/events/{self.event_id}/bookings", json={"seat": "A1", "hold_token": hold["hold_token"]}
        )
        self.assertEqual(response.status_code, 201)
        booking = response.get_json()
        with psycopg.connect(self.database_url) as connection:
            stored = connection.execute(
                "SELECT booking_id, booked_at FROM seats WHERE event_id = %s", (self.event_id,)
            ).fetchone()
            connection.execute(
                "UPDATE seats SET hold_expires_at = clock_timestamp() - interval '1 second' WHERE event_id = %s",
                (self.event_id,),
            )
        self.assertEqual(str(stored[0]), booking["booking_id"])
        self.assertEqual(stored[1].isoformat(), booking["booked_at"])
        self.assertEqual(client.post(self.path, json={"seat": "A1"}).status_code, 409)
        page = client.get(f"/events/{self.event_id}").get_data(as_text=True)
        self.assertIn("Booked", page)
        self.assertNotIn(booking["booking_id"], page)
        self.assertNotIn(hold["hold_token"], page)

    def test_confirmation_rejects_wrong_expired_and_replaced_tokens(self):
        client = app.test_client()
        path = f"/events/{self.event_id}/bookings"
        old = client.post(self.path, json={"seat": "A1"}).get_json()["hold_token"]
        wrong = client.post(path, json={"seat": "A1", "hold_token": "00000000-0000-0000-0000-000000000000"})
        self.assertEqual(wrong.status_code, 409)
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "UPDATE seats SET hold_expires_at = clock_timestamp() - interval '1 second' WHERE event_id = %s",
                (self.event_id,),
            )
        self.assertEqual(client.post(path, json={"seat": "A1", "hold_token": old}).status_code, 409)
        current = client.post(self.path, json={"seat": "A1"}).get_json()["hold_token"]
        self.assertEqual(client.post(path, json={"seat": "A1", "hold_token": old}).status_code, 409)
        self.assertEqual(client.post(path, json={"seat": "A1", "hold_token": current}).status_code, 201)

    def test_confirmation_validation(self):
        client = app.test_client()
        path = f"/events/{self.event_id}/bookings"
        for payload in (None, [], {}, {"seat": "A1"}, {"seat": "A1", "hold_token": 1},
                        {"seat": "A1", "hold_token": "invalid"}):
            with self.subTest(payload=payload):
                self.assertEqual(client.post(path, json=payload).status_code, 400)

    def test_competing_confirmations_create_only_one_booking(self):
        hold = app.test_client().post(self.path, json={"seat": "A1"}).get_json()
        barrier = threading.Barrier(2)

        def confirm(_):
            with app.test_client() as client:
                barrier.wait(timeout=10)
                response = client.post(
                    f"/events/{self.event_id}/bookings",
                    json={"seat": "A1", "hold_token": hold["hold_token"]},
                )
                return response.status_code, response.get_json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(confirm, range(2)))
        self.assertEqual(sorted(status for status, _ in responses), [200, 201])
        self.assertEqual(responses[0][1], responses[1][1])

    def test_failed_confirmation_rolls_back_and_can_be_attempted_again(self):
        client = app.test_client()
        hold = client.post(self.path, json={"seat": "A1"}).get_json()
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "ALTER TABLE seats ADD CONSTRAINT test_booking_failure CHECK (booking_id IS NULL)"
            )
        path = f"/events/{self.event_id}/bookings"
        try:
            with self.assertLogs(app.logger, level="ERROR") as logs:
                response = client.post(path, json={"seat": "A1", "hold_token": hold["hold_token"]})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(hold["hold_token"], "\n".join(logs.output))
            with psycopg.connect(self.database_url) as connection:
                stored = connection.execute(
                    "SELECT booking_id, booked_at, hold_token FROM seats WHERE event_id = %s",
                    (self.event_id,),
                ).fetchone()
            self.assertEqual(stored[:2], (None, None))
            self.assertEqual(str(stored[2]), hold["hold_token"])
        finally:
            with psycopg.connect(self.database_url) as connection:
                connection.execute("ALTER TABLE seats DROP CONSTRAINT test_booking_failure")
        self.assertEqual(client.post(path, json={"seat": "A1", "hold_token": hold["hold_token"]}).status_code, 201)


    def test_confirmation_replay_after_expiry_returns_original_ticket(self):
        client = app.test_client()
        hold = client.post(self.path, json={"seat": "A1"}).get_json()
        payload = {"seat": "A1", "hold_token": hold["hold_token"]}
        path = f"/events/{self.event_id}/bookings"
        first = client.post(path, json=payload)
        self.assertEqual(first.status_code, 201)
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "UPDATE seats SET hold_expires_at = clock_timestamp() - interval '1 second' WHERE event_id = %s",
                (self.event_id,),
            )
        replay = client.post(path, json=payload)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.get_json(), first.get_json())
        self.assertEqual(replay.headers["Cache-Control"], "no-store")
        payload["hold_token"] = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(client.post(path, json=payload).status_code, 409)

    def test_retry_recovers_ticket_when_http_client_disconnects(self):
        hold = app.test_client().post(self.path, json={"seat": "A1"}).get_json()
        path = f"/events/{self.event_id}/bookings"
        payload = {"seat": "A1", "hold_token": hold["hold_token"]}
        completed = threading.Event()

        def observe_confirmation(environ, start_response):
            result = app(environ, start_response)
            if environ["PATH_INFO"] == path:
                completed.set()
            return result

        server = make_server("127.0.0.1", 0, observe_confirmation, threaded=True)
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.start()
        try:
            body = json.dumps(payload).encode()
            request = (
                f"POST {path} HTTP/1.1\r\n"
                "Host: localhost\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            ).encode() + body
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=10) as client:
                client.sendall(request)
                # Disconnect without reading the booking response.
            self.assertTrue(completed.wait(timeout=10))
            with psycopg.connect(self.database_url) as connection:
                original = connection.execute(
                    "SELECT booking_id, booked_at FROM seats WHERE event_id = %s", (self.event_id,)
                ).fetchone()
            self.assertIsNotNone(original[0])
            retry = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}{path}",
                data=body, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(retry, timeout=10) as response:
                self.assertEqual(response.status, 200)
                recovered = json.load(response)
            self.assertEqual(recovered["booking_id"], str(original[0]))
            self.assertEqual(recovered["booked_at"], original[1].isoformat())
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()
