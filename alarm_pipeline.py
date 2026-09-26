"""
Kết nối escalation + RCA + alerting thành 1 pipeline chạy mỗi chu kỳ poll.

2 nhánh, đều chạy RCA theo STATION/NODE (không theo alarm/device):
  - full_down: các station full-down được escalation gộp lên 1 node cha lớn nhất
    (EscalationGroup). RCA chạy ĐÚNG 1 LẦN cho cả node đó (rca.engine.analyze_group)
    — không phân tích/gửi kết quả riêng cho từng station con bên trong.
  - partial_down: station báo riêng, không vào cây escalation -> RCA theo từng
    station (rca.engine.analyze_station), bỏ qua bước 1.

Giữ trạng thái lần chạy trước (in-memory, theo node_id cho full-down và site_id
cho partial-down) để chỉ gửi cảnh báo khi có thay đổi thật sự.

Xử lý escalate-lên-cao-hơn (node A ⊂ node B): khi node A đang được cảnh báo mà sau
đó toàn bộ (hoặc thêm) nhánh dưới node B cũng down, escalation sẽ gộp lại thành 1
group mới ở node B -> node A biến mất khỏi current_map dù các station của A VẪN
đang down (chỉ là được báo cáo ở cấp cao hơn). Trường hợp này KHÔNG được coi là
"đã khôi phục" A — chỉ gửi "đã khôi phục" khi station của group cũ thực sự không
còn nằm trong bất kỳ group full-down nào ở chu kỳ hiện tại.

Lưu ý: trạng thái mất khi restart tiến trình -> nếu restart giữa lúc đang có sự
cố, cảnh báo DOWN có thể gửi lại 1 lần (chấp nhận được, còn hơn im lặng bỏ sót).
"""
import logging

from escalation.engine import compute_escalation
from escalation.station_status import get_partial_down_stations
from rca.engine import analyze_group, analyze_station
from alerting.messages import (
    format_down_alert,
    format_recovered_alert,
    format_partial_station_alert,
    format_partial_station_recovered,
)
from alerting.notifier import send_alert

logger = logging.getLogger(__name__)


class AlarmPipeline:
    def __init__(self):
        self._last_groups = {}   # node_id -> EscalationGroup (full-down, lần chạy trước)
        self._last_partial = {}  # site_id -> {site_code, site_name} (partial-down, lần chạy trước)

    def run_cycle(self):
        current_groups = compute_escalation()
        current_map = {g.node_id: g for g in current_groups}
        current_partial = get_partial_down_stations()

        # union toàn bộ station đang full-down ở chu kỳ này (bất kể thuộc group nào)
        # -> dùng để phân biệt "thật sự khôi phục" với "chỉ escalate lên node cha cao hơn"
        currently_down_station_ids = set()
        for g in current_map.values():
            currently_down_station_ids |= g.station_site_ids

        new_ids = set(current_map) - set(self._last_groups)
        resolved_ids = set(self._last_groups) - set(current_map)

        for node_id in new_ids:
            group = current_map[node_id]
            try:
                rca_result = analyze_group(group)
            except ValueError:
                logger.exception("RCA lỗi cho node=%s, gửi cảnh báo không kèm RCA", group.node_code)
                continue
            text = format_down_alert(group, rca_result)
            logger.info("Gửi cảnh báo DOWN: node=%s (%s)", group.node_code, group.node_type)
            send_alert(text)

        for node_id in resolved_ids:
            old_group = self._last_groups[node_id]
            if old_group.station_site_ids & currently_down_station_ids:
                # station của node này vẫn đang down, chỉ là đã escalate lên node
                # cha cao hơn (đã/sẽ được báo ở group mới) -> KHÔNG gửi "khôi phục"
                logger.info("Node=%s biến mất khỏi group nhưng station vẫn down -> bỏ qua (đã escalate lên cao hơn)", old_group.node_code)
                continue
            text = format_recovered_alert(old_group)
            logger.info("Gửi cảnh báo RECOVERED: node=%s", old_group.node_code)
            send_alert(text)

        # --- partial-down (theo station, không vào cây escalation) ---
        new_partial_ids = set(current_partial) - set(self._last_partial)
        resolved_partial_ids = set(self._last_partial) - set(current_partial)

        for site_id in new_partial_ids:
            try:
                rca_result = analyze_station(site_id)
            except ValueError:
                logger.exception("RCA lỗi cho site_id=%s (partial-down), bỏ qua chu kỳ này", site_id)
                continue
            text = format_partial_station_alert(rca_result)
            logger.info("Gửi cảnh báo DOWN (partial): station=%s", rca_result.site_code)
            send_alert(text)

        for site_id in resolved_partial_ids:
            if site_id in currently_down_station_ids:
                # station đã "chuyển cấp" thành full-down (đang nằm trong 1 group) ->
                # đã báo ở nhánh escalation phía trên, không gửi "đã khôi phục" partial
                # gây hiểu nhầm.
                continue
            station = self._last_partial[site_id]
            text = format_partial_station_recovered(station)
            logger.info("Gửi cảnh báo RECOVERED (partial): station=%s", station["site_code"])
            send_alert(text)

        self._last_groups = current_map
        self._last_partial = current_partial

        return {
            "new_alerts": len(new_ids),
            "resolved_alerts": len(resolved_ids),
            "active_groups": len(current_map),
            "new_partial_alerts": len(new_partial_ids),
            "resolved_partial_alerts": len(resolved_partial_ids),
            "active_partial_stations": len(current_partial),
        }
