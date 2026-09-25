"""
RCA Engine cho alarm loss_comm, chạy tuần tự 3 bước theo đúng thứ tự trong yêu cầu
gốc, DỪNG NGAY khi 1 bước có bằng chứng (không chạy các bước sau):
  1. Đồng bộ mất liên lạc trong cùng node cha (cửa sổ ±NODE_SYNC_WINDOW_MINUTES phút)
  2. Mất điện liên quan — quy tắc: nếu BẤT KỲ device nào trong CÙNG STATION có
     power_fail thì coi như ảnh hưởng chung cả station (không chỉ riêng device
     đang loss_comm). Trong POWER_FAIL_LOOKBACK_HOURS giờ gần nhất tính từ NOW,
     và xảy ra trước loss_comm <= POWER_FAIL_CORRELATION_HOURS giờ.
  3. Neighbor_edge — trạm lân cận (đã build sẵn theo NEIGHBOR_DISTANCE_KM) có cùng
     mất liên lạc không
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from db.connection import get_cursor
from config.settings import (
    NODE_SYNC_WINDOW_MINUTES,
    POWER_FAIL_LOOKBACK_HOURS,
    POWER_FAIL_CORRELATION_HOURS,
)

logger = logging.getLogger(__name__)

CONCLUSION_SYNC_PARENT = "sync_parent_node"      # nghi do node cha / tuyến truyền dẫn
CONCLUSION_POWER_FAIL = "power_fail"             # nghi do mất điện
CONCLUSION_AREA_OUTAGE = "area_outage"           # nghi do sự cố diện rộng (khu vực)
CONCLUSION_ISOLATED = "isolated_device"          # không tìm được liên hệ -> lỗi cục bộ thiết bị

CONCLUSION_LABEL_VI = {
    CONCLUSION_SYNC_PARENT: "Nghi ngờ sự cố tuyến truyền dẫn / node cha (nhiều trạm cùng nhánh mất liên lạc đồng thời)",
    CONCLUSION_POWER_FAIL: "Nghi ngờ do mất điện",
    CONCLUSION_AREA_OUTAGE: "Nghi ngờ sự cố diện rộng (các trạm lân cận cũng mất liên lạc)",
    CONCLUSION_ISOLATED: "Chưa xác định được liên hệ -> có thể lỗi cục bộ tại thiết bị/trạm",
}


@dataclass
class RCAResult:
    alarm_id: int
    device_id: int
    site_id: int
    site_code: str
    start_time: datetime
    conclusion: str
    sync_siblings: list = field(default_factory=list)
    power_fail_evidence: dict = None
    neighbor_down: list = field(default_factory=list)

    @property
    def conclusion_label(self) -> str:
        return CONCLUSION_LABEL_VI.get(self.conclusion, self.conclusion)

    def to_text(self) -> str:
        lines = [f"Kết luận RCA: {self.conclusion_label}"]
        if self.sync_siblings:
            names = ", ".join(s["site_code"] for s in self.sync_siblings)
            lines.append(f"- Đồng bộ cùng node cha: {len(self.sync_siblings)} trạm khác cũng mất liên lạc ({names})")
        if self.power_fail_evidence:
            pf = self.power_fail_evidence
            lines.append(
                f"- Mất điện liên quan: thiết bị {pf['device_code']} ({pf['device_type']}) lúc {pf['start_time']}"
                f" (trước loss_comm {pf['hours_before']:.1f}h) — quy về ảnh hưởng chung cả trạm"
            )
        if self.neighbor_down:
            names = ", ".join(s["site_code"] for s in self.neighbor_down)
            lines.append(f"- Trạm lân cận cùng mất liên lạc: {len(self.neighbor_down)} trạm ({names})")
        return "\n".join(lines)


def _get_alarm_context(cur, alarm_id: int):
    cur.execute(
        """
        SELECT ae.alarm_id, ae.device_id, ae.alarm_name, ae.start_time,
               d.site_id, s.site_code, s.site_id AS station_node_id
        FROM alarm_event ae
        JOIN device d ON d.device_id = ae.device_id
        JOIN station s ON s.site_id = d.site_id
        WHERE ae.alarm_id = %(alarm_id)s
        """,
        {"alarm_id": alarm_id},
    )
    return cur.fetchone()


def _step1_sync_parent(cur, ctx) -> list:
    """Các trạm khác cùng node cha (topo_link) có loss_comm active trong cửa sổ ±N phút quanh start_time."""
    window_start = ctx["start_time"] - timedelta(minutes=NODE_SYNC_WINDOW_MINUTES)
    window_end = ctx["start_time"] + timedelta(minutes=NODE_SYNC_WINDOW_MINUTES)
    cur.execute(
        """
        SELECT DISTINCT s2.site_id, s2.site_code, ae2.start_time
        FROM topo_link tl_self
        JOIN topo_link tl_sib
            ON tl_sib.parent_node_id = tl_self.parent_node_id
           AND tl_sib.child_node_id != tl_self.child_node_id
           AND tl_sib.is_active = TRUE
        JOIN station s2 ON s2.site_id = tl_sib.child_node_id
        JOIN device d2 ON d2.site_id = s2.site_id
        JOIN alarm_event ae2
            ON ae2.device_id = d2.device_id
           AND ae2.alarm_name = 'loss_comm'
           AND ae2.status = 'active'
        WHERE tl_self.child_node_id = %(station_node_id)s
          AND tl_self.is_active = TRUE
          AND tl_self.parent_node_id IS NOT NULL
          AND ae2.start_time BETWEEN %(window_start)s AND %(window_end)s
        """,
        {"station_node_id": ctx["station_node_id"], "window_start": window_start, "window_end": window_end},
    )
    return [dict(r) for r in cur.fetchall()]


def _step2_power_fail(cur, ctx) -> dict:
    """
    power_fail trên BẤT KỲ device nào trong CÙNG STATION (quy tắc: 1 device
    power_fail -> coi như ảnh hưởng toàn bộ device của station đó), trong
    POWER_FAIL_LOOKBACK_HOURS giờ gần nhất tính từ NOW, và xảy ra trước loss_comm
    <= POWER_FAIL_CORRELATION_HOURS giờ. Lấy bản ghi power_fail gần loss_comm nhất.
    """
    now = datetime.now()
    lookback_start = now - timedelta(hours=POWER_FAIL_LOOKBACK_HOURS)
    correlation_start = ctx["start_time"] - timedelta(hours=POWER_FAIL_CORRELATION_HOURS)

    cur.execute(
        """
        SELECT ae.alarm_id, ae.start_time, d.device_id, d.device_code, d.type AS device_type
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
            "site_id": ctx["site_id"],
            "lookback_start": lookback_start,
            "loss_comm_start": ctx["start_time"],
            "correlation_start": correlation_start,
        },
    )
    row = cur.fetchone()
    if row is None:
        return None
    hours_before = (ctx["start_time"] - row["start_time"]).total_seconds() / 3600.0
    return {
        "alarm_id": row["alarm_id"],
        "device_id": row["device_id"],
        "device_code": row["device_code"],
        "device_type": row["device_type"],
        "start_time": row["start_time"],
        "hours_before": hours_before,
    }


def _step3_neighbor(cur, ctx) -> list:
    """Các trạm trong bán kính (neighbor_edge đã build sẵn) đang loss_comm active."""
    cur.execute(
        """
        SELECT DISTINCT s2.site_id, s2.site_code, ne.distance_km
        FROM neighbor_edge ne
        JOIN station s2
            ON s2.site_id = CASE WHEN ne.site_id_a = %(site_id)s THEN ne.site_id_b ELSE ne.site_id_a END
        JOIN device d2 ON d2.site_id = s2.site_id
        JOIN alarm_event ae2
            ON ae2.device_id = d2.device_id
           AND ae2.alarm_name = 'loss_comm'
           AND ae2.status = 'active'
        WHERE (ne.site_id_a = %(site_id)s OR ne.site_id_b = %(site_id)s)
        """,
        {"site_id": ctx["site_id"]},
    )
    return [dict(r) for r in cur.fetchall()]


def _decide_conclusion(sync_siblings, power_fail_evidence, neighbor_down) -> str:
    """Giữ lại cho nơi khác/test muốn suy luận conclusion độc lập; run_rca() không gọi hàm này nữa
    (đã inline logic short-circuit trực tiếp để đảm bảo bước sau THẬT SỰ không được thực thi)."""
    if sync_siblings:
        return CONCLUSION_SYNC_PARENT
    if power_fail_evidence:
        return CONCLUSION_POWER_FAIL
    if neighbor_down:
        return CONCLUSION_AREA_OUTAGE
    return CONCLUSION_ISOLATED


def run_rca(alarm_id: int) -> RCAResult:
    """
    Chạy tuần tự 3 bước cho 1 alarm loss_comm, DỪNG NGAY khi 1 bước phát hiện bằng
    chứng (short-circuit) — các bước sau không chạy nữa:
      Bước 1 (sync node cha) có kết quả -> dừng, không chạy bước 2/3.
      Bước 1 rỗng -> chạy bước 2 (power_fail); có kết quả -> dừng, không chạy bước 3.
      Bước 1, 2 đều rỗng -> chạy bước 3 (neighbor_edge).
    Ném ValueError nếu alarm_id không tồn tại hoặc không phải loss_comm.
    """
    with get_cursor(dict_cursor=True, commit=False) as cur:
        ctx = _get_alarm_context(cur, alarm_id)
        if ctx is None:
            raise ValueError(f"Không tìm thấy alarm_id={alarm_id}")
        if ctx["alarm_name"] != "loss_comm":
            raise ValueError(f"RCA chỉ áp dụng cho alarm loss_comm, alarm_id={alarm_id} là '{ctx['alarm_name']}'")

        sync_siblings = _step1_sync_parent(cur, ctx)
        power_fail_evidence = None
        neighbor_down = []

        if sync_siblings:
            conclusion = CONCLUSION_SYNC_PARENT
        else:
            power_fail_evidence = _step2_power_fail(cur, ctx)
            if power_fail_evidence:
                conclusion = CONCLUSION_POWER_FAIL
            else:
                neighbor_down = _step3_neighbor(cur, ctx)
                conclusion = CONCLUSION_AREA_OUTAGE if neighbor_down else CONCLUSION_ISOLATED

    result = RCAResult(
        alarm_id=ctx["alarm_id"],
        device_id=ctx["device_id"],
        site_id=ctx["site_id"],
        site_code=ctx["site_code"],
        start_time=ctx["start_time"],
        conclusion=conclusion,
        sync_siblings=sync_siblings,
        power_fail_evidence=power_fail_evidence,
        neighbor_down=neighbor_down,
    )
    logger.info("RCA alarm_id=%s site=%s -> %s", alarm_id, ctx["site_code"], conclusion)
    return result


def check_power_fail_only(alarm_id: int) -> dict:
    """
    Chỉ chạy BƯỚC 2 (power_fail liên quan, theo cùng quy tắc "1 device power_fail
    ảnh hưởng cả station" như run_rca) cho 1 alarm bất kỳ — dùng cho case
    partial-device (station chưa down toàn bộ): vẫn phân tích nguyên nhân mất
    điện, nhưng CỐ Ý bỏ qua bước 1 (sync node cha) vì lý do "truyền dẫn node cha"
    chỉ có ý nghĩa ở mức station down toàn bộ, không áp dụng cho 1 device lẻ.
    Trả về dict evidence (như power_fail_evidence trong RCAResult) hoặc None.
    Ném ValueError nếu alarm_id không tồn tại.
    """
    with get_cursor(dict_cursor=True, commit=False) as cur:
        ctx = _get_alarm_context(cur, alarm_id)
        if ctx is None:
            raise ValueError(f"Không tìm thấy alarm_id={alarm_id}")
        return _step2_power_fail(cur, ctx)
