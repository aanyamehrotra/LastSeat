# LastSeat

A ticket-booking app for the moment two people try to book the last seat. The goal is a small Python web app backed by PostgreSQL. The database will decide who holds a seat, even when requests arrive together.

## Run locally

Use Python 3.11 or newer and PostgreSQL 14 or newer. Run these commands from the repository folder. After creating the database and installing the schema below, start the app:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="dbname=lastseat"
flask --app app run
```

Open http://127.0.0.1:5000. The development server is for local use.
Set DATABASE_URL to your PostgreSQL connection string when using a different database.
Keep credentials out of Git.

## Day 2 · November 9 · Event list

The home page reads events from PostgreSQL and orders them by start time and ID.
It shows a friendly empty state and returns HTTP 503 when the database is unavailable.
Event names are escaped by the template engine. Times include the database session's
time zone. Event creation and seat selection are upcoming milestones.

## Day 1 · Event inventory

The first version models events and their seats. A seat label belongs to one event. Two events can each have an A1, but one event cannot list A1 twice.

With PostgreSQL running, create an empty database and install the schema once:

```sh
createdb lastseat
psql -X -v ON_ERROR_STOP=1 -d lastseat -f schema.sql
```

Try this in `psql -d lastseat`. The example rolls back so you can repeat it.

```sql
BEGIN;
WITH new_event AS (
    INSERT INTO events (name, starts_at)
    VALUES ('Friday Gig', '2026-12-04 19:00+05:30')
    RETURNING id
)
INSERT INTO seats (event_id, label)
SELECT id, label FROM new_event CROSS JOIN (VALUES ('A1'), ('A2')) AS row_seats(label);

SELECT e.name, s.label
FROM events e JOIN seats s ON s.event_id = e.id
ORDER BY e.id, s.label;
ROLLBACK;
```

## Build plan · November 8–27, 2026

| Days | Milestones in order |
| --- | --- |
| 1–4 | Model inventory. List events through the web app. Create an event with seats. Display its seat map. |
| 5–8 | Acquire a hold atomically. Reclaim expired holds. Confirm a valid hold. Make confirmation retries return the same ticket. |
| 9–12 | Connect the booking screen. Show hold expiry in the browser. Test conflicting bookings against PostgreSQL. Race 100 requests for one seat through the HTTP endpoint. |
| 13–16 | Handle stale screens and retry errors. Run tests automatically on GitHub. Measure contention and record results. Document the transaction and its trade-offs. |
| 17–20 | Rehearse setup from a fresh checkout. Prepare the demo and deploy if hosting is available. Fix issues from the rehearsal. Verify the release and write a resume bullet using measured results. |

Day 2 verification used a temporary PostgreSQL database and requests to the actual
HTTP server. Checks passed for an empty database, chronological event ordering,
escaped HTML in event names, machine-readable timestamps, and a 503 response
without database details when the connection fails.
