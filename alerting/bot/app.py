"""
Entry point cho bot tra cứu Telegram. Chạy: python -m alerting.bot.app

Đây là tiến trình RIÊNG với main_collector.py:
  - main_collector.py: đẩy cảnh báo chủ động (push, qua alerting/notifier.py, HTTP thuần)
  - alerting/bot/app.py: bot tương tác (polling getUpdates, xử lý lệnh /status, /tra...)
Tách riêng để 1 bên crash không ảnh hưởng bên kia, và tránh 2 tiến trình cùng gọi
getUpdates gây xung đột (Telegram chỉ cho 1 getUpdates long-poll tại 1 thời điểm).
"""
import logging

from telegram.ext import Application, CommandHandler

from config.settings import TELEGRAM_BOT_TOKEN, LOG_LEVEL
from alerting.bot import handlers  # noqa: F401 — import để trigger đăng ký @command
from alerting.bot.handlers.registry import COMMAND_HANDLERS

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telegram_bot")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN chưa cấu hình — kiểm tra file .env")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    for name, desc, callback in COMMAND_HANDLERS:
        app.add_handler(CommandHandler(name, callback))
        logger.info("Đã đăng ký lệnh /%s — %s", name, desc)

    logger.info("Bot tra cứu khởi động, đang polling...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
