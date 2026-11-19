"""Deliberately unsafe comparison confined to a disposable demo event."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import threading
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from app import app


def main():
    database = os.environ.get("TEST_DATABASE_URL")
    if not database:
        raise SystemExit("Set TEST_DATABASE_URL to a dedicated test database with schema.sql installed.")
    os.environ["DATABASE_URL"] = database
    with psycopg.connect(database) as connection:
        event_id = connection.execute(
            "INSERT INTO events(name, starts_at) VALUES ('Race demo', now()) RETURNING id"
        ).fetchone()[0]
        connection.execute("INSERT INTO seats(event_id, label) VALUES (%s, 'A1')", (event_id,))
    try:
        barrier = threading.Barrier(2)

        def unsafe(_):
            # Both callers see availability before either writes. This is the bug.
            with psycopg.connect(database, autocommit=True) as connection:
                available = connection.execute(
                    "SELECT hold_token IS NULL FROM seats WHERE event_id = %s AND label = 'A1'",
                    (event_id,),
                ).fetchone()[0]
                barrier.wait(timeout=10)
                if not available:
                    raise RuntimeError("Expected a free demo seat")
                token = uuid4()
                connection.execute(
                    """UPDATE seats SET hold_token = %s,
                       hold_expires_at = clock_timestamp() + interval '2 minutes'
                       WHERE event_id = %s AND label = 'A1'""", (token, event_id),
                )
                return token

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(unsafe, range(2)))
        with psycopg.connect(database) as connection:
            stored = connection.execute(
                "SELECT hold_token FROM seats WHERE event_id = %s", (event_id,)
            ).fetchone()[0]
            if len(set(claims)) != 2 or stored not in claims:
                raise RuntimeError("Unsafe race did not reproduce")
            connection.execute(
                "UPDATE seats SET hold_token = NULL, hold_expires_at = NULL WHERE event_id = %s",
                (event_id,),
            )
        print("UNSAFE: both callers reported success; one caller's token was overwritten.")

        barrier = threading.Barrier(2)
        def safe(_):
            with app.test_client() as client:
                barrier.wait(timeout=10)
                return client.post(f"/events/{event_id}/holds", json={"seat": "A1"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(safe, range(2)))
        if sorted(response.status_code for response in responses) != [201, 409]:
            raise RuntimeError("Safe endpoint did not produce one winner")
        winner = next(response.get_json() for response in responses if response.status_code == 201)
        with psycopg.connect(database) as connection:
            stored = connection.execute(
                "SELECT hold_token FROM seats WHERE event_id = %s", (event_id,)
            ).fetchone()[0]
        if str(stored) != winner["hold_token"]:
            raise RuntimeError("Stored token did not match the winner")
        print("SAFE: one caller succeeded, one received a conflict; the winner's token persisted.")
    finally:
        with psycopg.connect(database) as connection:
            connection.execute("DELETE FROM seats WHERE event_id = %s", (event_id,))
            connection.execute("DELETE FROM events WHERE id = %s", (event_id,))


if __name__ == "__main__":
    main()
