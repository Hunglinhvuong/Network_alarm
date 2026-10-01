import unittest
from unittest.mock import Mock, patch

from collectors.oracle_collector import OracleAlarmCollector


class OracleCollectorTests(unittest.TestCase):
    def setUp(self):
        self.collector = OracleAlarmCollector(
            host="oracle.example",
            port=1521,
            service_name="network",
            user="alarm_reader",
            password="not-used",
            table="ALARM_ACTIVE",
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
            password="not-used",
            dsn="dsn",
            params=driver.ConnectParams.return_value,
        )
        self.assertIs(result, connection)
        self.assertEqual(connection.call_timeout, 25000)

    def test_fetch_closes_cursor_and_connection(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.__iter__ = Mock(return_value=iter([("D1", "LOSS_COMM", None)]))
        self.collector._connect = Mock(return_value=connection)

        records = self.collector.fetch_active_alarms()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].device_code, "D1")
        self.assertEqual(records[0].alarm_name, "loss_comm")
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
