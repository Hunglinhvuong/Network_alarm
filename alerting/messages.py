"""
Soạn text cảnh báo Telegram (HTML parse_mode) từ EscalationGroup + RCAResult.
Tách riêng khỏi notifier.py để dễ thay đổi format mà không đụng logic gửi.
"""
from datetime import datetime

from rca.engine import RCAResult
from escalation.engine import EscalationGroup

_ICON_DOWN = "🔴"
_ICON_UP = "🟢"
_ICON_TRANS = "📡"
_ICON_STATION = "🏢"


def format_down_alert(group: EscalationGroup, rca_results: list) -> str:
    icon = _ICON_STATION if group.node_type == "STATION" else _ICON_TRANS
    lines = [
        f"{_ICON_DOWN} <b>MẤT LIÊN LẠC</b> {icon} <b>{group.node_name}</b> ({group.node_code})",
        f"Loại node: {group.node_type}",
        f"Số trạm ảnh hưởng: {len(group.station_site_ids)}",
        "Trạm: " + ", ".join(sorted(group.station_site_codes)),
        "",
    ]
    if rca_results:
        lines.append("<b>Phân tích nguyên nhân (RCA):</b>")
        seen_conclusions = {}
        for r in rca_results:
            seen_conclusions.setdefault(r.conclusion_label, []).append(r.site_code)
        for label, sites in seen_conclusions.items():
            lines.append(f"- {label} ({', '.join(sites)})")
    lines.append("")
    lines.append(f"Thời điểm phát hiện: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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


_ICON_PARTIAL = "🟠"


def format_partial_device_alert(device: dict) -> str:
    """
    device: {device_code, device_type, site_code, site_name}
    Chỉ 1 phần device của station mất liên lạc (không phải toàn bộ) -> báo riêng
    theo device.type + station.site_name, KHÔNG coi là station down.
    """
    lines = [
        f"{_ICON_PARTIAL} <b>MẤT LIÊN LẠC (thiết bị)</b>",
        f"Thiết bị loại <b>{device['device_type']}</b> ({device['device_code']}) tại trạm "
        f"<b>{device['site_name']}</b> ({device['site_code']}) mất liên lạc.",
        "Các thiết bị khác cùng trạm vẫn bình thường -> chưa xác định trạm mất liên lạc toàn bộ.",
        "",
        f"Thời điểm phát hiện: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines)


def format_partial_device_recovered(device: dict) -> str:
    lines = [
        f"{_ICON_UP} <b>ĐÃ KHÔI PHỤC (thiết bị)</b>",
        f"Thiết bị loại <b>{device['device_type']}</b> ({device['device_code']}) tại trạm "
        f"<b>{device['site_name']}</b> ({device['site_code']}) đã khôi phục liên lạc.",
        "",
        f"Thời điểm: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines)
