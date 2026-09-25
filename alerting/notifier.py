"""
Gửi cảnh báo chủ động (push) tới các chat Telegram cấu hình trong TELEGRAM_ALERT_CHAT_IDS.

Dùng thẳng HTTP API sendMessage qua `requests` thay vì thư viện python-telegram-bot,
vì đây là tiến trình main_collector (đẩy tin một chiều) — không cần bộ máy
polling/dispatcher nặng của thư viện bot, chạy nhẹ hơn trên Wyse 5010 và không xung
đột getUpdates với bot tra cứu (alerting/bot/app.py) chạy ở tiến trình riêng.
"""
import logging

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_IDS

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


def send_alert(text: str, chat_ids=None) -> None:
    """
    Gửi `text` tới danh sách chat_ids (mặc định = TELEGRAM_ALERT_CHAT_IDS trong config).
    Lỗi gửi tới 1 chat không làm dừng việc gửi tới các chat còn lại.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN chưa cấu hình -> bỏ qua gửi cảnh báo: %s", text[:80])
        return

    targets = chat_ids if chat_ids is not None else TELEGRAM_ALERT_CHAT_IDS
    if not targets:
        logger.warning("TELEGRAM_ALERT_CHAT_IDS chưa cấu hình -> bỏ qua gửi cảnh báo: %s", text[:80])
        return

    url = _API_BASE.format(token=TELEGRAM_BOT_TOKEN)
    for chat_id in targets:
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("Gửi Telegram thất bại chat_id=%s status=%s body=%s", chat_id, resp.status_code, resp.text[:300])
        except requests.RequestException:
            logger.exception("Lỗi mạng khi gửi Telegram tới chat_id=%s", chat_id)
