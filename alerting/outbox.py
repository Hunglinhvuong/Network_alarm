"""Durable, sequential Telegram delivery with retry and global rate-limit cooldown."""
import logging
import random
import threading
from datetime import datetime, timedelta, timezone

import requests

from config.settings import (
    TELEGRAM_ALERT_CHAT_IDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_GLOBAL_SEND_INTERVAL_SEC,
    TELEGRAM_GROUP_SEND_INTERVAL_SEC,
    TELEGRAM_OUTBOX_POLL_INTERVAL_SEC,
    TELEGRAM_SEND_INTERVAL_SEC,
)
from db.connection import create_connection, get_cursor

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_LOCK_SECONDS = 60
_MAX_BACKOFF_SECONDS = 300


def enqueue_messages(text: str, chat_ids=None) -> int:
    """Lưu một bản tin hoặc các trang của nó cho từng chat, trả về số tin đã xếp."""
    from alerting.pagination import paginate_message

    targets = chat_ids if chat_ids is not None else TELEGRAM_ALERT_CHAT_IDS
    pages = paginate_message(text)
    with get_cursor(dict_cursor=False, commit=True) as cur:
        cur.executemany(
            "INSERT INTO telegram_chat_limits (chat_id) VALUES (%s) ON CONFLICT (chat_id) DO NOTHING",
            [(str(chat_id),) for chat_id in targets],
        )
        cur.executemany(
            "INSERT INTO telegram_outbox (chat_id, message_text) VALUES (%s, %s)",
            [(str(chat_id), page) for chat_id in targets for page in pages],
        )
    return len(targets) * len(pages)


def _claim_next(connection):
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT q.queue_id, q.chat_id, q.message_text, q.attempts
                        FROM telegram_outbox q
                        JOIN telegram_outbox_control c ON c.control_id = 1
                        JOIN telegram_chat_limits cl ON cl.chat_id = q.chat_id
                        WHERE c.paused_until <= now()
              AND c.next_send_at <= now()
                            AND cl.next_send_at <= now()
              AND (
                    (q.status = 'pending' AND q.next_attempt_at <= now())
                    OR (q.status = 'sending' AND q.locked_until <= now())
              )
                            AND NOT EXISTS (
                                        SELECT 1
                                        FROM telegram_outbox earlier
                                        WHERE earlier.chat_id = q.chat_id
                                            AND earlier.queue_id < q.queue_id
                                            AND earlier.status IN ('pending', 'sending')
          )
            ORDER BY q.queue_id
            LIMIT 1
                        FOR UPDATE OF q, c, cl SKIP LOCKED
            """
        )
        row = cur.fetchone()
        if row is None:
            connection.commit()
            return None
        cur.execute(
            """
            UPDATE telegram_outbox
            SET status = 'sending', attempts = attempts + 1,
                locked_until = now() + (%s * interval '1 second')
            WHERE queue_id = %s
            """,
            (_LOCK_SECONDS, row[0]),
        )
        cur.execute(
            """
            UPDATE telegram_outbox_control
            SET next_send_at = now() + (%s * interval '1 second')
            WHERE control_id = 1
            """,
            (TELEGRAM_GLOBAL_SEND_INTERVAL_SEC,),
        )
        chat_interval = (
            TELEGRAM_GROUP_SEND_INTERVAL_SEC
            if str(row[1]).startswith("-")
            else TELEGRAM_SEND_INTERVAL_SEC
        )
        cur.execute(
            """
            UPDATE telegram_chat_limits
            SET next_send_at = now() + (%s * interval '1 second')
            WHERE chat_id = %s
            """,
            (chat_interval, row[1]),
        )
        connection.commit()
    return {
        "queue_id": row[0],
        "chat_id": row[1],
        "message_text": row[2],
        "attempts": row[3] + 1,
    }


def _finish_sent(connection, queue_id):
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE telegram_outbox
            SET status = 'sent', sent_at = now(), locked_until = NULL, last_error = NULL
            WHERE queue_id = %s
            """,
            (queue_id,),
        )
    connection.commit()


def _retry(connection, job, error, delay, pause_all=False):
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE telegram_outbox
            SET status = 'pending', next_attempt_at = %s,
                locked_until = NULL, last_error = %s, failed_at = NULL
            WHERE queue_id = %s
            """,
            (retry_at, error[:1000], job["queue_id"]),
        )
        if pause_all:
            cur.execute(
                """
                UPDATE telegram_outbox_control
                SET paused_until = GREATEST(paused_until, %s),
                    next_send_at = GREATEST(next_send_at, %s)
                WHERE control_id = 1
                """,
                (retry_at, retry_at),
            )
    connection.commit()


def _mark_failed(connection, queue_id, error):
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE telegram_outbox
            SET status = 'failed', locked_until = NULL, last_error = %s, failed_at = now()
            WHERE queue_id = %s
            """,
            (error[:1000], queue_id),
        )
    connection.commit()


def _backoff(attempts):
    return min(2 ** min(attempts, 8), _MAX_BACKOFF_SECONDS) + random.uniform(0, 1)


def _retry_policy(status_code, payload, attempts):
    if status_code == 429:
        retry_after = payload.get("parameters", {}).get("retry_after", 1)
        return max(1, int(retry_after)) + random.uniform(0, 1), True
    if status_code == 408 or status_code >= 500:
        return _backoff(attempts), False
    return None, False


def _send_one(connection, job, token):
    try:
        response = requests.post(
            _API_URL.format(token=token),
            json={
                "chat_id": job["chat_id"],
                "text": job["message_text"],
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code == 200 and payload.get("ok") is True:
            _finish_sent(connection, job["queue_id"])
            return

        error = payload.get("description", response.text[:300])
        delay, pause_all = _retry_policy(response.status_code, payload, job["attempts"])
        if delay is not None:
            _retry(connection, job, error, delay, pause_all=pause_all)
            if pause_all:
                logger.warning("Telegram rate limit; tạm dừng gửi %.1fs", delay)
            else:
                logger.warning("Telegram tạm lỗi status=%s; retry sau %.1fs", response.status_code, delay)
        else:
            _mark_failed(connection, job["queue_id"], error)
            logger.error(
                "Telegram từ chối tin chat_id=%s status=%s: %s",
                job["chat_id"], response.status_code, error,
            )
    except requests.RequestException as exc:
        delay = _backoff(job["attempts"])
        _retry(connection, job, str(exc), delay)
        logger.warning("Lỗi mạng Telegram; retry sau %.1fs", delay)


class TelegramOutboxWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="telegram-outbox", daemon=True)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=12)

    def run(self):
        connection = None
        try:
            while not self._stop_event.is_set():
                try:
                    if connection is None or connection.closed:
                        connection = create_connection()
                    job = _claim_next(connection)
                    if job is None:
                        self._stop_event.wait(TELEGRAM_OUTBOX_POLL_INTERVAL_SEC)
                        continue
                    _send_one(connection, job, TELEGRAM_BOT_TOKEN)
                except Exception:
                    logger.exception("Lỗi worker Telegram outbox")
                    if connection is not None:
                        try:
                            connection.rollback()
                            connection.close()
                        except Exception:
                            pass
                        connection = None
                    self._stop_event.wait(TELEGRAM_OUTBOX_POLL_INTERVAL_SEC)
        finally:
            if connection is not None and not connection.closed:
                connection.close()


_worker = None
_worker_lock = threading.Lock()


def start_outbox_worker():
    global _worker
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALERT_CHAT_IDS:
        logger.warning("Thiếu Telegram token/chat IDs; outbox worker chưa khởi động")
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = TelegramOutboxWorker()
            _worker.start()


def stop_outbox_worker():
    global _worker
    with _worker_lock:
        worker = _worker
        _worker = None
    if worker is not None:
        worker.stop()