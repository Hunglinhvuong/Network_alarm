"""
Cấu hình tập trung cho toàn hệ thống.
Ưu tiên đọc từ biến môi trường (ENV) — fallback về giá trị mặc định khi chạy local/test.
Không hardcode secret thật trong file này khi lên production; dùng .env + os.environ.
"""
import os

from dotenv import load_dotenv

# Nạp file .env ở thư mục gốc dự án (nếu có) trước khi đọc os.environ.
# override=False: biến ENV đã export sẵn trong shell/systemd luôn được ưu tiên hơn .env.
load_dotenv(override=False)

# ---------- Database (PostgreSQL) ----------
DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": int(os.environ.get("PG_PORT", 5432)),
    "dbname": os.environ.get("PG_DBNAME", "network_alarm"),
    "user": os.environ.get("PG_USER", "postgres"),
    "password": os.environ.get("PG_PASSWORD", "postgres"),
    # Wyse 5010 yếu RAM -> giới hạn connection, không dùng pool lớn
    "connect_timeout": 5,
}

# ---------- Alarm Collector ----------
# "csv" (test nội bộ hiện tại) | "oracle" (hệ thống thật sau này)
ALARM_SOURCE = os.environ.get("ALARM_SOURCE", "csv")

CSV_COLLECTOR_CONFIG = {
    # File CSV mô phỏng snapshot "các alarm đang active tại thời điểm poll"
    "file_path": os.environ.get("ALARM_CSV_PATH", "sample_data/alarms.csv"),
}

ORACLE_COLLECTOR_CONFIG = {
    "host": os.environ.get("ORACLE_HOST", ""),
    "port": int(os.environ.get("ORACLE_PORT", 1521)),
    "service_name": os.environ.get("ORACLE_SERVICE", ""),
    "user": os.environ.get("ORACLE_USER", ""),
    "password": os.environ.get("ORACLE_PASSWORD", ""),
    # Giả định tên bảng/cột phía Oracle — CẦN xác nhận lại khi có schema thật.
    # Xem ghi chú TODO trong collectors/oracle_collector.py
    "table": os.environ.get("ORACLE_ALARM_TABLE", "ALARM_ACTIVE"),
}

# ---------- Polling ----------
POLL_INTERVAL_NORMAL_SEC = int(os.environ.get("POLL_INTERVAL_NORMAL_SEC", 300))   # 5 phút, khi hệ thống yên
POLL_INTERVAL_ACTIVE_SEC = int(os.environ.get("POLL_INTERVAL_ACTIVE_SEC", 60))    # 1 phút, khi đang có alarm active

# ---------- RCA / nghiệp vụ (tham số hoá, chưa có bảng config trong DB) ----------
NODE_SYNC_WINDOW_MINUTES = int(os.environ.get("NODE_SYNC_WINDOW_MINUTES", 10))      # bước 1: đồng bộ cùng node cha
POWER_FAIL_LOOKBACK_HOURS = int(os.environ.get("POWER_FAIL_LOOKBACK_HOURS", 12))    # bước 2: mất điện gần đây
POWER_FAIL_CORRELATION_HOURS = int(os.environ.get("POWER_FAIL_CORRELATION_HOURS", 6))  # power_fail phải xảy ra trước loss_comm <= 6h
NEIGHBOR_DISTANCE_KM = float(os.environ.get("NEIGHBOR_DISTANCE_KM", 7.0))           # bước 3: bán kính neighbor_edge

ALARM_NAME_LOSS_COMM = "loss_comm"
ALARM_NAME_POWER_FAIL = "power_fail"

NODE_TYPE_STATION = "STATION"
NODE_TYPE_TRANS_NODE = "TRANS_NODE"

# ---------- Telegram ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

def _parse_id_list(raw: str) -> list:
    return [x.strip() for x in raw.split(",") if x.strip()]

# Chat/group nhận cảnh báo tự động (push alert) — có thể nhiều chat cùng lúc
TELEGRAM_ALERT_CHAT_IDS = _parse_id_list(os.environ.get("TELEGRAM_ALERT_CHAT_IDS", ""))
# User được phép dùng các lệnh tra cứu/quản trị trên bot (whitelist theo Telegram user_id)
TELEGRAM_ADMIN_IDS = _parse_id_list(os.environ.get("TELEGRAM_ADMIN_IDS", ""))
# Khoảng nghỉ (giây) giữa các lần poll getUpdates của bot tra cứu
TELEGRAM_BOT_POLL_INTERVAL_SEC = int(os.environ.get("TELEGRAM_BOT_POLL_INTERVAL_SEC", 2))

# ---------- Logging ----------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
