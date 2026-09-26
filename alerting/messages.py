"""
Soạn text cảnh báo Telegram (HTML parse_mode). Tách riêng khỏi notifier.py để dễ
đổi format mà không đụng logic gửi.

2 luồng:
  - Station full-down (mọi device loss_comm) -> gộp qua escalation.EscalationGroup,
    RCA chạy MỘT LẦN cho cả node cha (rca.engine.analyze_group) — chỉ 1 kết luận
    cho toàn node, không liệt kê phân tích riêng từng station con.
  - Station partial-down (1 phần device) -> cảnh báo riêng theo TỪNG STATION, RCA
    chỉ chạy bước 2 + 3 (rca.engine.analyze_station).
"""
from datetime import datetime

from escalation.engine import EscalationGroup
from rca.engine import NodeRCAResult, StationRCAResult

_ICON_DOWN = "🔴"
_ICON_PARTIAL = "🟠"
_ICON_UP = "🟢"
_ICON_TRANS = "📡"
_ICON_STATION = "🏢"


def format_down_alert(group: EscalationGroup, rca_result: NodeRCAResult) -> str:
    icon = _ICON_STATION if group.node_type == "STATION" else _ICON_TRANS
    lines = [
        f"{_ICON_DOWN} <b>MẤT LIÊN LẠC TOÀN BỘ</b> {icon} <b>{group.node_name}</b> ({group.node_code})",
        f"Loại node: {group.node_type}",
        f"Số trạm ảnh hưởng: {len(group.station_site_ids)}",
        "Trạm: " + ", ".join(sorted(group.station_site_codes)),
        "",
        rca_result.to_text(),
        "",
        f"Thời điểm phát hiện: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines)


def format_recovered_alert(group: EscalationGroup) -> str:
    icon = _ICON_STATION if group.node_type == "STATION" else _ICON_TRANS
    lines = [
        f"{_ICON_UP} <b>ĐÃ KHÔI PHỤC</b> {icon} <b>{group.node_name}</b> ({group.node_code})",
        f"Số trạm đã khôi phục: {len(group.station_site_ids)}",
        "Trạm: " + ", ".join(sorted(group.station_site_codes)),
        "",
        f"Thời điểm: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines)


def format_partial_station_alert(rca_result: StationRCAResult) -> str:
    """Station chỉ mất liên lạc 1 phần device -> báo riêng, RCA đã bỏ qua bước 1."""
    s = rca_result.station
    lines = [
        f"{_ICON_PARTIAL} <b>MẤT LIÊN LẠC MỘT PHẦN</b> 🏢 <b>{s['site_name']}</b> ({s['site_code']})",
        "Thiết bị ảnh hưởng: " + ", ".join(s["affected_device_types"]),
        "",
        rca_result.to_text(),
        "",
        f"Thời điểm phát hiện: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines)


def format_partial_station_recovered(station: dict) -> str:
    """station: {site_code, site_name} — snapshot lần trước (đã khôi phục toàn bộ, không còn partial)."""
    lines = [
        f"{_ICON_UP} <b>ĐÃ KHÔI PHỤC</b> 🏢 <b>{station['site_name']}</b> ({station['site_code']})",
        "",
        f"Thời điểm: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines)
