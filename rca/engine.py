"""
RCA Engine — phân tích theo từng STATION (dùng escalation.station_status làm nguồn
dữ liệu trạng thái duy nhất, tránh 2 nơi định nghĩa full-down/partial-down khác nhau).

  - full_down   (mọi device loss_comm)  -> chạy đủ bước 1 -> 2 -> 3
  - partial_down (một phần device)      -> BỎ QUA bước 1, chỉ chạy bước 2 -> 3
  - power_affected (>=1 device power_fail active) -> flag độc lập, luôn kèm báo cáo

RCAP (Root Cause Analysis Procedure) — mỗi bước dừng ngay khi có bằng chứng:
  Bước 1 (chỉ full_down, có >1 station trong group): đồng bộ mất liên lạc trong cùng
      node cha, cửa sổ ±NODE_SYNC_WINDOW_MINUTES. Lưu ý: escalation.compute_escalation()
      đã validate rồi, nên nếu group có >1 station thì chắc chắn toàn bộ full-down +
      trong window. Bước 1 ở đây chỉ cần double-check và sinh evidence.
  Bước 2: power_fail của BẤT KỲ device nào trong station, trong 12h gần nhất tính
      từ NOW, xảy ra trước loss_comm <= 6h.
  Bước 3: trạm lân cận (neighbor_edge, ≤ NEIGHBOR_DISTANCE_KM) có power_fail active
      không -> khuyến nghị "x/y trạm lân cận mất điện".
  Không bước nào có bằng chứng -> isolated.

Đặc biệt: Bước 1 bây giờ PHẢI đảm bảo TẤT CẢ station trong group:
  - Là full-down
  - Nằm trong ±NODE_SYNC_WINDOW_MINUTES
  Nếu có 1 station không thỏa 2 điều kiện này -> KHÔNG báo "sync_parent", tiếp tục
  bước 2/3 thay vào đó.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from db.connection import get_cursor
from config.settings import (
    NODE_SYNC_WINDOW_MINUTES,
    POWER_FAIL_LOOKBACK_HOURS,
    POWER_FAIL_CORRELATION_HOURS,
    NODE_TYPE_STATION,
)
from escalation.station_status import get_station_full_status, FULL_DOWN, PARTIAL_DOWN
from escalation.engine import compute_escalation

logger = logging.getLogger(__name__)

CONCLUSION_SYNC_PARENT = "sync_parent_node"
CONCLUSION_POWER_FAIL = "power_fail"
CONCLUSION_AREA_POWER = "area_power_outage"
CONCLUSION_ISOLATED = "isolated"

CONCLUSION_LABEL_VI = {
    CONCLUSION_SYNC_PARENT: "Nghi ngờ sự cố tuyến truyền dẫn / node cha (nhiều trạm cùng nhánh mất liên lạc đồng bộ)",
    CONCLUSION_POWER_FAIL: "Nghi ngờ do mất điện tại trạm",
    CONCLUSION_AREA_POWER: "Nghi ngờ sự cố điện diện rộng (trạm lân cận cũng mất điện)",
    CONCLUSION_ISOLATED: "Chưa xác định được nguyên nhân liên quan -> có thể lỗi cục bộ",
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
            lines.append(
                f"Trạng thái: 🔴 MẤT LIÊN LẠC TOÀN BỘ ({len(s['devices'])}/{len(s['devices'])} thiết bị)"
            )
        else:
            lines.append(
                f"Trạng thái: 🟠 MẤT LIÊN LẠC MỘT PHẦN — loại thiết bị ảnh hưởng: "
                f"{', '.join(s['affected_device_types'])}"
            )
        if s["power_affected"]:
            lines.append("⚡ Ghi nhận mất điện tại trạm (tính cả station bị ảnh hưởng)")

        lines.append(f"Kết luận RCA: {self.conclusion_label}")

        if self.step1_evidence:
            ev = self.step1_evidence
            lines.append(
                f"- Đồng bộ cùng node cha: <b>{ev['node_name']}</b> ({ev['node_code']}, {ev['node_type']})"
                f" — trạm ảnh hưởng: {', '.join(ev['affected_station_codes'])}"
            )
        if self.step2_evidence:
            ev = self.step2_evidence
            lines.append(
                f"- Mất điện liên quan: thiết bị {ev['device_code']} lúc {ev['start_time']}"
                f" (trước loss_comm {ev['hours_before']:.1f}h)"
            )
        if self.step3_evidence:
            ev = self.step3_evidence
            extra = f" ({', '.join(ev['affected_codes'])})" if ev["affected_codes"] else ""
            lines.append(
                f"- Trạm lân cận mất điện: {ev['affected_count']}/{ev['total_count']} trạm{extra}"
            )
        return "\n".join(lines)


def _step1_sync_parent(station: dict) -> dict:
    """
    Dùng lại escalation.compute_escalation() để tìm group (node cha lớn nhất) mà
    station này thuộc về. Lưu ý: compute_escalation đã validate rồi (full-down +
    trong window), nên nếu có group chứa station này + >1 member, thì chắc chắn
    toàn bộ thỏa điều kiện sync.

    Tuy nhiên, để chắc chắn, ta vẫn kiểm tra lại:
    1. Group chỉ có station này 1 mình -> không đồng bộ
    2. Group có >1 station nhưng không phải TRANS_NODE -> không đồng bộ
    3. Có station khác trong group, và tất cả nằm trong window -> đồng bộ
    """
    groups = compute_escalation()
    group = next((g for g in groups if station["site_id"] in g.station_site_ids), None)
    if group is None or group.node_type == NODE_TYPE_STATION:
        return None

    other_site_ids = [sid for sid in group.station_site_ids if sid != station["site_id"]]
    if not other_site_ids:
        return None

    # Double-check: tất cả other station phải full-down và nằm trong window
    try:
        other_stations = [get_station_full_status(sid) for sid in other_site_ids]
    except ValueError:
        return None

    # Bắt buộc tất cả phải full-down
    if any(s["comm_status"] != FULL_DOWN for s in other_stations):
        logger.warning(
            "Station %s: group có member partial-down, không xem là sync",
            station["site_code"],
        )
        return None

    # Kiểm tra window: tất cả station (gồm cả station này) phải trong window
    all_starts = [station["earliest_loss_comm_start"]] + [
        s["earliest_loss_comm_start"] for s in other_stations
    ]
    earliest = min(all_starts)
    latest = max(all_starts)
    if (latest - earliest) > timedelta(minutes=NODE_SYNC_WINDOW_MINUTES):
        logger.warning(
            "Station %s: group ngoài window ±%d phút (diff: %s), không xem là sync",
            station["site_code"],
            NODE_SYNC_WINDOW_MINUTES,
            latest - earliest,
        )
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
    correlation_start = station["earliest_loss_comm_start"] - timedelta(
        hours=POWER_FAIL_CORRELATION_HOURS
    )

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

    hours_before = (
        station["earliest_loss_comm_start"] - row["start_time"]
    ).total_seconds() / 3600.0
    return {
        "alarm_id": row["alarm_id"],
        "start_time": row["start_time"],
        "device_code": row["device_code"],
        "hours_before": hours_before,
    }


def _fetch_neighbor_power_rows(site_id: int) -> list:
    """Raw rows neighbor + power_fail active — dùng chung cho cả đánh giá 1 station
    (analyze_station) và gộp nhiều station trong 1 node (analyze_group)."""
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT s2.site_id, s2.site_code,
                   BOOL_OR(ae2.alarm_id IS NOT NULL) AS has_power_fail
            FROM neighbor_edge ne
            JOIN station s2
                ON s2.site_id = CASE WHEN ne.site_id_a = %(site_id)s THEN ne.site_id_b ELSE ne.site_id_a END
            JOIN device d2 ON d2.site_id = s2.site_id
            LEFT JOIN alarm_event ae2
                ON ae2.device_id = d2.device_id
               AND ae2.alarm_name = 'power_fail' AND ae2.status = 'active'
            WHERE ne.site_id_a = %(site_id)s OR ne.site_id_b = %(site_id)s
            GROUP BY s2.site_id, s2.site_code
            """,
            {"site_id": site_id},
        )
        return cur.fetchall()


def _step2_power_fail_multi(
    site_ids: list, loss_comm_start
) -> dict:
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


def _step3_neighbor_power(station: dict) -> dict:
    """Trạm lân cận (neighbor_edge, ≤ NEIGHBOR_DISTANCE_KM) có power_fail active không."""
    rows = _fetch_neighbor_power_rows(station["site_id"])
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
    for site_id in site_ids:
        for r in _fetch_neighbor_power_rows(site_id):
            if r["site_id"] in member_ids:
                continue
            neighbor_code[r["site_id"]] = r["site_code"]
            neighbor_has_power[r["site_id"]] = (
                neighbor_has_power.get(r["site_id"], False) or r["has_power_fail"]
            )

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
    conclusion: str = CONCLUSION_ISOLATED
    step1_evidence: dict = None
    step2_evidence: dict = None
    step3_evidence: dict = None

    @property
    def conclusion_label(self) -> str:
        return CONCLUSION_LABEL_VI.get(self.conclusion, self.conclusion)

    def to_text(self) -> str:
        lines = [f"Kết luận RCA: {self.conclusion_label}"]

        if self.step1_evidence:
            ev = self.step1_evidence
            lines.append(
                f"- Đồng bộ mất liên lạc trong cửa sổ ±{NODE_SYNC_WINDOW_MINUTES} phút:"
                f" {ev['count']} trạm cùng mất liên lạc ({ev['earliest']} → {ev['latest']})"
            )
        if self.step2_evidence:
            ev = self.step2_evidence
            lines.append(
                f"- Mất điện liên quan: thiết bị {ev['device_code']} (trạm {ev['site_code']}) lúc {ev['start_time']}"
                f" (trước loss_comm {ev['hours_before']:.1f}h)"
            )
        if self.step3_evidence:
            ev = self.step3_evidence
            extra = f" ({', '.join(ev['affected_codes'])})" if ev["affected_codes"] else ""
            lines.append(
                f"- Trạm lân cận mất điện: {ev['affected_count']}/{ev['total_count']} trạm{extra}"
            )

        if self.power_affected_codes:
            lines.append(f"⚡ Ghi nhận mất điện tại trạm: {', '.join(self.power_affected_codes)}")

        return "\n".join(lines)


def analyze_group(group) -> NodeRCAResult:
    """
    Chạy RCAP MỘT LẦN cho toàn bộ node cha (group) mà escalation.compute_escalation()
    đã gộp lên — KHÔNG chạy lại RCA riêng cho từng station con bên trong.

    LƯU Ý: compute_escalation() đã validate rồi (toàn bộ station full-down + trong
    window ±NODE_SYNC_WINDOW_MINUTES), nên:
    - group có >1 station: chắc chắn bước 1 (sync_parent) đạt
    - group chỉ có 1 station (node_type=STATION): bước 1 bỏ qua, chạy bước 2 -> 3

    Tuy vậy, ta vẫn cần re-check để đảm bảo tính chính xác (phòng trường hợp dữ
    liệu thay đổi giữa escalation và RCA):
    - Nếu 1 station trở thành partial-down -> không báo sync, tiếp tục bước 2/3
    - Nếu 1 station ra khỏi window -> không báo sync, tiếp tục bước 2/3
    """
    try:
        stations = [get_station_full_status(sid) for sid in group.station_site_ids]
    except ValueError as e:
        logger.error("RCA node=%s: lỗi lấy station status: %s", group.node_code, e)
        return NodeRCAResult(
            node_code=group.node_code,
            node_name=group.node_name,
            node_type=group.node_type,
            station_codes=[],
            conclusion=CONCLUSION_ISOLATED,
        )

    station_codes = sorted(s["site_code"] for s in stations)
    power_affected_codes = sorted(s["site_code"] for s in stations if s["power_affected"])

    result = NodeRCAResult(
        node_code=group.node_code,
        node_name=group.node_name,
        node_type=group.node_type,
        station_codes=station_codes,
        power_affected_codes=power_affected_codes,
    )

    # Bước 1: kiểm tra xem có >1 station và đều full-down + trong window không
    if len(stations) > 1:
        # Re-check: tất cả phải full-down
        if all(s["comm_status"] == FULL_DOWN for s in stations):
            starts = [s["earliest_loss_comm_start"] for s in stations]
            earliest, latest = min(starts), max(starts)
            if (latest - earliest) <= timedelta(minutes=NODE_SYNC_WINDOW_MINUTES):
                result.conclusion = CONCLUSION_SYNC_PARENT
                result.step1_evidence = {
                    "count": len(stations),
                    "earliest": earliest,
                    "latest": latest,
                }
                logger.info(
                    "RCA node=%s -> %s (bước 1, %d trạm)",
                    group.node_code,
                    result.conclusion,
                    len(stations),
                )
                return result
            else:
                logger.warning(
                    "RCA node=%s: re-check bước 1 thất bại (out of window: %s)",
                    group.node_code,
                    latest - earliest,
                )
        else:
            logger.warning(
                "RCA node=%s: re-check bước 1 thất bại (có station partial-down)",
                group.node_code,
            )

    # Bước 2: power_fail của BẤT KỲ device nào trong node
    site_ids = [s["site_id"] for s in stations]
    earliest_overall = min(s["earliest_loss_comm_start"] for s in stations)

    step2 = _step2_power_fail_multi(site_ids, earliest_overall)
    if step2:
        result.conclusion = CONCLUSION_POWER_FAIL
        result.step2_evidence = step2
        logger.info("RCA node=%s -> %s (bước 2)", group.node_code, result.conclusion)
        return result

    # Bước 3: neighbor của toàn node
    step3 = _step3_neighbor_power_multi(site_ids)
    if step3 and step3["affected_count"] > 0:
        result.conclusion = CONCLUSION_AREA_POWER
        result.step3_evidence = step3
        logger.info("RCA node=%s -> %s (bước 3)", group.node_code, result.conclusion)
        return result

    result.conclusion = CONCLUSION_ISOLATED
    logger.info(
        "RCA node=%s -> %s (không bước nào có bằng chứng)",
        group.node_code,
        result.conclusion,
    )
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
            logger.info(
                "RCA station=%s -> %s (bước 1)", station["site_code"], result.conclusion
            )
            return result

    step2 = _step2_power_fail(station)
    if step2:
        result.conclusion = CONCLUSION_POWER_FAIL
        result.step2_evidence = step2
        logger.info(
            "RCA station=%s -> %s (bước 2)", station["site_code"], result.conclusion
        )
        return result

    step3 = _step3_neighbor_power(station)
    if step3 and step3["affected_count"] > 0:
        result.conclusion = CONCLUSION_AREA_POWER
        result.step3_evidence = step3
        logger.info(
            "RCA station=%s -> %s (bước 3)", station["site_code"], result.conclusion
        )
        return result

    result.conclusion = CONCLUSION_ISOLATED
    logger.info(
        "RCA station=%s -> %s (không bước nào có bằng chứng)",
        station["site_code"],
        result.conclusion,
    )
    return result
