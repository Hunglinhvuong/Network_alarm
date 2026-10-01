"""
Collector đọc snapshot alarm active từ Oracle DB (hệ thống ngoài).
Dùng thư viện `oracledb` (python-oracledb) ở chế độ "thin" -> KHÔNG cần cài Oracle
Instant Client, phù hợp máy yếu (Wyse 5010).

GIẢ ĐỊNH SCHEMA PHÍA ORACLE (chưa xác nhận với user — cần chỉnh lại khi có info thật):
    Bảng ORACLE_ALARM_TABLE (mặc định "ALARM_ACTIVE") có tối thiểu các cột:
        DEVICE_CODE   VARCHAR2   -- khớp device.device_code bên Postgres
        ALARM_NAME    VARCHAR2   -- map được sang 'loss_comm' | 'power_fail'
        START_TIME    DATE/TIMESTAMP (có thể NULL)
    Chỉ SELECT các bản ghi đang active (không có cột end_time / hoặc có cờ status active).

Nếu schema thật khác, chỉ cần sửa hàm _build_query() và _map_row() bên dưới,
phần còn lại (sync, RCA, main loop) không bị ảnh hưởng.
"""
import logging

from collectors.base import AlarmRecord, BaseAlarmCollector
from config.settings import ORACLE_CALL_TIMEOUT_MS, ORACLE_CONNECT_TIMEOUT_SEC

logger = logging.getLogger(__name__)

# Map tên alarm phía Oracle -> tên chuẩn nội bộ. Cập nhật khi biết naming thật.
ALARM_NAME_MAP = {
    "LOSS_COMM": "loss_comm",
    "LOSS_OF_COMMUNICATION": "loss_comm",
    "POWER_FAIL": "power_fail",
    "POWER_FAILURE": "power_fail",
}


class OracleAlarmCollector(BaseAlarmCollector):
    def __init__(self, host: str, port: int, service_name: str, user: str, password: str, table: str):
        self.host = host
        self.port = port
        self.service_name = service_name
        self.user = user
        self.password = password
        self.table = table
        self._oracledb = None  # lazy import để CSV mode không bắt buộc cài oracledb

    def _get_driver(self):
        if self._oracledb is None:
            import oracledb  # cài: pip install oracledb
            oracledb.init_oracle_client() if False else None  # thin mode: KHÔNG gọi init_oracle_client()
            self._oracledb = oracledb
        return self._oracledb

    def _connect(self):
        oracledb = self._get_driver()
        dsn = oracledb.makedsn(self.host, self.port, service_name=self.service_name)
        params = oracledb.ConnectParams(tcp_connect_timeout=ORACLE_CONNECT_TIMEOUT_SEC)
        connection = oracledb.connect(
            user=self.user,
            password=self.password,
            dsn=dsn,
            params=params,
        )
        connection.call_timeout = ORACLE_CALL_TIMEOUT_MS
        return connection

    def _build_query(self) -> str:
        return f"""
            SELECT DEVICE_CODE, ALARM_NAME, START_TIME
            FROM {self.table}
            WHERE END_TIME IS NULL
        """

    def _map_row(self, row) -> AlarmRecord:
        device_code, alarm_name_raw, start_time = row
        alarm_name = ALARM_NAME_MAP.get((alarm_name_raw or "").strip().upper())
        if alarm_name is None:
            logger.warning("Không map được alarm_name '%s' từ Oracle -> bỏ qua bản ghi (device %s)", alarm_name_raw, device_code)
            return None
        return AlarmRecord(str(device_code).strip(), alarm_name, start_time)

    def fetch_active_alarms(self) -> list:
        conn = None
        cur = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(self._build_query())
            records = []
            for row in cur:
                rec = self._map_row(row)
                if rec is not None:
                    records.append(rec)
            logger.debug("OracleAlarmCollector: đọc được %d alarm active", len(records))
            return records
        except Exception:
            logger.exception("Lỗi khi lấy alarm từ Oracle; giữ nguyên trạng thái hiện tại và thử lại chu kỳ sau")
            # QUAN TRỌNG: không được trả [] khi lỗi kết nối, vì sync layer sẽ hiểu nhầm
            # là "tất cả alarm đã cleared". Ném lỗi lên để main loop giữ nguyên trạng thái active.
            raise
        finally:
            try:
                if cur is not None:
                    cur.close()
            finally:
                if conn is not None:
                    conn.close()
