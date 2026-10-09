"""Collector for active alarms from the production Oracle alarm log."""
import logging
import re
from collections import defaultdict

from collectors.base import AlarmRecord, BaseAlarmCollector
from config.settings import ORACLE_CALL_TIMEOUT_MS, ORACLE_CONNECT_TIMEOUT_SEC
from db.device_repo import get_device_num_cells

logger = logging.getLogger(__name__)

VALID_TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")
CELL_ALARM_NAMES = {
    "CELL UNAVAILABLE",
    "NR CELL UNAVAILABLE",
    "UMTS CELL UNAVAILABLE",
}
POWER_ALARM_TYPE = "POWER"
LOSS_ALARM_TYPE = "SERVICE"
NE_DISCONNECTED = "NE IS DISCONNECTED"
MINIMUM_AFFECTED_CELL_RATIO_NUMERATOR = 7
MINIMUM_AFFECTED_CELL_RATIO_DENOMINATOR = 10


class OracleAlarmCollector(BaseAlarmCollector):
    def __init__(self, host: str, port: int, service_name: str, user: str, password: str, table: str):
        if not VALID_TABLE_NAME.fullmatch(table):
            raise ValueError("ORACLE_ALARM_TABLE chỉ được chứa tên bảng hoặc schema.tên_bảng hợp lệ")
        self.host = host
        self.port = port
        self.service_name = service_name
        self.user = user
        self.password = password
        self.table = table
        self._oracledb = None

    def _get_driver(self):
        if self._oracledb is None:
            import oracledb
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
            SELECT rala.SITE, rala.CELLID, rala.SDATE, rala.EDATE,
                   rala.ALARM_TYPE, rala.SEVERITY, rala.ALARM_NAME,
                   rala.NETWORK, rala.PROVINCE
            FROM {self.table} rala
            WHERE rala.PROVINCE = 'Tỉnh Nghệ An'
              AND rala.EDATE IS NULL
              AND rala.SDATE >= TRUNC(SYSDATE) - 4
              AND rala.SDATE <= SYSDATE
              AND rala.ALARM_TYPE IN ('SERVICE', 'POWER')
              AND rala.ALARM_NAME IN (
                  'RF Unit DC Input Power Failure',
                  'Cell Unavailable',
                  'AC FAILED',
                  'NE Is Disconnected',
                  'NR Cell Unavailable',
                  'UMTS Cell Unavailable'
              )
              AND (rala.CELLID NOT LIKE 'VNP-4G%' OR rala.CELLID IS NULL)
        """

    @staticmethod
    def _device_code(site, network):
        site = str(site or "").strip()
        network = str(network or "").strip().upper()
        if not site:
            return None
        if network in {"3G", "RAN_4G"}:
            return site
        if network == "RAN_5G":
            return f"{site.split('_', 1)[0]}_5G"
        return None

    @classmethod
    def _cell_alarm_device_codes(cls, rows):
        codes = set()
        for row in rows:
            site, _cell_id, _start_time, _end_time, alarm_type, _severity, alarm_name, network, _province = row
            if (
                str(alarm_type or "").strip().upper() == LOSS_ALARM_TYPE
                and str(alarm_name or "").strip().upper() in CELL_ALARM_NAMES
            ):
                device_code = cls._device_code(site, network)
                if device_code:
                    codes.add(device_code)
        return codes

    @classmethod
    def _map_rows(cls, rows, num_cells) -> list:
        grouped = defaultdict(lambda: {
            "power_starts": [],
            "disconnected_starts": [],
            "cell_ids": set(),
            "cell_starts": [],
        })

        for row in rows:
            site, cell_id, start_time, _end_time, alarm_type, _severity, alarm_name, network, _province = row
            alarm_type = str(alarm_type or "").strip().upper()
            alarm_name = str(alarm_name or "").strip().upper()
            key = (str(site or "").strip(), str(network or "").strip().upper())
            alarm_group = grouped[key]

            if alarm_type == POWER_ALARM_TYPE:
                alarm_group["power_starts"].append(start_time)
            elif alarm_type == LOSS_ALARM_TYPE and alarm_name == NE_DISCONNECTED:
                alarm_group["disconnected_starts"].append(start_time)
            elif alarm_type == LOSS_ALARM_TYPE and alarm_name in CELL_ALARM_NAMES:
                if cell_id is not None:
                    cell_id = str(cell_id).strip()
                    if cell_id:
                        alarm_group["cell_ids"].add(cell_id)
                alarm_group["cell_starts"].append(start_time)

        power_starts_by_device = defaultdict(list)
        disconnected_starts_by_device = defaultdict(list)
        cell_starts_by_device = defaultdict(list)

        for (site, network), alarm_group in grouped.items():
            device_code = cls._device_code(site, network)
            if not device_code:
                logger.warning("Không map được Oracle NETWORK '%s' cho SITE '%s' -> bỏ qua", network, site)
                continue

            if alarm_group["power_starts"]:
                power_starts_by_device[device_code].extend(alarm_group["power_starts"])
            if alarm_group["disconnected_starts"]:
                disconnected_starts_by_device[device_code].extend(alarm_group["disconnected_starts"])
                continue

            cell_count = len(alarm_group["cell_ids"])
            num_cell = num_cells.get(device_code)
            if alarm_group["cell_starts"] and num_cell is None:
                logger.warning(
                    "Không có device.num_cell cho %s; không thể áp ngưỡng cảnh báo mất liên lạc theo cell",
                    device_code,
                )
                continue
            if (
                cell_count > 0
                and num_cell is not None
                and MINIMUM_AFFECTED_CELL_RATIO_DENOMINATOR * cell_count
                >= MINIMUM_AFFECTED_CELL_RATIO_NUMERATOR * num_cell
            ):
                cell_starts_by_device[device_code].extend(alarm_group["cell_starts"])

        records = []
        for device_code, starts in power_starts_by_device.items():
            records.append(AlarmRecord(device_code, "power_fail", min((s for s in starts if s is not None), default=None)))
        for device_code in disconnected_starts_by_device.keys() | cell_starts_by_device.keys():
            starts = disconnected_starts_by_device.get(device_code) or cell_starts_by_device[device_code]
            records.append(AlarmRecord(device_code, "loss_comm", min((s for s in starts if s is not None), default=None)))
        return records

    def fetch_active_alarms(self) -> list:
        conn = None
        cur = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(self._build_query())
            rows = list(cur)
            cell_device_codes = self._cell_alarm_device_codes(rows)
            num_cells = get_device_num_cells(cell_device_codes)
            records = self._map_rows(rows, num_cells)
            logger.debug("OracleAlarmCollector: đọc được %d alarm active", len(records))
            return records
        except Exception:
            logger.exception("Lỗi khi lấy alarm từ Oracle hoặc tra cứu num_cell; giữ nguyên trạng thái hiện tại và thử lại chu kỳ sau")
            raise
        finally:
            try:
                if cur is not None:
                    cur.close()
            finally:
                if conn is not None:
                    conn.close()
