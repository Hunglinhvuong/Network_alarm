"""
Bot polling và alarm collector chạy cùng một tiến trình.

THEO MÚI GIỜ: logging được cấu hình để dùng múi giờ APP_TIMEZONE thay vì UTC của hệ thống.
"""
import logging
import asyncio
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram.ext import Application, CommandHandler

from alerting.bot import handlers  # noqa: F401 — import để trigger đăng ký @command
from alerting.bot.handlers.registry import COMMAND_HANDLERS
from config.settings import APP_TIMEZONE, LOG_LEVEL, TELEGRAM_BOT_TOKEN
from main_collector import run_loop


# Định nghĩa hàm lấy thời gian thực tế theo múi giờ ứng dụng (APP_TIMEZONE)
def app_time_converter(*args):
    """Converter cho logging.Formatter để hiển thị thời gian theo múi giờ ứng dụng."""
    return datetime.now(ZoneInfo(APP_TIMEZONE)).timetuple()


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Ép toàn bộ hệ thống logging sử dụng múi giờ ứng dụng thay vì UTC của hệ điều hành
logging.Formatter.converter = app_time_converter

logger = logging.getLogger("telegram_bot")
_collector_stop = None
_collector_thread = None
_COLLECTOR_RESTART_INITIAL_SEC = 5
_COLLECTOR_RESTART_MAX_SEC = 60


def _run_collector_supervised(stop_event):
    restart_delay = _COLLECTOR_RESTART_INITIAL_SEC
    while not stop_event.is_set():
        try:
            run_loop(stop_event=stop_event, install_signal_handlers=False)
            if stop_event.is_set():
                break
            logger.warning("Alarm collector đã dừng ngoài yêu cầu; sẽ thử khởi động lại.")
        except Exception:
            logger.exception("Alarm collector lỗi khi khởi động; sẽ thử lại sau %.1f giây.", restart_delay)

        if stop_event.wait(restart_delay):
            break
        restart_delay = min(restart_delay * 2, _COLLECTOR_RESTART_MAX_SEC)


async def _handle_update_error(update, context):
    error = context.error
    if error is None:
        logger.error("Telegram update thất bại nhưng không có exception chi tiết.")
        return
    logger.error(
        "Lỗi khi xử lý Telegram update; bot tiếp tục nhận update khác.",
        exc_info=(type(error), error, error.__traceback__),
    )


async def _start_collector(application):
    global _collector_stop, _collector_thread
    _collector_stop = threading.Event()
    _collector_thread = threading.Thread(
        target=_run_collector_supervised,
        args=(_collector_stop,),
        name="alarm-collector",
        daemon=True,
    )
    _collector_thread.start()
    logger.info("Alarm collector đã khởi chạy trong cùng tiến trình với bot.")


async def _stop_collector(application):
    if _collector_stop is not None:
        _collector_stop.set()
    if _collector_thread is not None and _collector_thread.is_alive():
        await asyncio.to_thread(_collector_thread.join, 15)
        if _collector_thread.is_alive():
            logger.error("Collector chưa dừng sau 15 giây; tiến trình sẽ thoát khi cleanup hoàn tất.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN chưa cấu hình — kiểm tra file .env")

    logger.info("Khởi động bot tra cứu Telegram (múi giờ: %s)...", APP_TIMEZONE)
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_start_collector)
        .post_shutdown(_stop_collector)
        .build()
    )

    for name, desc, callback in COMMAND_HANDLERS:
        app.add_handler(CommandHandler(name, callback))
        logger.info("Đã đăng ký lệnh /%s — %s", name, desc)
    app.add_error_handler(_handle_update_error)

    logger.info("Bot tra cứu khởi động, đang polling...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
