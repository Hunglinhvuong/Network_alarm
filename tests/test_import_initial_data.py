import unittest
from unittest.mock import patch

from inputdata.import_initial_data import import_devices


class _Cursor:
    def execute(self, _sql):
        pass

    def fetchall(self):
        return [("S1", 1)]


class ImportInitialDataTests(unittest.TestCase):
    def test_import_devices_reads_optional_num_cell_values(self):
        rows = [
            {
                "device_code": "D1",
                "device_name": "Device 1",
                "site_code": "S1",
                "type": "4G",
                "NumCell": "3",
            },
            {
                "device_code": "D2",
                "device_name": "Device 2",
                "site_code": "S1",
                "type": "5G",
                "num_cell": "",
            },
            {
                "device_code": "D3",
                "device_name": "Device 3",
                "site_code": "S1",
                "type": "3G",
            },
        ]

        with patch("inputdata.import_initial_data.execute_values") as execute_values:
            import_devices(_Cursor(), rows)

        self.assertEqual(
            execute_values.call_args.args[2],
            [
                ("D1", "Device 1", 1, "4G", 3),
                ("D2", "Device 2", 1, "5G", None),
                ("D3", "Device 3", 1, "3G", None),
            ],
        )
        self.assertIn("num_cell", execute_values.call_args.args[1])

    def test_import_devices_rejects_invalid_num_cell(self):
        row = {
            "device_code": "D1",
            "device_name": "Device 1",
            "site_code": "S1",
            "type": "4G",
        }

        for value, error in (("-1", "không được âm"), ("unknown", "không hợp lệ")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, error):
                    import_devices(_Cursor(), [{**row, "NumCell": value}])


if __name__ == "__main__":
    unittest.main()
