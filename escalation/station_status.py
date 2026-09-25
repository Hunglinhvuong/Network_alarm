"""
Áp dụng quy tắc thống nhất về loss_comm ở mức station:
  - Station được coi là MẤT LIÊN LẠC (full) chỉ khi TẤT CẢ device của station đó
    đều đang có alarm loss_comm active.
  - Nếu chỉ một phần device mất liên lạc (không phải tất cả) -> KHÔNG tính là
    station down; báo riêng theo từng device (device.type + station.site_name).

Đây là nguồn dữ liệu dùng chung cho escalation (chỉ station full-down mới vào cây
escalation) và cảnh báo device-level (partial).
"""
import logging

from db.connection import get_cursor

logger = logging.getLogger(__name__)


def get_station_loss_comm_breakdown() -> dict:
    """
    Trả về dict site_id -> {
        site_code, site_name, node_id, total_devices,
        down_devices: [{device_id, device_code, device_type, alarm_id}],
        all_down: bool,
    }
    Chỉ gồm các station có >=1 device đang loss_comm active — station hoàn toàn
    bình thường sẽ không xuất hiện trong dict trả về (đỡ tốn duyệt trên Wyse 5010).
    """
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            WITH affected_sites AS (
                SELECT DISTINCT d.site_id
                FROM alarm_event ae
                JOIN device d ON d.device_id = ae.device_id
                WHERE ae.alarm_name = 'loss_comm' AND ae.status = 'active'
            )
            SELECT s.site_id, s.site_code, s.site_name,
                   d.device_id, d.device_code, d.type AS device_type,
                   ae.alarm_id
            FROM affected_sites af
            JOIN station s ON s.site_id = af.site_id
            JOIN device d ON d.site_id = s.site_id
            LEFT JOIN alarm_event ae
                ON ae.device_id = d.device_id AND ae.alarm_name = 'loss_comm' AND ae.status = 'active'
            """
        )
        rows = cur.fetchall()

    stations = {}
    for r in rows:
        entry = stations.setdefault(
            r["site_id"],
            {
                "site_code": r["site_code"],
                "site_name": r["site_name"],
                "node_id": r["site_id"],  # station.site_id là PK/FK thẳng tới node.node_id
                "total_devices": 0,
                "down_devices": [],
            },
        )
        entry["total_devices"] += 1
        if r["alarm_id"] is not None:
            entry["down_devices"].append(
                {
                    "device_id": r["device_id"],
                    "device_code": r["device_code"],
                    "device_type": r["device_type"],
                    "alarm_id": r["alarm_id"],
                }
            )

    for entry in stations.values():
        entry["all_down"] = entry["total_devices"] > 0 and len(entry["down_devices"]) == entry["total_devices"]

    return stations


def get_fully_down_stations() -> dict:
    """site_id -> {site_code, site_name, node_id, alarm_ids: set} — CHỈ station mà mọi device đều loss_comm."""
    breakdown = get_station_loss_comm_breakdown()
    result = {}
    for site_id, info in breakdown.items():
        if info["all_down"]:
            result[site_id] = {
                "site_code": info["site_code"],
                "site_name": info["site_name"],
                "node_id": info["node_id"],
                "alarm_ids": {d["alarm_id"] for d in info["down_devices"]},
            }
    return result


def get_partial_loss_comm_devices() -> list:
    """
    Danh sách device mất liên lạc riêng lẻ trong các station CHƯA down toàn bộ.
    Mỗi phần tử: {alarm_id, device_code, device_type, site_code, site_name}.
    """
    breakdown = get_station_loss_comm_breakdown()
    partial = []
    for info in breakdown.values():
        if info["all_down"]:
            continue
        for d in info["down_devices"]:
            partial.append(
                {
                    "alarm_id": d["alarm_id"],
                    "device_code": d["device_code"],
                    "device_type": d["device_type"],
                    "site_code": info["site_code"],
                    "site_name": info["site_name"],
                }
            )
    return partial
