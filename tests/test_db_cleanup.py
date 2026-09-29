import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from db.cleanup import cleanup_alarm_events, cleanup_telegram_outbox


class _Cursor:
    def __init__(self, rowcount=0, result=None):
        self.rowcount = rowcount
        self.result = result
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        return (self.result,)


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *_args):
        return False


class DatabaseCleanupTests(unittest.TestCase):
    def test_outbox_dry_run_only_counts_expired_terminal_statuses(self):
        cursor = _Cursor(result=7)
        now = datetime(2026, 9, 28, tzinfo=timezone.utc)

        with patch("db.cleanup.get_cursor", return_value=_CursorContext(cursor)):
            count = cleanup_telegram_outbox(dry_run=True, now=now)

        sql, params = cursor.executed[0]
        self.assertEqual(count, 7)
        self.assertIn("status = 'sent'", sql)
        self.assertIn("status = 'failed'", sql)
        self.assertNotIn("status = 'pending'", sql)
        self.assertNotIn("status = 'sending'", sql)
        self.assertEqual(len(params), 2)

    def test_alarm_cleanup_only_targets_cleared_rows(self):
        cursor = _Cursor(result=3)
        now = datetime(2026, 9, 28, tzinfo=timezone.utc)

        with patch("db.cleanup.get_cursor", return_value=_CursorContext(cursor)):
            count = cleanup_alarm_events(dry_run=True, now=now)

        sql, params = cursor.executed[0]
        self.assertEqual(count, 3)
        self.assertIn("status = 'cleared'", sql)
        self.assertIn("COALESCE(end_time, created_at)", sql)
        self.assertEqual(len(params), 1)

    def test_deletes_in_committed_batches_until_a_partial_batch(self):
        cursors = [_Cursor(rowcount=2), _Cursor(rowcount=1)]
        contexts = [_CursorContext(cursor) for cursor in cursors]

        with patch("db.cleanup.get_cursor", side_effect=contexts) as get_cursor:
            count = cleanup_alarm_events(batch_size=2)

        self.assertEqual(count, 3)
        self.assertEqual(get_cursor.call_count, 2)


if __name__ == "__main__":
    unittest.main()