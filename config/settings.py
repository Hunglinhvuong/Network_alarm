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

# ---------- Timezone ----------
# Múi giờ cho toàn bộ hệ thống: Database session, Logging, Alert messages
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Ho_Chi_Minh")

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
# "csv" (test nội bộ) | "oracle" | "psql" (PostgreSQL nguồn)
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
    "table": os.environ.get("ORACLE_ALARM_TABLE", "soca.R_ALARM_LOG_ACTIVE"),
}
ORACLE_CONNECT_TIMEOUT_SEC = float(os.environ.get("ORACLE_CONNECT_TIMEOUT_SEC", 5))
ORACLE_CALL_TIMEOUT_MS = int(os.environ.get("ORACLE_CALL_TIMEOUT_MS", 10000))
if min(ORACLE_CONNECT_TIMEOUT_SEC, ORACLE_CALL_TIMEOUT_MS) <= 0:
    raise ValueError("Oracle connect/call timeout phải lớn hơn 0")

PSQL_COLLECTOR_CONFIG = {
    "host": os.environ.get("PSQL_HOST", ""),
    "port": int(os.environ.get("PSQL_PORT", 5432)),
    "dbname": os.environ.get("PSQL_DBNAME", ""),
    "user": os.environ.get("PSQL_USER", ""),
    "password": os.environ.get("PSQL_PASSWORD", ""),
    # Bảng nguồn cần có DEVICE_CODE, ALARM_NAME, START_TIME, END_TIME.
    "table": os.environ.get("PSQL_ALARM_TABLE", "ALARM_ACTIVE"),
}
PSQL_CONNECT_TIMEOUT_SEC = int(os.environ.get("PSQL_CONNECT_TIMEOUT_SEC", 5))
PSQL_QUERY_TIMEOUT_MS = int(os.environ.get("PSQL_QUERY_TIMEOUT_MS", 10000))
if min(PSQL_CONNECT_TIMEOUT_SEC, PSQL_QUERY_TIMEOUT_MS) <= 0:
    raise ValueError("PSQL connect/query timeout phải lớn hơn 0")

# ---------- Polling ----------
POLL_INTERVAL_NORMAL_SEC = int(os.environ.get("POLL_INTERVAL_NORMAL_SEC", 300))   # 5 phút, khi hệ thống yên
POLL_INTERVAL_ACTIVE_SEC = int(os.environ.get("POLL_INTERVAL_ACTIVE_SEC", 120))    # 2 phút, khi đang có alarm active
PERIODIC_REPORT_INTERVAL_MINUTES = int(os.environ.get("PERIODIC_REPORT_INTERVAL_MINUTES", 30))
PERIODIC_REPORT_START_TIME = os.environ.get("PERIODIC_REPORT_START_TIME", "08:00")
if min(POLL_INTERVAL_NORMAL_SEC, POLL_INTERVAL_ACTIVE_SEC, PERIODIC_REPORT_INTERVAL_MINUTES) <= 0:
    raise ValueError("Polling intervals và periodic report interval phải lớn hơn 0")

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
TELEGRAM_OUTBOX_POLL_INTERVAL_SEC = float(os.environ.get("TELEGRAM_OUTBOX_POLL_INTERVAL_SEC", 1))
TELEGRAM_SEND_INTERVAL_SEC = float(os.environ.get("TELEGRAM_SEND_INTERVAL_SEC", 1))
TELEGRAM_GROUP_SEND_INTERVAL_SEC = float(os.environ.get("TELEGRAM_GROUP_SEND_INTERVAL_SEC", 3))
TELEGRAM_GLOBAL_SEND_INTERVAL_SEC = float(os.environ.get("TELEGRAM_GLOBAL_SEND_INTERVAL_SEC", 0.05))
if min(
    TELEGRAM_OUTBOX_POLL_INTERVAL_SEC,
    TELEGRAM_SEND_INTERVAL_SEC,
    TELEGRAM_GROUP_SEND_INTERVAL_SEC,
    TELEGRAM_GLOBAL_SEND_INTERVAL_SEC,
) <= 0:
    raise ValueError("Các cấu hình tốc độ gửi Telegram phải lớn hơn 0")
TELEGRAM_OUTBOX_SENT_RETENTION_DAYS = int(os.environ.get("TELEGRAM_OUTBOX_SENT_RETENTION_DAYS", 30))
TELEGRAM_OUTBOX_FAILED_RETENTION_DAYS = int(os.environ.get("TELEGRAM_OUTBOX_FAILED_RETENTION_DAYS", 90))
ALARM_EVENT_RETENTION_DAYS = int(os.environ.get("ALARM_EVENT_RETENTION_DAYS", 180))
DATA_CLEANUP_BATCH_SIZE = int(os.environ.get("DATA_CLEANUP_BATCH_SIZE", 5000))
if min(
    TELEGRAM_OUTBOX_SENT_RETENTION_DAYS,
    TELEGRAM_OUTBOX_FAILED_RETENTION_DAYS,
    ALARM_EVENT_RETENTION_DAYS,
    DATA_CLEANUP_BATCH_SIZE,
) <= 0:
    raise ValueError("Retention days và DATA_CLEANUP_BATCH_SIZE phải lớn hơn 0")
# User được phép dùng các lệnh tra cứu/quản trị trên bot (whitelist theo Telegram user_id)
TELEGRAM_ADMIN_IDS = _parse_id_list(os.environ.get("TELEGRAM_ADMIN_IDS", ""))
# Khoảng nghỉ (giây) giữa các lần poll getUpdates của bot tra cứu
TELEGRAM_BOT_POLL_INTERVAL_SEC = int(os.environ.get("TELEGRAM_BOT_POLL_INTERVAL_SEC", 2))

# ---------- Logging ----------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
