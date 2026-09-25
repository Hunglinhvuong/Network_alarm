"""
Collector đọc snapshot alarm active từ file CSV local — dùng để test toàn bộ pipeline
(sync, RCA, alerting) khi chưa có nguồn alarm thật.

Format CSV: device_code,alarm_name,start_time
  - device_code: khớp device.device_code trong DB
  - alarm_name: 'loss_comm' | 'power_fail'
  - start_time: ISO format (YYYY-MM-DD HH:MM:SS), có thể để trống -> dùng now()

Mỗi lần fetch_active_alarms() được gọi, file sẽ được đọc lại từ đầu -> để test
"clear alarm" chỉ cần xoá dòng tương ứng khỏi CSV rồi để collector poll lại.
"""
import csv
import logging
import os
from datetime import datetime

from collectors.base import AlarmRecord, BaseAlarmCollector

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"device_code", "alarm_name"}


class CSVAlarmCollector(BaseAlarmCollector):
    def __init__(self, file_path: str):
        self.file_path = file_path

    def fetch_active_alarms(self) -> list:
        if not os.path.exists(self.file_path):
            logger.warning("File CSV alarm không tồn tại: %s -> coi như không có alarm nào active", self.file_path)
            return []

        records = []
        with open(self.file_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"File CSV alarm thiếu cột bắt buộc: {missing}")

            for row_num, row in enumerate(reader, start=2):
                device_code = (row.get("device_code") or "").strip()
                alarm_name = (row.get("alarm_name") or "").strip()
                if not device_code or not alarm_name:
                    logger.warning("Bỏ qua dòng %d: thiếu device_code/alarm_name", row_num)
                    continue

                raw_start_time = None
                start_time_str = (row.get("start_time") or "").strip()
                if start_time_str:
                    try:
                        raw_start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        logger.warning(
                            "Dòng %d: start_time '%s' sai định dạng (cần YYYY-MM-DD HH:MM:SS) -> bỏ qua, dùng now()",
                            row_num, start_time_str,
                        )

                records.append(AlarmRecord(device_code, alarm_name, raw_start_time))

        logger.debug("CSVAlarmCollector: đọc được %d alarm active từ %s", len(records), self.file_path)
        return records
