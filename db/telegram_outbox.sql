-- Apply before starting main_collector.py; safe to rerun when upgrading an existing database.
CREATE TABLE IF NOT EXISTS telegram_outbox_control (
    control_id SMALLINT PRIMARY KEY CHECK (control_id = 1),
    paused_until TIMESTAMPTZ NOT NULL DEFAULT '-infinity',
    next_send_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity'
);

INSERT INTO telegram_outbox_control (control_id)
VALUES (1)
ON CONFLICT (control_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS telegram_chat_limits (
    chat_id TEXT PRIMARY KEY,
    next_send_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity'
);

CREATE TABLE IF NOT EXISTS telegram_outbox (
    queue_id BIGSERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL,
    message_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_until TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ
);

ALTER TABLE telegram_outbox
    ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ;

UPDATE telegram_outbox
SET failed_at = created_at
WHERE status = 'failed' AND failed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_telegram_outbox_pending
    ON telegram_outbox (status, next_attempt_at, queue_id);