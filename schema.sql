BEGIN;

CREATE TABLE events (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL CHECK (btrim(name) <> ''),
    starts_at timestamptz NOT NULL
);

CREATE TABLE seats (
    event_id integer NOT NULL REFERENCES events(id),
    label text NOT NULL CHECK (btrim(label) <> ''),
    booking_id uuid UNIQUE,
    booked_at timestamptz,
    CONSTRAINT seat_booking_complete CHECK (
        (booking_id IS NULL) = (booked_at IS NULL)
    ),
    CONSTRAINT booked_seat_has_holder CHECK (
        booking_id IS NULL OR hold_token IS NOT NULL
    ),
    hold_token uuid,
    hold_expires_at timestamptz,
    CONSTRAINT seat_hold_complete CHECK (
        (hold_token IS NULL) = (hold_expires_at IS NULL)
    ),
    PRIMARY KEY (event_id, label)
);

COMMIT;
