BEGIN;

ALTER TABLE device
    ADD COLUMN IF NOT EXISTS num_cell INTEGER
    CHECK (num_cell IS NULL OR num_cell >= 0);

COMMIT;
