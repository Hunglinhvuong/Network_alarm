"""
Entry point cho Alarm Collector. Chạy: python main_collector.py

Vòng lặp: poll collector -> sync vào DB -> ngủ (interval adaptive: nhanh hơn khi
đang có alarm active, chậm lại khi hệ thống yên để tiết kiệm tài nguyên trên Wyse 5010).
"""
import logging
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from config.settings import LOG_LEVEL, POLL_INTERVAL_NORMAL_SEC, POLL_INTERVAL_ACTIVE_SEC
from collectors.factory import build_collector
from collectors.alarm_sync import sync_alarms, has_active_alarms
from alarm_pipeline import AlarmPipeline
from db.connection import close_connection


# Định nghĩa hàm lấy thời gian thực tế theo múi giờ Việt Nam
def vietnam_time_converter(*args):
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).timetuple()


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Ép toàn bộ hệ thống logging sử dụng múi giờ Việt Nam thay vì UTC của hệ điều hành
logging.Formatter.converter = vietnam_time_converter

logger = logging.getLogger("main_collector")

_running = True


def _handle_shutdown(signum, frame):
    global _running
    logger.info("Nhận tín hiệu dừng (%s) -> thoát vòng lặp an toàn", signum)
    _running = False


def run_loop():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    collector = build_collector()
    pipeline = AlarmPipeline()
    logger.info("Alarm Collector khởi động, nguồn: %s", collector.__class__.__name__)

    while _running:
        cycle_start = time.time()
        try:
            records = collector.fetch_active_alarms()
            sync_alarms(records)
            result = pipeline.run_cycle()
            if result["new_alerts"] or result["resolved_alerts"] or result["new_partial_alerts"] or result["resolved_partial_alerts"]:
                logger.info("Pipeline: %s", result)
        except Exception:
            logger.exception("Lỗi trong chu kỳ poll -> bỏ qua chu kỳ này, thử lại lần sau")

        try:
            interval = POLL_INTERVAL_ACTIVE_SEC if has_active_alarms() else POLL_INTERVAL_NORMAL_SEC
        except Exception:
            logger.exception("Không xác định được có alarm active hay không -> dùng interval mặc định")
            interval = POLL_INTERVAL_NORMAL_SEC

        elapsed = time.time() - cycle_start
        sleep_time = max(0.0, interval - elapsed)
        logger.debug("Chu kỳ mất %.2fs, ngủ %.2fs (interval=%ds)", elapsed, sleep_time, interval)

        # ngủ theo từng đoạn nhỏ để phản ứng nhanh với tín hiệu dừng
        slept = 0.0
        while slept < sleep_time and _running:
            step = min(1.0, sleep_time - slept)
            time.sleep(step)
            slept += step

    close_connection()
    logger.info("Alarm Collector đã dừng.")


if __name__ == "__main__":
    run_loop()
    sys.exit(0)
