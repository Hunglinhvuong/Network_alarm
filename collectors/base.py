"""
Interface chung cho mọi nguồn alarm. Muốn đổi nguồn (CSV -> Oracle -> API/SNMP...)
chỉ cần viết class mới kế thừa BaseAlarmCollector và implement fetch_active_alarms(),
KHÔNG cần sửa alarm_sync.py hay main_collector.py.
"""
from abc import ABC, abstractmethod


class AlarmRecord:
    """1 bản ghi alarm đang active tại thời điểm poll, theo chuẩn nội bộ."""

    __slots__ = ("device_code", "alarm_name", "raw_start_time")

    def __init__(self, device_code: str, alarm_name: str, raw_start_time=None):
        self.device_code = device_code
        self.alarm_name = alarm_name
        # raw_start_time: thời điểm bắt đầu do hệ nguồn báo (nếu có).
        # Có thể None -> sync layer sẽ dùng thời điểm phát hiện (now()) làm start_time.
        self.raw_start_time = raw_start_time

    def __repr__(self):
        return f"AlarmRecord(device_code={self.device_code!r}, alarm_name={self.alarm_name!r})"


class BaseAlarmCollector(ABC):
    @abstractmethod
    def fetch_active_alarms(self) -> list:
        """
        Trả về danh sách AlarmRecord đang ACTIVE tại thời điểm gọi (snapshot đầy đủ,
        không phải delta). Sync layer sẽ tự so sánh với DB để biết alarm nào mới,
        alarm nào đã cleared (không còn xuất hiện trong snapshot này).
        """
        raise NotImplementedError
