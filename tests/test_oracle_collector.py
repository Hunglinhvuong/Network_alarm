import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from collectors.oracle_collector import OracleAlarmCollector


class OracleCollectorTests(unittest.TestCase):
    def setUp(self):
        self.collector = OracleAlarmCollector(
            host="oracle.example",
            port=1521,
            service_name="network",
            user="alarm_reader",
            password="secret",
            table="soca.R_ALARM_LOG_ACTIVE",
        )

    def test_connect_sets_bounded_tcp_and_call_timeouts(self):
        driver = Mock()
        driver.makedsn.return_value = "dsn"
        connection = Mock()
        driver.connect.return_value = connection
        self.collector._oracledb = driver

        with (
            patch("collectors.oracle_collector.ORACLE_CONNECT_TIMEOUT_SEC", 4),
            patch("collectors.oracle_collector.ORACLE_CALL_TIMEOUT_MS", 25000),
        ):
            result = self.collector._connect()

        driver.ConnectParams.assert_called_once_with(tcp_connect_timeout=4)
        driver.connect.assert_called_once_with(
            user="alarm_reader",
            password="secret",
            dsn="dsn",
            params=driver.ConnectParams.return_value,
        )
        self.assertIs(result, connection)
        self.assertEqual(connection.call_timeout, 25000)

    def test_build_query_uses_production_active_alarm_filters(self):
        query = self.collector._build_query()

        self.assertIn("FROM soca.R_ALARM_LOG_ACTIVE rala", query)
        self.assertIn("rala.PROVINCE = 'Tỉnh Nghệ An'", query)
        self.assertIn("rala.EDATE IS NULL", query)
        self.assertIn("rala.SDATE >= TRUNC(SYSDATE) - 7", query)
        self.assertIn("rala.SDATE <= SYSDATE", query)
        self.assertIn("rala.ALARM_TYPE IN ('SERVICE', 'POWER')", query)
        self.assertIn("'NE Is Disconnected'", query)
        self.assertIn("'NR Cell Unavailable'", query)

    def test_rejects_unsafe_table_name(self):
        with self.assertRaisesRegex(ValueError, "ORACLE_ALARM_TABLE"):
            OracleAlarmCollector("", 1521, "", "", "", "soca.R_ALARM_LOG_ACTIVE; DROP TABLE device")

    def test_maps_power_loss_priority_cell_threshold_and_network_codes(self):
        s1_time = datetime(2026, 10, 8, 10)
        cell_time = datetime(2026, 10, 8, 11)
        rows = [
            ("SITE1", None, s1_time, None, "SERVICE", "MAJOR", "NE Is Disconnected", "RAN_4G", "Tỉnh Nghệ An"),
            ("SITE1", "A", cell_time, None, "SERVICE", "MAJOR", "Cell Unavailable", "RAN_4G", "Tỉnh Nghệ An"),
            ("SITE1", "B", cell_time, None, "SERVICE", "MAJOR", "NR Cell Unavailable", "RAN_4G", "Tỉnh Nghệ An"),
            ("NANTKY01_LN", None, s1_time, None, "POWER", "MAJOR", "AC FAILED", "RAN_5G", "Tỉnh Nghệ An"),
            ("NANTKY02", "A", cell_time, None, "SERVICE", "MAJOR", "UMTS Cell Unavailable", "3G", "Tỉnh Nghệ An"),
            ("NANTKY02", "A", cell_time, None, "SERVICE", "MAJOR", "Cell Unavailable", "3G", "Tỉnh Nghệ An"),
        ]

        records = self.collector._map_rows(rows, {"NANTKY02": 3})

        records_by_device_alarm = {(record.device_code, record.alarm_name): record for record in records}
        self.assertEqual(set(records_by_device_alarm), {
            ("SITE1", "loss_comm"),
            ("NANTKY01_5G", "power_fail"),
        })
        self.assertEqual(records_by_device_alarm[("SITE1", "loss_comm")].raw_start_time, s1_time)
        self.assertEqual(records_by_device_alarm[("NANTKY01_5G", "power_fail")].raw_start_time, s1_time)

    def test_cell_loss_requires_two_thirds_of_distinct_cells(self):
        rows = [
            ("SITE3", "A", datetime(2026, 10, 8, 10), None, "SERVICE", "MAJOR", "Cell Unavailable", "RAN_4G", "Tỉnh Nghệ An"),
            ("SITE3", "A", datetime(2026, 10, 8, 10), None, "SERVICE", "MAJOR", "Cell Unavailable", "RAN_4G", "Tỉnh Nghệ An"),
            ("SITE3", "B", datetime(2026, 10, 8, 11), None, "SERVICE", "MAJOR", "NR Cell Unavailable", "RAN_4G", "Tỉnh Nghệ An"),
        ]

        cell_codes = self.collector._cell_alarm_device_codes(rows)
        records = self.collector._map_rows(rows, {"SITE3": 3})

        self.assertEqual(cell_codes, {"SITE3"})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].device_code, "SITE3")
        self.assertEqual(records[0].alarm_name, "loss_comm")
        self.assertEqual(records[0].raw_start_time, datetime(2026, 10, 8, 10))

        below_threshold = self.collector._map_rows(rows[:2], {"SITE3": 3})
        self.assertEqual(below_threshold, [])

    def test_fetch_looks_up_cell_counts_and_closes_resources(self):
        rows = [
            ("SITE1", "A", datetime(2026, 10, 8, 10), None, "SERVICE", "MAJOR", "Cell Unavailable", "RAN_4G", "Tỉnh Nghệ An"),
            ("SITE1", "B", datetime(2026, 10, 8, 11), None, "SERVICE", "MAJOR", "Cell Unavailable", "RAN_4G", "Tỉnh Nghệ An"),
        ]
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.__iter__ = Mock(return_value=iter(rows))
        self.collector._connect = Mock(return_value=connection)

        with patch(
            "collectors.oracle_collector.get_device_num_cells",
            return_value={"SITE1": 3},
        ) as get_num_cells:
            records = self.collector.fetch_active_alarms()

        get_num_cells.assert_called_once_with({"SITE1"})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].alarm_name, "loss_comm")
        self.assertEqual(records[0].raw_start_time, datetime(2026, 10, 8, 10))
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_fetch_closes_resources_when_query_fails(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.execute.side_effect = RuntimeError("query failed")
        self.collector._connect = Mock(return_value=connection)

        with self.assertRaisesRegex(RuntimeError, "query failed"):
            self.collector.fetch_active_alarms()

        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
