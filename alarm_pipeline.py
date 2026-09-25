"""
Kết nối escalation + RCA + alerting thành 1 pipeline chạy mỗi chu kỳ poll.

Xử lý 2 nhánh song song, theo đúng quy tắc thống nhất:
  - Station mất liên lạc TOÀN BỘ (mọi device đều loss_comm) -> đi qua escalation
    (gộp lên node cấp cao nhất đang down toàn bộ) + RCA.
  - Station chỉ mất liên lạc 1 PHẦN device -> cảnh báo riêng theo từng device
    (device.type + station.site_name), không tính là station down, không vào cây
    escalation.

Giữ trạng thái lần chạy trước (in-memory) để chỉ gửi cảnh báo khi có thay đổi
(group/device MỚI xuất hiện hoặc đã biến mất) — tránh spam lại mỗi chu kỳ poll.

Lưu ý: trạng thái này mất khi restart tiến trình -> nếu restart giữa lúc đang có
sự cố, cảnh báo DOWN có thể gửi lại 1 lần (chấp nhận được, còn hơn im lặng bỏ sót).
"""
import logging

from db.connection import get_cursor
from escalation.engine import compute_escalation
from escalation.station_status import get_partial_loss_comm_devices
from rca.engine import run_rca
from alerting.messages import (
    format_down_alert,
    format_recovered_alert,
    format_partial_device_alert,
    format_partial_device_recovered,
)
from alerting.notifier import send_alert

logger = logging.getLogger(__name__)


class AlarmPipeline:
    def __init__(self):
        self._last_groups = {}   # node_id -> EscalationGroup (station full-down, lần chạy trước)
        self._last_partial = {}  # alarm_id -> device dict (station partial-down, lần chạy trước)

    def run_cycle(self):
        current_groups = compute_escalation()
        current_map = {g.node_id: g for g in current_groups}

        current_partial_list = get_partial_loss_comm_devices()
        current_partial_map = {d["alarm_id"]: d for d in current_partial_list}

        new_ids = set(current_map) - set(self._last_groups)
        resolved_ids = set(self._last_groups) - set(current_map)

        for node_id in new_ids:
            group = current_map[node_id]
            rca_results = []
            for alarm_id in group.alarm_ids:
                try:
                    rca_results.append(run_rca(alarm_id))
                except ValueError:
                    logger.exception("RCA lỗi cho alarm_id=%s, bỏ qua evidence này", alarm_id)
            text = format_down_alert(group, rca_results)
            logger.info("Gửi cảnh báo DOWN: node=%s (%s)", group.node_code, group.node_type)
            send_alert(text)

        for node_id in resolved_ids:
            group = self._last_groups[node_id]
            text = format_recovered_alert(group)
            logger.info("Gửi cảnh báo RECOVERED: node=%s", group.node_code)
            send_alert(text)

        # --- device-level partial (station chưa down toàn bộ) ---
        new_partial_ids = set(current_partial_map) - set(self._last_partial)
        resolved_partial_ids = set(self._last_partial) - set(current_partial_map)

        # alarm_id đã "chuyển cấp" thành 1 phần của station full-down -> không coi
        # là recovered riêng lẻ, đã có cảnh báo DOWN ở mức station rồi.
        escalated_alarm_ids = set()
        for g in current_map.values():
            escalated_alarm_ids |= g.alarm_ids

        for alarm_id in new_partial_ids:
            device = current_partial_map[alarm_id]
            text = format_partial_device_alert(device)
            logger.info("Gửi cảnh báo DOWN (device): %s tại %s", device["device_code"], device["site_code"])
            send_alert(text)

        for alarm_id in resolved_partial_ids:
            if alarm_id in escalated_alarm_ids:
                # device này giờ nằm trong 1 station đã down toàn bộ -> đã báo ở
                # nhánh escalation, không gửi "đã khôi phục" gây hiểu nhầm.
                continue
            device = self._last_partial[alarm_id]
            if _alarm_still_active(alarm_id):
                # vẫn active nhưng không còn "partial" theo snapshot mới (hiếm khi
                # xảy ra do race condition đọc DB) -> bỏ qua, chờ chu kỳ sau ổn định
                continue
            text = format_partial_device_recovered(device)
            logger.info("Gửi cảnh báo RECOVERED (device): %s tại %s", device["device_code"], device["site_code"])
            send_alert(text)

        self._last_groups = current_map
        self._last_partial = current_partial_map

        return {
            "new_alerts": len(new_ids),
            "resolved_alerts": len(resolved_ids),
            "active_groups": len(current_map),
            "new_partial_alerts": len(new_partial_ids),
            "resolved_partial_alerts": len(resolved_partial_ids),
            "active_partial_devices": len(current_partial_map),
        }


def _alarm_still_active(alarm_id: int) -> bool:
    with get_cursor(dict_cursor=False, commit=False) as cur:
        cur.execute("SELECT 1 FROM alarm_event WHERE alarm_id = %(alarm_id)s AND status = 'active'", {"alarm_id": alarm_id})
        return cur.fetchone() is not None
