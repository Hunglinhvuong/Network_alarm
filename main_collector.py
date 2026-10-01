"""
Entry point cho Alarm Collector. Chạy: python main_collector.py

Vòng lặp: poll collector -> sync vào DB -> ngủ (interval adaptive: nhanh hơn khi
đang có alarm active, chậm lại khi hệ thống yên để tiết kiệm tài nguyên trên Wyse 5010).

THEO MÚI GIỜ: logging được cấu hình để dùng múi giờ APP_TIMEZONE thay vì UTC của hệ thống.
"""
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config.settings import (
    LOG_LEVEL,
    POLL_INTERVAL_NORMAL_SEC,
    POLL_INTERVAL_ACTIVE_SEC,
    PERIODIC_REPORT_INTERVAL_MINUTES,
    PERIODIC_REPORT_START_TIME,
    APP_TIMEZONE,
)
from collectors.factory import build_collector
from collectors.alarm_sync import sync_alarms, has_active_alarms
from alarm_pipeline import AlarmPipeline
from db.connection import close_connection
from alerting.periodic_report import build_periodic_report, next_report_time
from alerting.notifier import send_alert, start_outbox_worker, stop_outbox_worker


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

logger = logging.getLogger("main_collector")

def run_loop(stop_event=None, install_signal_handlers=True):
    stop_event = stop_event or threading.Event()
    if install_signal_handlers:
        def handle_shutdown(signum, frame):
            logger.info("Nhận tín hiệu dừng (%s) -> thoát vòng lặp an toàn", signum)
            stop_event.set()

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        collector = build_collector()
        pipeline = AlarmPipeline()
        next_report_at = next_report_time(
            datetime.now(ZoneInfo(APP_TIMEZONE)),
            PERIODIC_REPORT_START_TIME,
            PERIODIC_REPORT_INTERVAL_MINUTES,
        )
        start_outbox_worker()
    except Exception:
        logger.exception("Không thể khởi tạo alarm collector")
        stop_outbox_worker()
        close_connection()
        raise
    logger.info("Alarm Collector khởi động, nguồn: %s (múi giờ: %s)", collector.__class__.__name__, APP_TIMEZONE)

    try:
        while not stop_event.is_set():
            cycle_start = time.time()
            try:
                records = collector.fetch_active_alarms()
                sync_alarms(records)
                result = pipeline.run_cycle()
                if result["new_alerts"] or result["resolved_alerts"] or result["new_partial_alerts"] or result["resolved_partial_alerts"]:
                    logger.info("Pipeline: %s", result)
                report_check_time = datetime.now(ZoneInfo(APP_TIMEZONE))
                if report_check_time >= next_report_at:
                    logger.info("Gửi báo cáo tổng hợp định kỳ")
                    send_alert(build_periodic_report())
                    while next_report_at <= report_check_time:
                        next_report_at += timedelta(minutes=PERIODIC_REPORT_INTERVAL_MINUTES)
            except Exception:
                logger.exception("Lỗi trong chu kỳ poll -> bỏ qua chu kỳ này, thử lại lần sau")

            try:
                interval = POLL_INTERVAL_ACTIVE_SEC if has_active_alarms() else POLL_INTERVAL_NORMAL_SEC
            except Exception:
                logger.exception("Không xác định được có alarm active hay không -> dùng interval mặc định")
                interval = POLL_INTERVAL_NORMAL_SEC

            elapsed = time.time() - cycle_start
            sleep_time = max(0.0, interval - elapsed)
            until_report = (next_report_at - datetime.now(ZoneInfo(APP_TIMEZONE))).total_seconds()
            sleep_time = min(sleep_time, max(0.0, until_report))
            logger.debug("Chu kỳ mất %.2fs, ngủ %.2fs (interval=%ds)", elapsed, sleep_time, interval)
            stop_event.wait(sleep_time)
    finally:
        stop_outbox_worker()
        close_connection()
        logger.info("Alarm Collector đã dừng.")


if __name__ == "__main__":
    run_loop()
    sys.exit(0)
