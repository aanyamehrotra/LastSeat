# LastSeat

A ticket-booking app for the moment two people try to book the last seat. The goal is a small Python web app backed by PostgreSQL. The database will decide who holds a seat, even when requests arrive together.

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
