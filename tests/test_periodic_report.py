import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from alerting.periodic_report import format_periodic_report, next_report_time


class PeriodicReportTests(unittest.TestCase):
    def test_report_counts_stations_and_omits_parent_covered_stations(self):
        parent = SimpleNamespace(
            node_type="TRANS_NODE",
            node_name="Node cha",
            node_code="N-1",
            station_site_ids={1, 2},
        )
        single_station_group = SimpleNamespace(
            node_type="TRANS_NODE",
            node_name="Node nhánh đơn",
            node_code="N-2",
            station_site_ids={4},
        )
        breakdown = {
            1: {
                "site_code": "S-1",
                "site_name": "Trạm 1",
                "all_down": True,
                "down_devices": [{"device_type": "3G"}],
            },
            2: {
                "site_code": "S-2",
                "site_name": "Trạm 2",
                "all_down": True,
                "down_devices": [{"device_type": "4G"}],
            },
            3: {
                "site_code": "S-3",
                "site_name": "Trạm 3",
                "all_down": False,
                "down_devices": [{"device_type": "5G"}],
            },
            4: {
                "site_code": "S-4",
                "site_name": "Trạm 4",
                "all_down": True,
                "down_devices": [{"device_type": "4G"}],
            },
        }

        report = format_periodic_report(
            [parent, single_station_group], breakdown, datetime(2026, 9, 27, 8, 0)
        )

        self.assertIn("Báo cáo tổng hợp định kỳ vào lúc 08:00:", report)
        self.assertIn("Tổng số trạm đang có thiết bị mất liên lạc: 4 (full-down: 3, partial-down: 1)", report)
        self.assertIn("Node cha (N-1): 2 trạm ảnh hưởng.", report)
        self.assertNotIn("Node nhánh đơn (N-2)", report)
        self.assertIn("Trạm 3 (S-3) [partial-down]: 5G", report)
        self.assertIn("Trạm 4 (S-4) [full-down]: 4G", report)
        self.assertNotIn("Trạm 1 (S-1)", report)
        self.assertNotIn("Trạm 2 (S-2)", report)

    def test_next_report_time_uses_start_time_and_interval(self):
        timezone = ZoneInfo("Asia/Ho_Chi_Minh")
        now = datetime(2026, 9, 28, 10, 15, tzinfo=timezone)

        next_time = next_report_time(now, "08:00", 420)

        self.assertEqual(next_time, datetime(2026, 9, 28, 16, 0, tzinfo=timezone))

    def test_next_report_time_rejects_nonpositive_interval(self):
        with self.assertRaises(ValueError):
            next_report_time(datetime(2026, 9, 27, 7), "08:00", 0)


if __name__ == "__main__":
    unittest.main()