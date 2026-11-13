import os
from datetime import datetime
from uuid import UUID, uuid4

import click

from flask import Flask, abort, jsonify, render_template, request
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)


@app.get("/")
def list_events():
    try:
        with psycopg.connect(
            os.environ.get("DATABASE_URL", "dbname=lastseat"),
            connect_timeout=5,
            row_factory=dict_row,
        ) as connection:
            events = connection.execute(
                "SELECT id, name, starts_at FROM events ORDER BY starts_at, id"
            ).fetchall()
    except psycopg.Error as error:
        app.logger.error("Could not load events (SQLSTATE %s)", error.sqlstate or "connection")
        return render_template(
            "events.html", events=[], error="Events are temporarily unavailable. Please try again."
        ), 503

    return render_template("events.html", events=events, error=None)


@app.get("/events/<int:event_id>")
def show_event(event_id):
    if event_id > 2147483647:
        abort(404)
    try:
        with psycopg.connect(
            os.environ.get("DATABASE_URL", "dbname=lastseat"),
            connect_timeout=5,
            row_factory=dict_row,
        ) as connection:
            rows = connection.execute(
                """
                SELECT e.name, e.starts_at, s.label,
                       CASE WHEN s.booking_id IS NOT NULL THEN 'Booked'
                            WHEN s.hold_expires_at > clock_timestamp()
                            THEN 'Held' ELSE 'Available' END AS status
                FROM events e
                LEFT JOIN seats s ON s.event_id = e.id
                WHERE e.id = %s
                ORDER BY s.label
                """,
                (event_id,),
            ).fetchall()
    except psycopg.Error as error:
        app.logger.error("Could not load event seats (SQLSTATE %s)", error.sqlstate or "connection")
        return render_template(
            "event.html", event=None, seats=[], error="Seats are temporarily unavailable. Please try again."
        ), 503

    if not rows:
        abort(404)
    seats = [row for row in rows if row["label"] is not None]
    response = app.make_response(
        render_template("event.html", event=rows[0], seats=seats, error=None)
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/events/<int:event_id>/holds")
def hold_seat(event_id):
    if event_id > 2147483647:
        abort(404)
    data = request.get_json(silent=True)
    label = data.get("seat") if isinstance(data, dict) else None
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 20:
        return jsonify(error="Provide a seat label between 1 and 20 characters."), 400

    token = uuid4()
    try:
        with psycopg.connect(
            os.environ.get("DATABASE_URL", "dbname=lastseat"),
            connect_timeout=5,
        ) as connection:
            held = connection.execute(
                """
                UPDATE seats
                SET hold_token = %s,
                    hold_expires_at = clock_timestamp() + interval '2 minutes'
                WHERE event_id = %s AND label = %s
                  AND booking_id IS NULL
                  AND (hold_token IS NULL OR hold_expires_at <= clock_timestamp())
                RETURNING hold_expires_at
                """,
                (token, event_id, label.strip()),
            ).fetchone()
    except psycopg.Error as error:
        app.logger.error("Could not hold seat (SQLSTATE %s)", error.sqlstate or "connection")
        return jsonify(error="Could not reserve the seat. Please try again."), 503

    if held is None:
        return jsonify(error="Seat is unavailable or does not exist."), 409
    response = jsonify(
        seat=label.strip(), hold_token=str(token), expires_at=held[0].isoformat()
    )
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@app.post("/events/<int:event_id>/bookings")
def confirm_booking(event_id):
    if event_id > 2147483647:
        abort(404)
    data = request.get_json(silent=True)
    label = data.get("seat") if isinstance(data, dict) else None
    token = data.get("hold_token") if isinstance(data, dict) else None
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 20:
        return jsonify(error="Provide a seat label between 1 and 20 characters."), 400
    try:
        token = UUID(token) if isinstance(token, str) else None
    except ValueError:
        token = None
    if token is None:
        return jsonify(error="Provide a valid hold token."), 400

    booking_id = uuid4()
    try:
        with psycopg.connect(
            os.environ.get("DATABASE_URL", "dbname=lastseat"), connect_timeout=5
        ) as connection:
            booked = connection.execute(
                """
                UPDATE seats
                SET booking_id = %s, booked_at = clock_timestamp()
                WHERE event_id = %s AND label = %s
                  AND hold_token = %s AND hold_expires_at > clock_timestamp()
                  AND booking_id IS NULL
                RETURNING booking_id, booked_at
                """,
                (booking_id, event_id, label.strip(), token),
            ).fetchone()
            status = 201
            if booked is None:
                # A concurrent confirmation may have committed while our UPDATE waited.
                booked = connection.execute(
                    """
                    SELECT booking_id, booked_at FROM seats
                    WHERE event_id = %s AND label = %s AND hold_token = %s
                      AND booking_id IS NOT NULL
                    """,
                    (event_id, label.strip(), token),
                ).fetchone()
                status = 200
    except psycopg.Error as error:
        app.logger.error("Could not confirm booking (SQLSTATE %s)", error.sqlstate or "connection")
        return jsonify(error="Could not confirm the booking. Please try again."), 503

    if booked is None:
        return jsonify(error="Hold is expired, replaced, or does not exist."), 409
    response = jsonify(
        booking_id=str(booked[0]), seat=label.strip(), booked_at=booked[1].isoformat()
    )
    response.headers["Cache-Control"] = "no-store"
    return response, status


@app.cli.command("create-event")
@click.option("--name", required=True, help="Event name.")
@click.option("--starts-at", required=True, help="ISO date/time with a UTC offset.")
@click.option("--seat", "seats", multiple=True, required=True, help="Seat label. Repeat for each seat.")
def create_event(name, starts_at, seats):
    """Create an event and its seats in one transaction."""
    name = name.strip()
    labels = [seat.strip() for seat in seats]
    if not name or len(name) > 200:
        raise click.ClickException("Use an event name between 1 and 200 characters.")
    try:
        start = datetime.fromisoformat(starts_at)
    except ValueError:
        raise click.ClickException("Use an ISO date/time, such as 2026-12-04T19:00:00+05:30.")
    if start.utcoffset() is None:
        raise click.ClickException("Include the time zone offset, such as +05:30.")
    if not 1 <= len(labels) <= 1000:
        raise click.ClickException("Provide between 1 and 1000 seats.")
    if any(not label or len(label) > 20 for label in labels):
        raise click.ClickException("Seat labels must contain 1 to 20 characters.")
    if len(set(labels)) != len(labels):
        raise click.ClickException("Each seat label must be unique within the event.")

    try:
        with psycopg.connect(
            os.environ.get("DATABASE_URL", "dbname=lastseat"), connect_timeout=5
        ) as connection:
            event_id = connection.execute(
                "INSERT INTO events (name, starts_at) VALUES (%s, %s) RETURNING id",
                (name, start),
            ).fetchone()[0]
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO seats (event_id, label) VALUES (%s, %s)",
                    [(event_id, label) for label in labels],
                )
    except psycopg.Error as error:
        app.logger.error("Could not create event (SQLSTATE %s)", error.sqlstate or "connection")
        raise click.ClickException("Could not create the event. No partial event was saved.")

    click.echo(f"Created event {event_id} with {len(labels)} seats.")
