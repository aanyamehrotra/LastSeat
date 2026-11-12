BEGIN;
ALTER TABLE seats
    ADD COLUMN booking_id uuid UNIQUE,
    ADD COLUMN booked_at timestamptz,
    ADD CONSTRAINT seat_booking_complete CHECK (
        (booking_id IS NULL) = (booked_at IS NULL)
    ),
    ADD CONSTRAINT booked_seat_has_holder CHECK (
        booking_id IS NULL OR hold_token IS NOT NULL
    );
COMMIT;
