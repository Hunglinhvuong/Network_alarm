import unittest

from alerting.messages import format_down_alert_single, format_down_alert_node


class DummyGroup:
    def __init__(self, node_type, node_name, node_code, station_site_ids, station_site_codes):
        self.node_type = node_type
        self.node_name = node_name
        self.node_code = node_code
        self.station_site_ids = station_site_ids
        self.station_site_codes = station_site_codes


class DummyRCA:
    def __init__(self, conclusion="isolated", station=None):
        self.conclusion = conclusion
        self.station = station or {
            "devices": [
                {"device_type": "3G"},
                {"device_type": "4G"},
            ]
        }

    def to_text(self):
        return "RCA: test"


class FormatDownAlertTests(unittest.TestCase):
    def test_single_station_full_down_uses_station_specific_title(self):
        group = DummyGroup(
            node_type="STATION",
            node_name="Trạm A",
            node_code="STA-01",
            station_site_ids={101},
            station_site_codes={"STA-01"},
        )

        text = format_down_alert_single(
            group,
            DummyRCA(conclusion="isolated", station={"devices": [{"device_type": "3G"}, {"device_type": "4G"}]})
        )

        self.assertIn("MẤT LIÊN LẠC TOÀN BỘ TRẠM", text)
        self.assertNotIn("MẤT LIÊN LẠC TOÀN BỘ NODE TRUYỀN DẪN", text)

    def test_single_station_full_down_reads_real_device_types_from_station(self):
        group = DummyGroup(
            node_type="STATION",
            node_name="Trạm A",
            node_code="STA-01",
            station_site_ids={101},
            station_site_codes={"STA-01"},
        )

        text = format_down_alert_single(
            group,
            DummyRCA(conclusion="isolated", station={"devices": [{"device_type": "3G"}, {"device_type": "4G"}]})
        )

        self.assertIn("Thiết bị mất liên lạc:", text)
        self.assertIn("3G", text)
        self.assertIn("4G", text)
        self.assertNotIn("không xác định", text)

    def test_sync_parent_full_down_uses_sync_parent_title(self):
        group = DummyGroup(
            node_type="TRANSMISSION",
            node_name="Node 10",
            node_code="N10",
            station_site_ids={101, 102},
            station_site_codes={"STA-01", "STA-02"},
        )

        text = format_down_alert_node(group, DummyRCA(conclusion="sync_parent_node"))

        self.assertIn("MẤT LIÊN LẠC TOÀN BỘ NODE TRUYỀN DẪN", text)
        self.assertNotIn("MẤT LIÊN LẠC TOÀN BỘ TRẠM", text)

    def test_station_metadata_is_escaped_before_html_delivery(self):
        group = DummyGroup(
            node_type="STATION",
            node_name="<b>unsafe</b>",
            node_code="STA-01",
            station_site_ids={101},
            station_site_codes={"STA-01"},
        )

        text = format_down_alert_single(group, DummyRCA())

        self.assertIn("&lt;b&gt;unsafe&lt;/b&gt;", text)


if __name__ == "__main__":
    unittest.main()
