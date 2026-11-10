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
