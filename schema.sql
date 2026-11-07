BEGIN;

CREATE TABLE events (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL CHECK (btrim(name) <> ''),
    starts_at timestamptz NOT NULL
);

CREATE TABLE seats (
    event_id integer NOT NULL REFERENCES events(id),
    label text NOT NULL CHECK (btrim(label) <> ''),
    PRIMARY KEY (event_id, label)
);

COMMIT;
