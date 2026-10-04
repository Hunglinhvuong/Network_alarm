"""Collector đọc snapshot alarm active từ PostgreSQL nguồn."""
import logging
import re

from collectors.base import AlarmRecord, BaseAlarmCollector
from config.settings import PSQL_CONNECT_TIMEOUT_SEC, PSQL_QUERY_TIMEOUT_MS

logger = logging.getLogger(__name__)

ALARM_NAME_MAP = {
    "LOSS_COMM": "loss_comm",
    "LOSS_OF_COMMUNICATION": "loss_comm",
    "POWER_FAIL": "power_fail",
    "POWER_FAILURE": "power_fail",
}
VALID_TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


class PSQLAlarmCollector(BaseAlarmCollector):
    def __init__(self, host: str, port: int, dbname: str, user: str, password: str, table: str):
        if not VALID_TABLE_NAME.fullmatch(table):
            raise ValueError("PSQL_ALARM_TABLE chỉ được chứa tên bảng hoặc schema.tên_bảng hợp lệ")
        self.host = host
        self.port = port
        self.dbname = dbname
        self.user = user
        self.password = password
        self.table = table
        self._psycopg2 = None

    def _get_driver(self):
        if self._psycopg2 is None:
            import psycopg2
            self._psycopg2 = psycopg2
        return self._psycopg2

    def _connect(self):
        psycopg2 = self._get_driver()
        return psycopg2.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
            connect_timeout=PSQL_CONNECT_TIMEOUT_SEC,
            options=f"-c statement_timeout={PSQL_QUERY_TIMEOUT_MS}",
        )

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
            logger.warning(
                "Không map được alarm_name '%s' từ PostgreSQL -> bỏ qua bản ghi (device %s)",
                alarm_name_raw,
                device_code,
            )
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
                record = self._map_row(row)
                if record is not None:
                    records.append(record)
            logger.debug("PSQLAlarmCollector: đọc được %d alarm active", len(records))
            return records
        except Exception:
            logger.exception("Lỗi khi lấy alarm từ PostgreSQL; giữ nguyên trạng thái hiện tại và thử lại chu kỳ sau")
            raise
        finally:
            try:
                if cur is not None:
                    cur.close()
            finally:
                if conn is not None:
                    conn.close()