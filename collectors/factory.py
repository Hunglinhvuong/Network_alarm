"""
Factory: chọn collector theo config.ALARM_SOURCE ("csv" | "oracle" | "psql").
Đây là điểm DUY NHẤT cần sửa khi chuyển nguồn alarm thật -> chỉ đổi ENV
ALARM_SOURCE=oracle/psql + set biến nguồn tương ứng trong settings, không cần đổi code chỗ khác.
"""
from config.settings import (
    ALARM_SOURCE,
    CSV_COLLECTOR_CONFIG,
    ORACLE_COLLECTOR_CONFIG,
    PSQL_COLLECTOR_CONFIG,
)
from collectors.csv_collector import CSVAlarmCollector
from collectors.oracle_collector import OracleAlarmCollector
from collectors.psql_collector import PSQLAlarmCollector


def build_collector():
    if ALARM_SOURCE == "csv":
        return CSVAlarmCollector(file_path=CSV_COLLECTOR_CONFIG["file_path"])
    elif ALARM_SOURCE == "oracle":
        cfg = ORACLE_COLLECTOR_CONFIG
        return OracleAlarmCollector(
            host=cfg["host"],
            port=cfg["port"],
            service_name=cfg["service_name"],
            user=cfg["user"],
            password=cfg["password"],
            table=cfg["table"],
        )
    elif ALARM_SOURCE == "psql":
        cfg = PSQL_COLLECTOR_CONFIG
        return PSQLAlarmCollector(
            host=cfg["host"],
            port=cfg["port"],
            dbname=cfg["dbname"],
            user=cfg["user"],
            password=cfg["password"],
            table=cfg["table"],
        )
    else:
        raise ValueError(f"ALARM_SOURCE không hợp lệ: {ALARM_SOURCE!r} (chỉ hỗ trợ 'csv', 'oracle' hoặc 'psql')")
