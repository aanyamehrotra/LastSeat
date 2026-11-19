# LastSeat

A seat-booking application built around the final-seat race. PostgreSQL grants
two-minute holds atomically, confirms only the valid holder, and returns the
original ticket when a confirmation is retried after a lost response.

Built with Flask, PostgreSQL, plain JavaScript, and Gunicorn.

## Release status

**[Try the live demo](https://lastseat-demo-3rcd.onrender.com)**

The booking flow, desktop/mobile Chromium checks, database upgrades, the
100-caller HTTP race, and production Gunicorn startup are verified. The Render
demo also passed real HTTPS checks for a hold, competing-request rejection,
confirmation, and a retry returning the same ticket. Reproducible tests are
included below.

## Run locally

Use Python 3.11+, PostgreSQL 14+, and a running PostgreSQL service.

```sh
git clone https://github.com/aanyamehrotra/LastSeat.git
cd LastSeat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
createdb lastseat
psql -X -v ON_ERROR_STOP=1 -d lastseat -f schema.sql
export DATABASE_URL="dbname=lastseat"
flask --app app create-event --name "Friday Gig" \
  --starts-at "2026-12-04T19:00:00+05:30" --seat A1 --seat A2
flask --app app run
```

Open http://127.0.0.1:5000, choose an event, hold a seat, and confirm it.
The page shows the ticket number and keeps the current hold and receipt in
sessionStorage for that tab. Save your ticket number. Closing the tab can clear
that storage. When storage is unavailable, keep the page open until confirmation.
Organizer event creation uses the local CLI. There is no payment processing.

For an existing pre-hold database, apply migrations/001_seat_holds.sql and
migrations/002_bookings.sql once each, in order, using psql with ON_ERROR_STOP=1.
A database already containing holds needs only 002. New databases use schema.sql
directly. Stop the app during an upgrade and restart afterward. The migration
test verifies that legacy events and seats survive and can be booked.

## Test and demonstrate

Use a dedicated test database because the integration suite injects temporary
constraints and creates isolated migration schemas.

```sh
pip install -r requirements-dev.txt
python -m playwright install chromium
createdb lastseat_test
psql -X -v ON_ERROR_STOP=1 -d lastseat_test -f schema.sql
export TEST_DATABASE_URL="dbname=lastseat_test"
RUN_BROWSER_TESTS=1 python -m unittest discover -s tests -v
node --test tests/booking.test.cjs
python scripts/race_demo.py
python scripts/load_test.py
```

The JavaScript tests require Node 22+. On Linux, install browser system
dependencies with python -m playwright install --with-deps chromium.
Without RUN_BROWSER_TESTS=1, the three Chromium tests are skipped.

The release passed 25 Python tests, including three real Chromium tests, and
10 JavaScript tests. Coverage includes a hold expiring during a row-lock wait,
wrong/replaced tokens, confirmation replay after expiry, rollback on injected
failure, cross-event isolation, legacy migrations, mobile layout, refresh
recovery, and a browser response lost after the server committed the booking.

The HTTP race test starts 100 callers together and requires one winner,
99 conflicts, and the saved winning token. The separate race_demo script forces
two callers through an unsafe SELECT-then-UPDATE sequence, showing two false
success claims. It then calls the real hold endpoint through Flask test clients
and checks one success and one conflict. Unsafe SQL is confined to that script's
disposable event. The application always uses the protected endpoint.

For a three-minute interview demo, run the unsafe comparison, explain the
check/write gap, show the conditional UPDATE, then run the HTTP race test.
Finish with the browser booking flow and explain why a lost confirmation
response is recovered with the same token.

## API

| Endpoint | Input | Successful result |
| --- | --- | --- |
| GET / | None | Event list |
| GET /events/ID | None | Seat page |
| POST /events/ID/holds | JSON seat | 201 with hold_token and expires_at |
| POST /events/ID/bookings | JSON seat and hold_token | 201 on first confirmation, 200 on replay |

Invalid JSON/input returns 400. An unavailable or missing seat, invalid holder,
or expired unconfirmed hold returns 409. Database failures return 503.
Unknown event pages return 404. Successful hold and booking responses include
Cache-Control no-store. Tokens belong in the JSON body and must remain private.

## Production startup and deployment

```sh
gunicorn app:app --bind "0.0.0.0:${PORT:-8000}" --workers 2 --threads 4 --timeout 30
```

Set DATABASE_URL to the persistent PostgreSQL connection string supplied by the
host. Use its required SSL settings and HTTPS for browser traffic. Initialize the
database with schema.sql once, or apply only the outstanding migrations for an
existing database. Create demo inventory through the CLI against that database.
The development flask run server is for local use.

The Dockerfile packages the same Gunicorn entry point. It runs as an unprivileged
user, reads PORT, and requires an external PostgreSQL database. GitHub CI runs
the Python/browser and JavaScript suites, builds the image, and verifies its
database-backed home page. Credentials belong in host environment settings.

The live demo uses one Free Render web service in Singapore and Free PostgreSQL,
within the approved ₹0 allowance. `render.yaml` binds the existing `lastseat-db`
connection privately. `start-demo.sh` initializes one 48-seat demo event once,
preserves existing bookings on restart, and starts Gunicorn. Initialization uses
a database advisory lock to serialize overlapping starts.

Render's Free web service sleeps after 15 idle minutes, so the first visit can
take longer. This demo database expires on **December 20, 2026 at 01:08 IST**
(December 19 at 19:38 UTC). The demo is temporary and must be migrated or recreated
before expiry to remain available. No paid upgrades are selected.
See [Render's documented limits](https://render.com/docs/free).

## Measured local performance

A November 18, 2026 sample used 200 requests, 20 concurrent workers, and one seat.

| Measurement | Observed result |
| --- | --- |
| Responses | 1 HTTP 201 and 199 HTTP 409 |
| Elapsed | 1.461 seconds |
| Throughput | 136.93 requests/second |
| Median latency | 52.78 ms |
| p95 latency | 946.86 ms |
| Machine | macOS 26.5.2, arm64, 8 logical CPUs |
| Runtime | Python 3.14.2, PostgreSQL 14.18 Homebrew |

Each worker sends ten requests. Latency includes localhost HTTP connection
setup, the development server queue, and a fresh database connection. Percentiles
use nearest-rank over 200 responses. Elapsed time includes worker startup.
This single, short, conflict-heavy sample does not establish successful booking
throughput or production capacity. The script prints environment details so
future measurements can be compared honestly.

## Resume wording

Built LastSeat, a Flask/PostgreSQL seat-booking application with atomic expiring
holds and idempotent confirmation recovery. Verified one winner across 100
simultaneous HTTP requests and covered booking conflicts, database upgrades,
and desktop/mobile flows with 35 automated tests.

## Design and trade-offs


### Request path and ownership

The browser sends JSON to one Flask application. PostgreSQL owns seat availability,
hold deadlines, and booking identity. The browser stores its current token and
receipt in tab-scoped sessionStorage. Page availability and the local countdown
are estimates; the write endpoint decides whether a request can succeed.

### Data model and indexes

An event has a generated integer ID, name, and timestamp. Each seat belongs to
an event through a foreign key. Its composite primary key, (event_id, label),
prevents duplicate labels within that event and creates a B-tree index usable
for a particular seat or the seats of one event. The event primary key supports
the event lookup. booking_id has a unique index to prevent duplicate ticket IDs.

A seat stores its current hold and final booking directly. Check constraints
require token/deadline and booking/timestamp pairs to be complete. A booked seat
must retain a holder token. This representation supports one immutable booking
per seat. Cancellation, transfers, payment records, booking history, and user
accounts would require explicit lifecycle and authorization changes.

### Transactions and competing requests

| Operation | Transaction boundary | Result |
| --- | --- | --- |
| Create event | Insert event and all seats in one connection context | All inserts commit together or roll back |
| Hold seat | One conditional UPDATE, commit before HTTP success | Only an unbooked, free or expired seat gets a token |
| Confirm hold | Conditional UPDATE and, when needed, replay SELECT in one transaction | A valid holder creates a ticket or recovers its existing one |

PostgreSQL row locks serialize competing updates to the same seat. Under the
default READ COMMITTED isolation level, an UPDATE that waits for a changed row
rechecks its condition against the updated version. The confirmation replay
SELECT gets a new statement snapshot and can see the ticket committed by a
concurrent winner. This reasoning assumes the application retains its default
isolation level and confirmed bookings remain immutable.

The database clock establishes hold expiry. Expired holds are replaced lazily by
the next successful hold request, so correctness needs no background cleanup.
Requests for different seats can proceed independently. Every request currently
opens a new database connection.

### Failures and recovery

A failure before commit rolls back the transaction. A connection failure around
commit can leave the client uncertain whether the write succeeded. Confirmation
retries carry the same event, seat, and token, allowing the server to return the
saved booking. Replays remain valid after the original hold deadline once that
booking exists. A different token cannot recover it.

A lost hold response cannot recover an unknown token in the current API. The
browser asks for refreshed availability and the unconfirmed hold expires.
Tokens act as bearer credentials; anyone possessing one can confirm or recover
that hold's booking. Keep them out of URLs and logs and use HTTPS in deployment.
Error logs record the SQLSTATE category without row values. Cache-Control
no-store is used on hold/booking success responses and the seat page.

### Scaling decisions to explain

More application workers can serve different seats, while one popular seat
still serializes at its database row. Connection pooling is a candidate when
measurements show connection setup or limits dominate. Event lists currently
sort all events with no pagination or starts_at index; growth should be measured
before introducing those changes. A cache could accelerate event descriptions,
but seat writes would still require PostgreSQL's availability check.

The localhost load sample measures a conflict-heavy workload, not successful
booking capacity. It includes the development server queue and new connections.
A production evaluation needs a production WSGI server, deployment hardware,
networked clients, successful bookings, sustained load, and separate error-rate
reporting. The isolated race demonstration and desktop/mobile Chromium checks are verified.
The Free live demo verifies deployment and booking behavior, not production-scale
capacity or availability.
