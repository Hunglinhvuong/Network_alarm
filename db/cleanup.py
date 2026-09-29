"""Retention cleanup for completed Telegram messages and cleared alarm history."""
import argparse
import logging
from datetime import datetime, timedelta, timezone

from config.settings import (
    ALARM_EVENT_RETENTION_DAYS,
    DATA_CLEANUP_BATCH_SIZE,
    TELEGRAM_OUTBOX_FAILED_RETENTION_DAYS,
    TELEGRAM_OUTBOX_SENT_RETENTION_DAYS,
)
from db.connection import get_cursor

logger = logging.getLogger(__name__)


def _delete_in_batches(sql, params, batch_size):
    deleted = 0
    while True:
        with get_cursor(dict_cursor=False, commit=True) as cur:
            cur.execute(sql, (*params, batch_size))
            batch_deleted = cur.rowcount
        deleted += batch_deleted
        if batch_deleted < batch_size:
            return deleted


def cleanup_telegram_outbox(dry_run=False, batch_size=DATA_CLEANUP_BATCH_SIZE, now=None):
    """Remove expired sent/failed rows; leave pending and sending rows untouched."""
    now = now or datetime.now(timezone.utc)
    sent_cutoff = now - timedelta(days=TELEGRAM_OUTBOX_SENT_RETENTION_DAYS)
    failed_cutoff = now - timedelta(days=TELEGRAM_OUTBOX_FAILED_RETENTION_DAYS)
    predicate = """
        (status = 'sent' AND sent_at < %s)
        OR (status = 'failed' AND COALESCE(failed_at, created_at) < %s)
    """

    if dry_run:
        with get_cursor(dict_cursor=False, commit=False) as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM telegram_outbox WHERE {predicate}",
                (sent_cutoff, failed_cutoff),
            )
            return cur.fetchone()[0]

    sql = f"""
        WITH candidates AS (
            SELECT queue_id
            FROM telegram_outbox
            WHERE {predicate}
            ORDER BY created_at, queue_id
            LIMIT %s
        )
        DELETE FROM telegram_outbox q
        USING candidates c
        WHERE q.queue_id = c.queue_id
    """
    return _delete_in_batches(sql, (sent_cutoff, failed_cutoff), batch_size)


def cleanup_alarm_events(dry_run=False, batch_size=DATA_CLEANUP_BATCH_SIZE, now=None):
    """Remove expired cleared alarms while preserving every active alarm."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=ALARM_EVENT_RETENTION_DAYS)
    predicate = "status = 'cleared' AND COALESCE(end_time, created_at) < %s"

    if dry_run:
        with get_cursor(dict_cursor=False, commit=False) as cur:
            cur.execute(f"SELECT COUNT(*) FROM alarm_event WHERE {predicate}", (cutoff,))
            return cur.fetchone()[0]

    sql = f"""
        WITH candidates AS (
            SELECT alarm_id
            FROM alarm_event
            WHERE {predicate}
            ORDER BY COALESCE(end_time, created_at), alarm_id
            LIMIT %s
        )
        DELETE FROM alarm_event a
        USING candidates c
        WHERE a.alarm_id = c.alarm_id
    """
    return _delete_in_batches(sql, (cutoff,), batch_size)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Purge expired database history in batches.")
    parser.add_argument(
        "--target",
        choices=("telegram-outbox", "alarm-events", "all"),
        default="all",
        help="Data set to inspect or clean (default: all).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count eligible rows without deleting.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cleanups = {
        "telegram-outbox": cleanup_telegram_outbox,
        "alarm-events": cleanup_alarm_events,
    }
    targets = cleanups if args.target == "all" else {args.target: cleanups[args.target]}
    for target, cleanup in targets.items():
        count = cleanup(dry_run=args.dry_run)
        action = "đủ điều kiện xóa" if args.dry_run else "đã xóa"
        logger.info("%s: %d bản ghi %s", target, count, action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())