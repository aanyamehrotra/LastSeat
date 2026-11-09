import os

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
