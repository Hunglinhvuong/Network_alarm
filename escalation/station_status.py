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

FULL_DOWN = "full_down"
PARTIAL_DOWN = "partial_down"


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
                   ae.alarm_id, ae.start_time AS loss_comm_start
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
                "earliest_loss_comm_start": None,
            },
        )
        entry["total_devices"] += 1
        if r["alarm_id"] is not None:
            device = {
                "device_id": r["device_id"],
                "device_code": r["device_code"],
                "device_type": r["device_type"],
                "alarm_id": r["alarm_id"],
                "loss_comm_start": r["loss_comm_start"],
            }
            entry["down_devices"].append(device)
            if r["loss_comm_start"] is not None:
                if entry["earliest_loss_comm_start"] is None:
                    entry["earliest_loss_comm_start"] = r["loss_comm_start"]
                else:
                    entry["earliest_loss_comm_start"] = min(
                        entry["earliest_loss_comm_start"],
                        r["loss_comm_start"],
                    )

    for entry in stations.values():
        entry["all_down"] = entry["total_devices"] > 0 and len(entry["down_devices"]) == entry["total_devices"]

    return stations


def get_fully_down_stations(breakdown: dict | None = None) -> dict:
    """site_id -> {site_code, site_name, node_id, alarm_ids: set} — CHỈ station mà mọi device đều loss_comm."""
    breakdown = breakdown if breakdown is not None else get_station_loss_comm_breakdown()
    result = {}
    for site_id, info in breakdown.items():
        if info["all_down"]:
            result[site_id] = {
                "site_code": info["site_code"],
                "site_name": info["site_name"],
                "node_id": info["node_id"],
                "alarm_ids": {d["alarm_id"] for d in info["down_devices"]},
                "earliest_loss_comm_start": info.get("earliest_loss_comm_start"),
            }
    return result


def get_partial_loss_comm_devices(breakdown: dict | None = None) -> list:
    """
    Danh sách device mất liên lạc riêng lẻ trong các station CHƯA down toàn bộ.
    Mỗi phần tử: {alarm_id, device_code, device_type, site_code, site_name}.
    """
    breakdown = breakdown if breakdown is not None else get_station_loss_comm_breakdown()
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


def get_partial_down_stations(breakdown: dict | None = None) -> dict:
    """
    site_id -> {site_code, site_name} — CHỈ station có loss_comm 1 PHẦN (không toàn
    bộ). Dùng để diff theo từng chu kỳ poll ở mức STATION (không phải mức device)
    -> RCA và cảnh báo giờ chạy theo station, xem rca.engine.analyze_station().
    """
    breakdown = breakdown if breakdown is not None else get_station_loss_comm_breakdown()
    return {
        site_id: {"site_code": info["site_code"], "site_name": info["site_name"]}
        for site_id, info in breakdown.items()
        if not info["all_down"]
    }


def get_station_full_statuses(site_ids: list[int]) -> dict[int, dict]:
    """
    Chi tiết các station — dùng bởi RCA engine để tránh truy vấn từng station:
    {
        site_id, site_code, site_name,
        devices: [{device_id, device_code, device_type, loss_comm_active,
                   loss_comm_start, power_fail_active}],
        comm_status: 'full_down' | 'partial_down',
        affected_device_types: [...],   # chỉ có khi partial_down
        power_affected: bool,           # True nếu BẤT KỲ device nào có power_fail active
        earliest_loss_comm_start: datetime | None,
    }
    Ném ValueError nếu station không tồn tại hoặc không có device nào loss_comm active.
    """
    site_ids = list(dict.fromkeys(site_ids))
    if not site_ids:
        return {}

    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT s.site_id, s.site_code, s.site_name,
                   d.device_id, d.device_code, d.type AS device_type,
                   ae_comm.start_time AS loss_comm_start,
                   ae_power.is_active AS power_fail_active
            FROM station s
            JOIN device d ON d.site_id = s.site_id
            LEFT JOIN LATERAL (
                SELECT MIN(ae.start_time) AS start_time
                FROM alarm_event ae
                WHERE ae.device_id = d.device_id
                  AND ae.alarm_name = 'loss_comm' AND ae.status = 'active'
            ) ae_comm ON TRUE
            LEFT JOIN LATERAL (
                SELECT EXISTS (
                    SELECT 1 FROM alarm_event ae
                    WHERE ae.device_id = d.device_id
                      AND ae.alarm_name = 'power_fail' AND ae.status = 'active'
                ) AS is_active
            ) ae_power ON TRUE
            WHERE s.site_id = ANY(%(site_ids)s)
            ORDER BY s.site_id, d.device_id
            """,
            {"site_ids": site_ids},
        )
        rows = cur.fetchall()

    rows_by_site = {}
    for r in rows:
        rows_by_site.setdefault(r["site_id"], []).append(r)

    result = {}
    for site_id in site_ids:
        station_rows = rows_by_site.get(site_id, [])
        if not station_rows:
            raise ValueError(f"Không tìm thấy station site_id={site_id} hoặc station chưa có device")

        devices = []
        loss_comm_starts = []
        power_affected = False
        for r in station_rows:
            loss_comm_active = r["loss_comm_start"] is not None
            power_fail_active = r["power_fail_active"]
            devices.append(
                {
                    "device_id": r["device_id"],
                    "device_code": r["device_code"],
                    "device_type": r["device_type"],
                    "loss_comm_active": loss_comm_active,
                    "loss_comm_start": r["loss_comm_start"],
                    "power_fail_active": power_fail_active,
                }
            )
            if loss_comm_active:
                loss_comm_starts.append(r["loss_comm_start"])
            if power_fail_active:
                power_affected = True

        if not loss_comm_starts:
            raise ValueError(f"Station site_id={site_id} không có device nào loss_comm active -> không cần RCA")

        total = len(devices)
        down_count = len(loss_comm_starts)
        comm_status = FULL_DOWN if down_count == total else PARTIAL_DOWN
        affected_device_types = (
            sorted({d["device_type"] for d in devices if d["loss_comm_active"]})
            if comm_status == PARTIAL_DOWN
            else []
        )
        first = station_rows[0]
        result[site_id] = {
            "site_id": first["site_id"],
            "site_code": first["site_code"],
            "site_name": first["site_name"],
            "devices": devices,
            "comm_status": comm_status,
            "affected_device_types": affected_device_types,
            "power_affected": power_affected,
            "earliest_loss_comm_start": min(loss_comm_starts),
        }

    return result


def get_station_full_status(site_id: int) -> dict:
    """Chi tiết đầy đủ một station; wrapper tương thích cho caller đơn lẻ."""
    return get_station_full_statuses([site_id])[site_id]
