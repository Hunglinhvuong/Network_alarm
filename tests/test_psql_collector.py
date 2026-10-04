import unittest
from unittest.mock import Mock, patch

from collectors import factory
from collectors.psql_collector import PSQLAlarmCollector


class PSQLCollectorTests(unittest.TestCase):
    def setUp(self):
        self.collector = PSQLAlarmCollector(
            host="source.example",
            port=5432,
            dbname="alarms",
            user="alarm_reader",
            password="not-used",
            table="public.ALARM_ACTIVE",
        )

    def test_connect_sets_bounded_timeouts(self):
        driver = Mock()
        connection = Mock()
        driver.connect.return_value = connection
        self.collector._psycopg2 = driver

        with (
            patch("collectors.psql_collector.PSQL_CONNECT_TIMEOUT_SEC", 4),
            patch("collectors.psql_collector.PSQL_QUERY_TIMEOUT_MS", 25000),
        ):
            result = self.collector._connect()

        driver.connect.assert_called_once_with(
            host="source.example",
            port=5432,
            dbname="alarms",
            user="alarm_reader",
            password="not-used",
            connect_timeout=4,
            options="-c statement_timeout=25000",
        )
        self.assertIs(result, connection)

    def test_fetch_maps_active_rows_and_closes_resources(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.__iter__ = Mock(return_value=iter([("D1", "LOSS_COMM", None)]))
        self.collector._connect = Mock(return_value=connection)

        records = self.collector.fetch_active_alarms()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].device_code, "D1")
        self.assertEqual(records[0].alarm_name, "loss_comm")
        cursor.execute.assert_called_once_with(self.collector._build_query())
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

    def test_rejects_invalid_table_identifier(self):
        with self.assertRaisesRegex(ValueError, "PSQL_ALARM_TABLE"):
            PSQLAlarmCollector("host", 5432, "db", "user", "password", "alarms; DROP TABLE device")

    def test_factory_builds_psql_collector(self):
        config = {
            "host": "source.example",
            "port": 5432,
            "dbname": "alarms",
            "user": "alarm_reader",
            "password": "not-used",
            "table": "public.ALARM_ACTIVE",
        }
        with (
            patch.object(factory, "ALARM_SOURCE", "psql"),
            patch.object(factory, "PSQL_COLLECTOR_CONFIG", config),
        ):
            collector = factory.build_collector()

        self.assertIsInstance(collector, PSQLAlarmCollector)
        self.assertEqual(collector.host, "source.example")


if __name__ == "__main__":
    unittest.main()