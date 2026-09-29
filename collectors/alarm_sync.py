"""
So sánh snapshot alarm active lấy từ collector với trạng thái hiện tại trong DB:
  - Alarm có trong snapshot nhưng chưa có bản ghi 'active' trong DB -> INSERT mới.
  - Alarm có trong snapshot và đã 'active' trong DB -> không làm gì (giữ nguyên start_time gốc).
  - Alarm đang 'active' trong DB nhưng KHÔNG còn trong snapshot -> auto-clear
    (set end_time=now(), status='cleared').
"""
import logging
from datetime import datetime

from db.connection import get_cursor
from db.device_repo import get_device_id

logger = logging.getLogger(__name__)


def _get_active_alarms_from_db(cur):
    """Trả về dict {(device_id, alarm_name): alarm_id} cho các alarm đang active trong DB."""
    cur.execute("SELECT alarm_id, device_id, alarm_name FROM alarm_event WHERE status = 'active'")
    return {(row["device_id"], row["alarm_name"]): row["alarm_id"] for row in cur.fetchall()}


def sync_alarms(records) -> dict:
    """
    records: list[AlarmRecord] từ collector.fetch_active_alarms()
    Trả về dict thống kê {"new": n, "cleared": n, "unknown_device": n, "still_active": n}
    """
    stats = {"new": 0, "cleared": 0, "unknown_device": 0, "still_active": 0}
    now = datetime.now()

    with get_cursor(dict_cursor=True, commit=True) as cur:
        db_active = _get_active_alarms_from_db(cur)
        seen_keys = set()

        for rec in records:
            device_id = get_device_id(rec.device_code)
            if device_id is None:
                logger.warning("Alarm cho device_code không tồn tại trong DB: %s -> bỏ qua", rec.device_code)
                stats["unknown_device"] += 1
                continue

            key = (device_id, rec.alarm_name)
            seen_keys.add(key)

            if key in db_active:
                stats["still_active"] += 1
                continue

            start_time = rec.raw_start_time or now
            cur.execute(
                """
                INSERT INTO alarm_event (device_id, alarm_name, start_time, end_time, status)
                VALUES (%s, %s, %s, NULL, 'active')
                ON CONFLICT (device_id, alarm_name, start_time) DO NOTHING;
                """,
                (device_id, rec.alarm_name, start_time),
            )
            stats["new"] += 1
            logger.info("Alarm MỚI: device_id=%s alarm=%s start=%s", device_id, rec.alarm_name, start_time)

        # auto-clear: alarm active trong DB nhưng không còn xuất hiện ở snapshot lần này
        to_clear = set(db_active.keys()) - seen_keys
        for key in to_clear:
            alarm_id = db_active[key]
            cur.execute(
                "UPDATE alarm_event SET end_time = %s, status = 'cleared' WHERE alarm_id = %s",
                (now, alarm_id),
            )
            stats["cleared"] += 1
            logger.info("Alarm CLEARED: alarm_id=%s device_id=%s alarm=%s", alarm_id, key[0], key[1])

    if stats["new"] or stats["cleared"]:
        logger.info("Sync xong: %s", stats)
    return stats


def has_active_alarms() -> bool:
    """Dùng để quyết định adaptive polling interval."""
    with get_cursor(dict_cursor=False, commit=False) as cur:
        cur.execute("SELECT 1 FROM alarm_event WHERE status = 'active' LIMIT 1")
        return cur.fetchone() is not None
