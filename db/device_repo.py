"""
Tra cứu device theo device_code. Cache trong RAM (dict) vì bảng device nhỏ,
tránh query lặp lại liên tục trong vòng lặp poll (Wyse 5010 RAM yếu -> hạn chế round-trip DB).
Gọi refresh_cache() sau khi import_initial_data.py chạy lại (device có thể đổi).
"""
import logging

from db.connection import get_cursor

logger = logging.getLogger(__name__)

_device_cache = {}  # device_code -> device_id
_cache_loaded = False


def refresh_cache():
    global _device_cache, _cache_loaded
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute("SELECT device_id, device_code FROM device")
        rows = cur.fetchall()
    _device_cache = {row["device_code"]: row["device_id"] for row in rows}
    _cache_loaded = True
    logger.info("Đã nạp cache device: %d thiết bị", len(_device_cache))


def get_device_id(device_code):
    """Trả về device_id hoặc None nếu device_code không tồn tại trong DB."""
    if not _cache_loaded:
        refresh_cache()
    device_id = _device_cache.get(device_code)
    if device_id is None:
        # có thể device mới thêm sau lần cache cuối -> thử refresh 1 lần rồi tra lại
        refresh_cache()
        device_id = _device_cache.get(device_code)
    return device_id
