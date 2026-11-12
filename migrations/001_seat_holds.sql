BEGIN;
ALTER TABLE seats
    ADD COLUMN hold_token uuid,
    ADD COLUMN hold_expires_at timestamptz,
    ADD CONSTRAINT seat_hold_complete CHECK (
        (hold_token IS NULL) = (hold_expires_at IS NULL)
    );
COMMIT;
