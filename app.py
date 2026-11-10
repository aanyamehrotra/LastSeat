import os
from datetime import datetime

import click

from flask import Flask, render_template
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
    except psycopg.Error:
        app.logger.exception("Could not load events")
        return render_template(
            "events.html", events=[], error="Events are temporarily unavailable. Please try again."
        ), 503

    return render_template("events.html", events=events, error=None)


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
    except psycopg.Error:
        app.logger.exception("Could not create event")
        raise click.ClickException("Could not create the event. No partial event was saved.")

    click.echo(f"Created event {event_id} with {len(labels)} seats.")
