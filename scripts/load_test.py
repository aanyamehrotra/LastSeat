"""Run a local contention measurement against a dedicated test database."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import json
import math
import os
from pathlib import Path
import platform
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from werkzeug.serving import make_server, WSGIRequestHandler
from app import app


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        pass


def main():
    database = os.environ.get("TEST_DATABASE_URL")
    if not database:
        raise SystemExit("Set TEST_DATABASE_URL to a dedicated test database with schema.sql installed.")
    os.environ["DATABASE_URL"] = database
    with psycopg.connect(database) as connection:
        event_id = connection.execute(
            "INSERT INTO events(name, starts_at) VALUES ('Load test', now()) RETURNING id"
        ).fetchone()[0]
        connection.execute("INSERT INTO seats(event_id, label) VALUES (%s, 'A1')", (event_id,))

    server = None
    thread = None
    try:
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        concurrency = 20
        requests = 200
        barrier = threading.Barrier(concurrency)
        def worker(_):
            barrier.wait(timeout=15)
            results = []
            for _ in range(requests // concurrency):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/events/{event_id}/holds",
                    data=b'{"seat":"A1"}', headers={"Content-Type": "application/json"},
                )
                start = time.perf_counter()
                try:
                    response = urllib.request.urlopen(request, timeout=20)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    body = json.load(response)
                    results.append((response.code, time.perf_counter() - start, body))
            return results
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = [result for batch in pool.map(worker, range(concurrency)) for result in batch]
        elapsed = time.perf_counter() - start
        counts = Counter(status for status, _, _ in results)
        if counts != {201: 1, 409: requests - 1}:
            raise RuntimeError(f"Unexpected responses: {dict(counts)}")
        winner = next(body for status, _, body in results if status == 201)
        with psycopg.connect(database) as connection:
            stored = connection.execute(
                "SELECT hold_token FROM seats WHERE event_id = %s", (event_id,)
            ).fetchone()[0]
            version = connection.execute("SHOW server_version").fetchone()[0]
        if str(stored) != winner["hold_token"]:
            raise RuntimeError("Stored token differs from the winner")
        latencies = sorted(duration * 1000 for _, duration, _ in results)
        print(json.dumps({
            "requests": requests, "concurrency": concurrency, "responses": dict(counts),
            "elapsed_seconds": round(elapsed, 3),
            "requests_per_second": round(requests / elapsed, 2),
            "p50_ms": round(latencies[math.ceil(requests * .50) - 1], 2),
            "p95_ms": round(latencies[math.ceil(requests * .95) - 1], 2),
            "machine": platform.machine(), "os": platform.platform(),
            "logical_cpus": os.cpu_count(), "python": platform.python_version(),
            "postgresql": version,
        }, indent=2))
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join()
        with psycopg.connect(database) as connection:
            connection.execute("DELETE FROM seats WHERE event_id = %s", (event_id,))
            connection.execute("DELETE FROM events WHERE id = %s", (event_id,))


if __name__ == "__main__":
    main()
