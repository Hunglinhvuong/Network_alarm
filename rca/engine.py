"""
RCA Engine — phân tích theo từng STATION (dùng escalation.station_status làm nguồn
dữ liệu trạng thái duy nhất, tránh 2 nơi định nghĩa full-down/partial-down khác nhau).

  - full_down   (mọi device loss_comm)  -> chạy đủ bước 1 -> 2 -> 3
  - partial_down (một phần device)      -> BỎ QUA bước 1, chỉ chạy bước 2 -> 3
  - power_affected (>=1 device power_fail active) -> flag độc lập, luôn kèm báo cáo

RCAP (Root Cause Analysis Procedure) — mỗi bước dừng ngay khi có bằng chứng:
  Bước 1 (chỉ full_down): đồng bộ mất liên lạc trong cùng node cha, cửa sổ ±10 phút.
      Có bằng chứng -> dừng, báo cáo theo "node cha lớn nhất" (lấy từ escalation
      group — vốn đã climb lên node cao nhất mà mọi station con cháu đều full-down).
  Bước 2: power_fail của BẤT KỲ device nào trong station, trong 12h gần nhất tính
      từ NOW, xảy ra trước loss_comm <= 6h.
  Bước 3: trạm lân cận (neighbor_edge, ≤ NEIGHBOR_DISTANCE_KM) có power_fail active
      không -> khuyến nghị "x/y trạm lân cận mất điện".
  Không bước nào có bằng chứng -> isolated.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape

from db.connection import get_cursor
from config.settings import (
    NODE_SYNC_WINDOW_MINUTES,
    POWER_FAIL_LOOKBACK_HOURS,
    POWER_FAIL_CORRELATION_HOURS,
    NODE_TYPE_STATION,
)
from escalation.station_status import (
    get_station_full_status,
    get_station_full_statuses,
    FULL_DOWN,
    PARTIAL_DOWN,
)
from escalation.engine import compute_escalation

logger = logging.getLogger(__name__)

CONCLUSION_SYNC_PARENT = "sync_parent_node"
CONCLUSION_POWER_FAIL = "power_fail"
CONCLUSION_AREA_POWER = "area_power_outage"
CONCLUSION_ISOLATED = "isolated"

CONCLUSION_LABEL_VI = {
    CONCLUSION_SYNC_PARENT: "Nghi ngờ sự cố tuyến/thiết bị truyền dẫn (các trạm cùng node mất liên lạc đồng thời)",
    CONCLUSION_POWER_FAIL: "Nghi ngờ do mất điện tại trạm",
    CONCLUSION_AREA_POWER: "Nghi ngờ sự cố điện (trạm lân cận cũng mất điện)",
    CONCLUSION_ISOLATED: "Chưa xác định nguyên nhân, kiểm tra thiết bị hoặc truyền dẫn",
}


@dataclass
class StationRCAResult:
    station: dict  # kết quả của escalation.station_status.get_station_full_status()
    conclusion: str = CONCLUSION_ISOLATED
    step1_evidence: dict = None
    step2_evidence: dict = None
    step3_evidence: dict = None

    @property
    def site_code(self) -> str:
        return self.station["site_code"]

    @property
    def conclusion_label(self) -> str:
        return CONCLUSION_LABEL_VI.get(self.conclusion, self.conclusion)

    def to_text(self) -> str:
        s = self.station
        lines = []
        if s["comm_status"] == FULL_DOWN:
            lines.append(f"Trạng thái: 🔴 MẤT LIÊN LẠC TOÀN BỘ ({len(s['devices'])}/{len(s['devices'])} thiết bị)")
        else:
            lines.append(
                f"Trạng thái: 🟠 MẤT LIÊN LẠC MỘT PHẦN — loại thiết bị ảnh hưởng: "
                f"{', '.join(escape(str(value)) for value in s['affected_device_types'])}"
            )
        if s["power_affected"]:
            lines.append("⚡ Ghi nhận mất điện tại trạm (tính cả station bị ảnh hưởng)")

        lines.append(f"RCA: {self.conclusion_label}")

        if self.step1_evidence:
            ev = self.step1_evidence
            lines.append(
                f"- Đồng bộ cùng node cha: <b>{escape(str(ev['node_name']))}</b> "
                f"({escape(str(ev['node_code']))}, {escape(str(ev['node_type']))})"
                f" — trạm ảnh hưởng: {', '.join(escape(str(code)) for code in ev['affected_station_codes'])}"
            )
        if self.step2_evidence:
            ev = self.step2_evidence
            lines.append(
                f"- Mất điện liên quan: thiết bị {escape(str(ev['device_code']))} lúc {escape(str(ev['start_time']))}"
                f" (trước loss_comm {ev['hours_before']:.1f}h)"
            )
        if self.step3_evidence:
            ev = self.step3_evidence
            extra = f" ({', '.join(escape(str(code)) for code in ev['affected_codes'])})" if ev["affected_codes"] else ""
            lines.append(f"- Trạm lân cận mất điện: {ev['affected_count']}/{ev['total_count']} trạm{extra}")
        return "\n".join(lines)


def _step1_sync_parent(station: dict) -> dict:
    """
    Dùng lại escalation.compute_escalation() để tìm group (node cha lớn nhất) mà
    station này thuộc về (compute_escalation chỉ gồm station full-down). Nếu group
    chỉ có đúng station này -> không đồng bộ. Nếu có station khác -> kiểm tra thêm
    điều kiện cửa sổ ±NODE_SYNC_WINDOW_MINUTES trước khi kết luận.
    """
    groups = compute_escalation()
    group = next((g for g in groups if station["site_id"] in g.station_site_ids), None)
    if group is None or group.node_type == NODE_TYPE_STATION:
        return None

    other_site_ids = [sid for sid in group.station_site_ids if sid != station["site_id"]]
    if not other_site_ids:
        return None

    window_start = station["earliest_loss_comm_start"] - timedelta(minutes=NODE_SYNC_WINDOW_MINUTES)
    window_end = station["earliest_loss_comm_start"] + timedelta(minutes=NODE_SYNC_WINDOW_MINUTES)

    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT s2.site_id, s2.site_code, MIN(ae2.start_time) AS start_time
            FROM device d2
            JOIN station s2 ON s2.site_id = d2.site_id
            JOIN alarm_event ae2
                ON ae2.device_id = d2.device_id
               AND ae2.alarm_name = 'loss_comm' AND ae2.status = 'active'
            WHERE s2.site_id = ANY(%(site_ids)s)
            GROUP BY s2.site_id, s2.site_code
            HAVING MIN(ae2.start_time) BETWEEN %(window_start)s AND %(window_end)s
            """,
            {"site_ids": other_site_ids, "window_start": window_start, "window_end": window_end},
        )
        siblings_in_window = cur.fetchall()

    if not siblings_in_window:
        return None

    return {
        "node_code": group.node_code,
        "node_name": group.node_name,
        "node_type": group.node_type,
        "affected_station_codes": sorted(group.station_site_codes),
    }


def _step2_power_fail(station: dict) -> dict:
    """power_fail của BẤT KỲ device nào trong station, trong 12h gần nhất tính từ
    NOW, xảy ra trước loss_comm <= 6h."""
    now = datetime.now()
    lookback_start = now - timedelta(hours=POWER_FAIL_LOOKBACK_HOURS)
    correlation_start = station["earliest_loss_comm_start"] - timedelta(hours=POWER_FAIL_CORRELATION_HOURS)

    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT ae.alarm_id, ae.start_time, d.device_code
            FROM alarm_event ae
            JOIN device d ON d.device_id = ae.device_id
            WHERE d.site_id = %(site_id)s
              AND ae.alarm_name = 'power_fail'
              AND ae.start_time >= %(lookback_start)s
              AND ae.start_time <= %(loss_comm_start)s
              AND ae.start_time >= %(correlation_start)s
            ORDER BY ae.start_time DESC
            LIMIT 1
            """,
            {
                "site_id": station["site_id"],
                "lookback_start": lookback_start,
                "loss_comm_start": station["earliest_loss_comm_start"],
                "correlation_start": correlation_start,
            },
        )
        row = cur.fetchone()

    if row is None:
        return None

    hours_before = (station["earliest_loss_comm_start"] - row["start_time"]).total_seconds() / 3600.0
    return {
        "alarm_id": row["alarm_id"],
        "start_time": row["start_time"],
        "device_code": row["device_code"],
        "hours_before": hours_before,
    }


def _fetch_neighbor_power_rows(site_ids: list[int]) -> list:
    """Raw neighbor rows cho một hoặc nhiều station bằng một query."""
    site_ids = list(dict.fromkeys(site_ids))
    if not site_ids:
        return []

    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT endpoints.source_site_id, s2.site_id, s2.site_code,
                   BOOL_OR(ae2.alarm_id IS NOT NULL) AS has_power_fail
            FROM neighbor_edge ne
            CROSS JOIN LATERAL (
                SELECT CASE
                    WHEN ne.site_id_a = ANY(%(site_ids)s) THEN ne.site_id_a
                    ELSE ne.site_id_b
                END AS source_site_id,
                CASE
                    WHEN ne.site_id_a = ANY(%(site_ids)s) THEN ne.site_id_b
                    ELSE ne.site_id_a
                END AS neighbor_site_id
            ) endpoints
            JOIN station s2
                ON s2.site_id = endpoints.neighbor_site_id
            JOIN device d2 ON d2.site_id = s2.site_id
            LEFT JOIN alarm_event ae2
                ON ae2.device_id = d2.device_id
               AND ae2.alarm_name = 'power_fail' AND ae2.status = 'active'
            WHERE ne.site_id_a = ANY(%(site_ids)s) OR ne.site_id_b = ANY(%(site_ids)s)
            GROUP BY endpoints.source_site_id, s2.site_id, s2.site_code
            """,
            {"site_ids": site_ids},
        )
        return cur.fetchall()


def _step2_power_fail_multi(site_ids: list, loss_comm_start) -> dict:
    """Bản gộp bước 2: power_fail của BẤT KỲ device nào thuộc BẤT KỲ station nào
    trong node (site_ids), tương quan với thời điểm mất liên lạc sớm nhất của node."""
    now = datetime.now()
    lookback_start = now - timedelta(hours=POWER_FAIL_LOOKBACK_HOURS)
    correlation_start = loss_comm_start - timedelta(hours=POWER_FAIL_CORRELATION_HOURS)

    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT ae.alarm_id, ae.start_time, d.device_code, s.site_code
            FROM alarm_event ae
            JOIN device d ON d.device_id = ae.device_id
            JOIN station s ON s.site_id = d.site_id
            WHERE d.site_id = ANY(%(site_ids)s)
              AND ae.alarm_name = 'power_fail'
              AND ae.start_time >= %(lookback_start)s
              AND ae.start_time <= %(loss_comm_start)s
              AND ae.start_time >= %(correlation_start)s
            ORDER BY ae.start_time DESC
            LIMIT 1
            """,
            {
                "site_ids": site_ids,
                "lookback_start": lookback_start,
                "loss_comm_start": loss_comm_start,
                "correlation_start": correlation_start,
            },
        )
        row = cur.fetchone()

    if row is None:
        return None

    hours_before = (loss_comm_start - row["start_time"]).total_seconds() / 3600.0
    return {
        "alarm_id": row["alarm_id"],
        "start_time": row["start_time"],
        "device_code": row["device_code"],
        "site_code": row["site_code"],
        "hours_before": hours_before,
    }


def _get_group_outage_timing(site_ids: list) -> dict:
    """Tóm tắt trạm cũ/mới và device bắt đầu mất liên lạc gần nhất trong group."""
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT s.site_id, s.site_name, d.type AS device_type, ae.start_time
            FROM alarm_event ae
            JOIN device d ON d.device_id = ae.device_id
            JOIN station s ON s.site_id = d.site_id
            WHERE d.site_id = ANY(%(site_ids)s)
              AND ae.alarm_name = 'loss_comm'
              AND ae.status = 'active'
            ORDER BY ae.start_time, s.site_name, d.type
            """,
            {"site_ids": site_ids},
        )
        rows = cur.fetchall()

    if not rows:
        return None

    latest_start = max(row["start_time"] for row in rows)
    window_start = latest_start - timedelta(minutes=NODE_SYNC_WINDOW_MINUTES)
    station_first_alarm = {}
    for row in rows:
        station_first_alarm[row["site_id"]] = min(
            station_first_alarm.get(row["site_id"], row["start_time"]),
            row["start_time"],
        )

    old_station_ids = {
        site_id for site_id, start_time in station_first_alarm.items() if start_time < window_start
    }
    new_devices_by_station = {}
    for row in rows:
        if row["start_time"] < window_start:
            continue
        device = new_devices_by_station.setdefault(
            row["site_id"], {"site_name": row["site_name"], "device_types": set()}
        )
        device["device_types"].add(row["device_type"])

    new_devices = [
        {"site_name": device["site_name"], "device_types": sorted(device["device_types"])}
        for device in new_devices_by_station.values()
    ]
    return {
        "old_station_count": len(old_station_ids),
        "new_station_count": len(station_first_alarm) - len(old_station_ids),
        "detected_at": latest_start,
        "new_devices": new_devices,
    }



def _step3_neighbor_power(station: dict) -> dict:
    """Trạm lân cận (neighbor_edge, ≤ NEIGHBOR_DISTANCE_KM) có power_fail active không."""
    rows = _fetch_neighbor_power_rows([station["site_id"]])
    total_count = len(rows)
    if total_count == 0:
        return None  # không có neighbor -> không đánh giá được bước này

    affected = [r for r in rows if r["has_power_fail"]]
    return {
        "affected_count": len(affected),
        "total_count": total_count,
        "affected_codes": sorted(r["site_code"] for r in affected),
    }


def _step3_neighbor_power_multi(site_ids: list) -> dict:
    """
    Bản gộp cho 1 node cha có nhiều station: hợp nhất tập neighbor của TẤT CẢ
    station trong node (khử trùng lặp), loại bỏ neighbor nào chính là 1 thành viên
    trong node (đã tính là "down", không phải "lân cận" theo nghĩa cần cảnh báo thêm).
    """
    member_ids = set(site_ids)
    neighbor_has_power = {}
    neighbor_code = {}
    for r in _fetch_neighbor_power_rows(site_ids):
        if r["site_id"] in member_ids:
            continue
        neighbor_code[r["site_id"]] = r["site_code"]
        neighbor_has_power[r["site_id"]] = neighbor_has_power.get(r["site_id"], False) or r["has_power_fail"]

    total_count = len(neighbor_has_power)
    if total_count == 0:
        return None

    affected_ids = [sid for sid, v in neighbor_has_power.items() if v]
    return {
        "affected_count": len(affected_ids),
        "total_count": total_count,
        "affected_codes": sorted(neighbor_code[sid] for sid in affected_ids),
    }


@dataclass
class NodeRCAResult:
    """Kết quả RCA cho CẢ 1 node cha (escalation group) — chỉ 1 kết luận duy nhất
    cho toàn node, không liệt kê phân tích riêng từng station con."""
    node_code: str
    node_name: str
    node_type: str
    station_codes: list
    power_affected_codes: list = field(default_factory=list)
    outage_timing_evidence: dict = None
    conclusion: str = CONCLUSION_ISOLATED
    step1_evidence: dict = None
    step2_evidence: dict = None
    step3_evidence: dict = None

    @property
    def conclusion_label(self) -> str:
        return CONCLUSION_LABEL_VI.get(self.conclusion, self.conclusion)

    def to_text(self) -> str:
        lines = [f"RCA: {self.conclusion_label}"]

        if self.step1_evidence:
            ev = self.step1_evidence
            lines.append(
                f"- Toàn bộ thiết bị tại {ev['count']} trạm mất liên lạc đồng bộ "
                f"trong cửa sổ ±{NODE_SYNC_WINDOW_MINUTES} phút "
                f"({ev['earliest']} → {ev['latest']})"
            )
        if self.outage_timing_evidence:
            ev = self.outage_timing_evidence
            lines.append(
                f"- Các trạm trong node {escape(str(self.node_name))} không mất liên lạc đồng thời; "
                f"có {ev['old_station_count']} trạm mất liên lạc từ trước, "
                f"có {ev['new_station_count']} trạm mới phát hiện mất liên lạc vào "
                f"{ev['detected_at'].strftime('%Y-%m-%d %H:%M:%S')}"
            )
            lines.append(
                "- Thiết bị mới phát hiện mất liên lạc: "
                + ", ".join(
                    f"{escape(str(d['site_name']))} "
                    f"({','.join(escape(str(value)) for value in d['device_types'])})"
                    for d in ev["new_devices"]
                )
            )
        if self.step2_evidence:
            ev = self.step2_evidence
            lines.append(
                f"- Mất điện liên quan: thiết bị {escape(str(ev['device_code']))} "
                f"(trạm {escape(str(ev['site_code']))}) lúc {escape(str(ev['start_time']))}"
                f" (trước loss_comm {ev['hours_before']:.1f}h)"
            )
        if self.step3_evidence:
            ev = self.step3_evidence
            extra = f" ({', '.join(escape(str(code)) for code in ev['affected_codes'])})" if ev["affected_codes"] else ""
            lines.append(f"- Trạm lân cận mất điện: {ev['affected_count']}/{ev['total_count']} trạm{extra}")

        if self.power_affected_codes:
            lines.append(
                "⚡ Ghi nhận mất điện tại trạm: "
                + ", ".join(escape(str(code)) for code in self.power_affected_codes)
            )

        return "\n".join(lines)


def analyze_group(group) -> NodeRCAResult:
    """
    Chạy RCAP MỘT LẦN cho toàn bộ node cha (group) mà escalation.compute_escalation()
    đã gộp lên — KHÔNG chạy lại RCA riêng cho từng station con bên trong.

        - group có >1 station: bước 1 chỉ kết luận sự cố node cha khi tất cả station
            đều full-down và thời điểm loss_comm của toàn bộ device trong group nằm
            trong cùng cửa sổ NODE_SYNC_WINDOW_MINUTES.
    - group chỉ có 1 station (node_type=STATION, không có sibling nào down cùng):
      bước 1 tự động bỏ qua (không có gì để so sánh đồng bộ), chạy thẳng bước 2.
    - Không đạt bước 1 -> bước 2 (power_fail bất kỳ device nào trong TOÀN BỘ group)
      -> không đạt -> bước 3 (neighbor của TOÀN BỘ group, loại trừ chính các
      station thành viên).
    """
    station_statuses = get_station_full_statuses(sorted(group.station_site_ids))
    stations = list(station_statuses.values())
    station_codes = sorted(s["site_code"] for s in stations)
    power_affected_codes = sorted(s["site_code"] for s in stations if s["power_affected"])

    result = NodeRCAResult(
        node_code=group.node_code,
        node_name=group.node_name,
        node_type=group.node_type,
        station_codes=station_codes,
        power_affected_codes=power_affected_codes,
    )

    starts = [s["earliest_loss_comm_start"] for s in stations]
    latest_start = max(starts)
    window_start = latest_start - timedelta(minutes=NODE_SYNC_WINDOW_MINUTES)
    latest_wave_stations = [s for s in stations if s["earliest_loss_comm_start"] >= window_start]
    is_staged_outage = len(latest_wave_stations) < len(stations)

    if is_staged_outage:
        result.outage_timing_evidence = _get_group_outage_timing(list(group.station_site_ids))

    all_stations_full_down = all(s["comm_status"] == FULL_DOWN for s in stations)
    device_loss_starts = [
        device["loss_comm_start"]
        for station in stations
        for device in station["devices"]
        if device["loss_comm_active"]
    ]
    total_device_count = sum(len(station["devices"]) for station in stations)

    if len(stations) > 1 and all_stations_full_down and len(device_loss_starts) == total_device_count:
        earliest_device_loss = min(device_loss_starts)
        latest_device_loss = max(device_loss_starts)
        if latest_device_loss - earliest_device_loss <= timedelta(minutes=NODE_SYNC_WINDOW_MINUTES):
            result.conclusion = CONCLUSION_SYNC_PARENT
            result.step1_evidence = {
                "count": len(stations),
                "earliest": earliest_device_loss,
                "latest": latest_device_loss,
            }
            logger.info(
                "RCA node=%s -> %s (bước 1, toàn bộ %d device tại %d trạm)",
                group.node_code,
                result.conclusion,
                len(device_loss_starts),
                len(stations),
            )
            return result

    power_correlation_stations = latest_wave_stations if is_staged_outage else stations
    site_ids = [s["site_id"] for s in power_correlation_stations]
    earliest_overall = min(s["earliest_loss_comm_start"] for s in power_correlation_stations)

    step2 = _step2_power_fail_multi(site_ids, earliest_overall)
    if step2:
        result.conclusion = CONCLUSION_POWER_FAIL
        result.step2_evidence = step2
        logger.info("RCA node=%s -> %s (bước 2)", group.node_code, result.conclusion)
        return result

    step3 = _step3_neighbor_power_multi(site_ids)
    if step3 and step3["affected_count"] > 0:
        result.conclusion = CONCLUSION_AREA_POWER
        result.step3_evidence = step3
        logger.info("RCA node=%s -> %s (bước 3)", group.node_code, result.conclusion)
        return result

    result.conclusion = CONCLUSION_ISOLATED
    logger.info("RCA node=%s -> %s (không bước nào có bằng chứng)", group.node_code, result.conclusion)
    return result


def analyze_station(site_id: int) -> StationRCAResult:
    """
    Chạy RCAP cho 1 station:
      - full_down    -> bước 1 -> (nếu không) bước 2 -> (nếu không) bước 3
      - partial_down -> bỏ qua bước 1, chỉ bước 2 -> (nếu không) bước 3
    Dừng ngay khi 1 bước có bằng chứng (short-circuit). Ném ValueError nếu station
    không tồn tại hoặc không có device nào loss_comm active.
    """
    station = get_station_full_status(site_id)
    result = StationRCAResult(station=station)

    if station["comm_status"] == FULL_DOWN:
        step1 = _step1_sync_parent(station)
        if step1:
            result.conclusion = CONCLUSION_SYNC_PARENT
            result.step1_evidence = step1
            logger.info("RCA station=%s -> %s (bước 1)", station["site_code"], result.conclusion)
            return result

    step2 = _step2_power_fail(station)
    if step2:
        result.conclusion = CONCLUSION_POWER_FAIL
        result.step2_evidence = step2
        logger.info("RCA station=%s -> %s (bước 2)", station["site_code"], result.conclusion)
        return result

    step3 = _step3_neighbor_power(station)
    if step3 and step3["affected_count"] > 0:
        result.conclusion = CONCLUSION_AREA_POWER
        result.step3_evidence = step3
        logger.info("RCA station=%s -> %s (bước 3)", station["site_code"], result.conclusion)
        return result

    result.conclusion = CONCLUSION_ISOLATED
    logger.info("RCA station=%s -> %s (không bước nào có bằng chứng)", station["site_code"], result.conclusion)
    return result
