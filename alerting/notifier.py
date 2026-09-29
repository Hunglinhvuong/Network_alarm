"""
Gửi cảnh báo chủ động (push) tới các chat Telegram cấu hình trong TELEGRAM_ALERT_CHAT_IDS.

Dùng thẳng HTTP API sendMessage qua `requests` thay vì thư viện python-telegram-bot,
vì đây là tiến trình main_collector (đẩy tin một chiều) — không cần bộ máy
polling/dispatcher nặng của thư viện bot, chạy nhẹ hơn trên Wyse 5010 và không xung
đột getUpdates với bot tra cứu (alerting/bot/app.py) chạy ở tiến trình riêng.
"""
import logging

from config.settings import TELEGRAM_ALERT_CHAT_IDS, TELEGRAM_BOT_TOKEN
from alerting.outbox import enqueue_messages, start_outbox_worker, stop_outbox_worker

logger = logging.getLogger(__name__)


def send_alert(text: str, chat_ids=None) -> None:
    """
    Xếp `text` vào PostgreSQL outbox để worker gửi tuần tự và retry khi lỗi.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN chưa cấu hình -> bỏ qua gửi cảnh báo: %s", text[:80])
        return

    targets = chat_ids if chat_ids is not None else TELEGRAM_ALERT_CHAT_IDS
    if not targets:
        logger.warning("TELEGRAM_ALERT_CHAT_IDS chưa cấu hình -> bỏ qua gửi cảnh báo: %s", text[:80])
        return

    try:
        queued = enqueue_messages(text, targets)
        logger.info("Đã xếp %d tin Telegram vào outbox", queued)
    except Exception:
        logger.exception("Không thể lưu tin Telegram vào outbox")
