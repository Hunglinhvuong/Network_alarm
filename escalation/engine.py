"""
Escalation logic:
  Khi nhiều station con cùng mất liên lạc (loss_comm active) trong cùng 1 nhánh,
  chỉ gửi 1 cảnh báo cho node cấp cao nhất (gần root nhất) mà TOÀN BỘ station con
  cháu của nó đều đang down ĐỒng bộ (full-down + nằm trong cửa sổ ±NODE_SYNC_WINDOW_MINUTES),
  suppress cảnh báo của các station con cháu bên dưới.

Thuật toán:
  Với mỗi station đang down, đi từ station đó lên root theo path_to_root. Vì tập
  descendant-station của 1 node chỉ tăng (hoặc giữ nguyên) khi đi lên cây, một khi
  điều kiện "toàn bộ descendant đều down đồng bộ" bị phá vỡ ở 1 node thì mọi node
  cao hơn cũng chắc chắn phá vỡ -> có thể dừng sớm (break).

Điều kiện "down đồng bộ" = full-down + nằm trong ±NODE_SYNC_WINDOW_MINUTES:
  - Tất cả device của station đều loss_comm active (full-down)
  - Thời điểm mất liên lạc (earliest_loss_comm_start) nằm trong window so với station
    mất liên lạc sớm nhất trong node
  Nếu có 1 station partial-down hoặc ngoài window -> node đó KHÔNG được xem là "sync"
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from topology.path_to_root import get_path_to_root
from topology.descendants import get_descendant_stations
from escalation.station_status import get_fully_down_stations, get_station_full_status, FULL_DOWN
from config.settings import NODE_SYNC_WINDOW_MINUTES

logger = logging.getLogger(__name__)


@dataclass
class EscalationGroup:
    node_id: int
    node_code: str
    node_name: str
    node_type: str
    station_site_ids: set = field(default_factory=set)
    station_site_codes: set = field(default_factory=set)
    alarm_ids: set = field(default_factory=set)

    @property
    def key(self):
        return self.node_id


def get_active_loss_comm_stations() -> dict:
    """
    Trả về dict site_id -> {site_code, site_name, node_id, alarm_ids: set} cho các
    station đang MẤT LIÊN LẠC TOÀN BỘ (tất cả device của station đều loss_comm
    active — theo quy tắc thống nhất). Station chỉ down 1 phần device không xuất
    hiện ở đây, xem escalation.station_status.get_partial_loss_comm_devices().
    """
    return get_fully_down_stations()


# cache trong 1 lần compute để tránh query lặp descendant cho cùng node_id
def _cached_descendants(node_id, cache):
    if node_id not in cache:
        cache[node_id] = get_descendant_stations(node_id)
    return cache[node_id]


def _is_valid_sync_group(site_ids: list) -> bool:
    """
    Kiểm tra xem 1 tập station có thỏa điều kiện "đồng bộ trong node" không:
    1. Phải > 1 station (không có thì không có gì để "đồng bộ")
    2. TẤT CẢ station phải là full-down (mọi device đều loss_comm)
    3. TẤT CẢ station phải có earliest_loss_comm_start nằm trong cửa sổ
       ±NODE_SYNC_WINDOW_MINUTES so với station mất liên lạc sớm nhất

    Nếu có 1 station không thỏa điều kiện trên -> cả node đó KHÔNG được xem là
    "sync transmission", phải tiếp tục bước 2 (power_fail) hoặc bước 3 (neighbor).
    """
    if len(site_ids) <= 1:
        return False

    try:
        stations = [get_station_full_status(sid) for sid in site_ids]
    except ValueError:
        # station không tồn tại hoặc không có loss_comm active
        return False

    # Điều kiện 2: tất cả phải full-down
    if any(s["comm_status"] != FULL_DOWN for s in stations):
        logger.debug(
            "Not a valid sync group: có station partial-down. "
            "Stations: %s",
            ", ".join(s["site_code"] for s in stations),
        )
        return False

    # Điều kiện 3: tất cả nằm trong window
    starts = [
        s["earliest_loss_comm_start"]
        for s in stations
        if s["earliest_loss_comm_start"] is not None
    ]
    if len(starts) != len(stations):
        # không có start_time (lỗi dữ liệu)
        return False

    earliest = min(starts)
    latest = max(starts)
    time_diff = latest - earliest

    if time_diff > timedelta(minutes=NODE_SYNC_WINDOW_MINUTES):
        logger.debug(
            "Not a valid sync group: ngoài window ±%d phút. "
            "Earliest: %s, Latest: %s, Diff: %s",
            NODE_SYNC_WINDOW_MINUTES,
            earliest,
            latest,
            time_diff,
        )
        return False

    logger.debug(
        "Valid sync group: %d stations, window: %s → %s (diff: %s)",
        len(stations),
        earliest,
        latest,
        time_diff,
    )
    return True


def compute_escalation() -> list:
    """
    Tính toán các EscalationGroup hiện tại dựa trên trạng thái loss_comm active
    trong DB ngay lúc gọi. Mỗi group là 1 node cấp cao nhất cần gửi cảnh báo;
    mọi station/alarm bên trong group đó coi như đã được "gộp", không cảnh báo
    riêng lẻ nữa.

    CHỈ group nào mà toàn bộ station con cháu đều:
    - full-down (tất cả device loss_comm)
    - nằm trong cửa sổ ±NODE_SYNC_WINDOW_MINUTES
    mới được escalate lên node cha cao hơn.

    Nếu 1 station không thỏa cả 2 điều kiện -> không gom lên node cha, vẫn báo riêng
    (partial hoặc isolated RCA).
    """
    affected = get_active_loss_comm_stations()
    if not affected:
        return []
    affected_ids = set(affected.keys())

    descendant_cache = {}
    groups = {}  # node_id -> EscalationGroup

    for site_id, info in affected.items():
        path = get_path_to_root(info["node_id"])
        if not path:
            logger.warning(
                "Station site_id=%s không có trong topo (path rỗng) -> escalate tại chính nó",
                site_id,
            )
            best = {
                "node_id": info["node_id"],
                "node_code": info["site_code"],
                "node_name": info["site_name"],
                "node_type": "STATION",
            }
        else:
            best = path[0]  # tối thiểu escalate ở chính station đó
            for node in path[1:]:
                desc = _cached_descendants(node["node_id"], descendant_cache)
                if not desc:
                    break

                # Tập descendant-station của node này mà đang down
                desc_affected = desc & affected_ids
                if not desc_affected:
                    break

                # Kiểm tra xem tất cả descendant down này có thỏa điều kiện
                # "đồng bộ trong node" không (full-down + trong window)
                if _is_valid_sync_group(sorted(desc_affected)):
                    best = node
                else:
                    break  # monotonic -> dừng sớm nếu không đạt đủ điều kiện

        group = groups.setdefault(
            best["node_id"],
            EscalationGroup(
                node_id=best["node_id"],
                node_code=best["node_code"],
                node_name=best["node_name"],
                node_type=best["node_type"],
            ),
        )
        group.station_site_ids.add(site_id)
        group.station_site_codes.add(info["site_code"])
        group.alarm_ids |= info["alarm_ids"]

    result = list(groups.values())
    logger.info(
        "Escalation: %d station down -> gộp thành %d nhóm cảnh báo (chỉ nhóm sync-valid)",
        len(affected_ids),
        len(result),
    )
    return result
