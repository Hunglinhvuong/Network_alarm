"""
Escalation logic:
  Khi nhiều station con cùng mất liên lạc (loss_comm active) trong cùng 1 nhánh,
  chỉ gửi 1 cảnh báo cho node cấp cao nhất (gần root nhất) mà TOÀN BỘ station con
  cháu của nó đều đang down, suppress cảnh báo của các station con cháu bên dưới.

Thuật toán:
  Với mỗi station đang down, đi từ station đó lên root theo path_to_root. Vì tập
  descendant-station của 1 node chỉ tăng (hoặc giữ nguyên) khi đi lên cây, một khi
  điều kiện "toàn bộ descendant đều down" bị phá vỡ ở 1 node thì mọi node cao hơn
  cũng chắc chắn phá vỡ -> có thể dừng sớm (break) ngay khi gặp node đầu tiên không
  thoả, không cần duyệt tiếp lên root.
"""
import logging
from dataclasses import dataclass, field

from topology.path_to_root import get_paths_to_root
from topology.descendants import get_descendant_stations_many
from escalation.station_status import get_fully_down_stations

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


def compute_escalation(affected: dict | None = None) -> list:
    """
    Tính toán các EscalationGroup hiện tại dựa trên trạng thái loss_comm active
    trong DB ngay lúc gọi. Mỗi group là 1 node cấp cao nhất cần gửi cảnh báo;
    mọi station/alarm bên trong group đó coi như đã được "gộp", không cảnh báo
    riêng lẻ nữa.
    """
    affected = affected if affected is not None else get_active_loss_comm_stations()
    if not affected:
        return []
    affected_ids = set(affected.keys())

    paths_by_station = get_paths_to_root([info["node_id"] for info in affected.values()])
    ancestor_ids = {
        node["node_id"]
        for path in paths_by_station.values()
        for node in path[1:]
    }
    descendants_by_node = get_descendant_stations_many(ancestor_ids)
    groups = {}  # node_id -> EscalationGroup

    for site_id, info in affected.items():
        path = paths_by_station.get(info["node_id"], [])
        if not path:
            logger.warning("Station site_id=%s không có trong topo (path rỗng) -> escalate tại chính nó", site_id)
            best = {"node_id": info["node_id"], "node_code": info["site_code"], "node_name": info["site_name"], "node_type": "STATION"}
        else:
            best = path[0]  # tối thiểu escalate ở chính station đó
            for node in path[1:]:
                desc = descendants_by_node.get(node["node_id"], set())
                if desc and desc <= affected_ids:
                    best = node
                else:
                    break  # monotonic -> dừng sớm

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
    logger.info("Escalation: %d station down -> gộp thành %d nhóm cảnh báo", len(affected_ids), len(result))
    return result
