import unittest
from unittest.mock import patch

from escalation.engine import compute_escalation
from escalation.station_status import get_station_full_statuses
from topology.descendants import get_descendant_stations_many
from topology.path_to_root import get_paths_to_root


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *_args):
        return False


class BatchQueryTests(unittest.TestCase):
    def test_paths_are_grouped_by_requested_source_with_one_query(self):
        rows = [
            {"source_node_id": 10, "node_id": 10, "node_code": "S10", "node_name": "Station 10", "node_type": "STATION", "depth": 0},
            {"source_node_id": 10, "node_id": 1, "node_code": "N1", "node_name": "Root", "node_type": "TRANS_NODE", "depth": 1},
            {"source_node_id": 20, "node_id": 20, "node_code": "S20", "node_name": "Station 20", "node_type": "STATION", "depth": 0},
        ]
        cursor = _Cursor(rows)

        with patch("topology.path_to_root.get_cursor", return_value=_CursorContext(cursor)):
            paths = get_paths_to_root([10, 20])

        self.assertEqual(len(cursor.executed), 1)
        self.assertEqual([node["node_id"] for node in paths[10]], [10, 1])
        self.assertEqual(paths[20][0]["node_code"], "S20")
        self.assertNotIn("source_node_id", paths[10][0])

    def test_descendants_are_grouped_by_root_with_one_query(self):
        cursor = _Cursor(
            [
                {"root_node_id": 1, "node_id": 1, "node_type": "TRANS_NODE"},
                {"root_node_id": 1, "node_id": 10, "node_type": "STATION"},
                {"root_node_id": 2, "node_id": 2, "node_type": "TRANS_NODE"},
                {"root_node_id": 2, "node_id": 20, "node_type": "STATION"},
            ]
        )

        with patch("topology.descendants.get_cursor", return_value=_CursorContext(cursor)):
            descendants = get_descendant_stations_many({1, 2})

        self.assertEqual(len(cursor.executed), 1)
        self.assertEqual(descendants, {1: {10}, 2: {20}})

    def test_station_statuses_are_assembled_from_one_query(self):
        cursor = _Cursor(
            [
                {"site_id": 10, "site_code": "S10", "site_name": "One", "device_id": 101, "device_code": "D101", "device_type": "4G", "loss_comm_start": "2026-09-30T00:00:00Z", "power_fail_active": False},
                {"site_id": 10, "site_code": "S10", "site_name": "One", "device_id": 102, "device_code": "D102", "device_type": "5G", "loss_comm_start": None, "power_fail_active": True},
            ]
        )

        with patch("escalation.station_status.get_cursor", return_value=_CursorContext(cursor)):
            statuses = get_station_full_statuses([10])

        self.assertEqual(len(cursor.executed), 1)
        self.assertEqual(statuses[10]["comm_status"], "partial_down")
        self.assertTrue(statuses[10]["power_affected"])
        self.assertEqual(statuses[10]["affected_device_types"], ["4G"])

    def test_escalation_reuses_preloaded_status_and_batches_topology(self):
        affected = {
            10: {"node_id": 10, "site_code": "S10", "site_name": "One", "alarm_ids": {1}},
            20: {"node_id": 20, "site_code": "S20", "site_name": "Two", "alarm_ids": {2}},
        }
        path_rows = {
            10: [
                {"node_id": 10, "node_code": "S10", "node_name": "One", "node_type": "STATION", "depth": 0},
                {"node_id": 1, "node_code": "N1", "node_name": "Root", "node_type": "TRANS_NODE", "depth": 1},
            ],
            20: [
                {"node_id": 20, "node_code": "S20", "node_name": "Two", "node_type": "STATION", "depth": 0},
                {"node_id": 1, "node_code": "N1", "node_name": "Root", "node_type": "TRANS_NODE", "depth": 1},
            ],
        }

        with (
            patch("escalation.engine.get_paths_to_root", return_value=path_rows) as get_paths,
            patch("escalation.engine.get_descendant_stations_many", return_value={1: {10, 20}}) as get_descendants,
        ):
            groups = compute_escalation(affected)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].node_id, 1)
        self.assertEqual(groups[0].station_site_ids, {10, 20})
        get_paths.assert_called_once_with([10, 20])
        get_descendants.assert_called_once_with({1})


if __name__ == "__main__":
    unittest.main()
