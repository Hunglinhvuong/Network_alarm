"""
Soạn text cảnh báo Telegram (HTML parse_mode). Tách riêng khỏi notifier.py để dễ
đổi format mà không đụng logic gửi.

2 luồng:
  - Station full-down (mọi device loss_comm) -> gộp qua escalation.EscalationGroup,
    RCA chạy MỘT LẦN cho cả node cha (rca.engine.analyze_group) — chỉ 1 kết luận
    cho toàn node, không liệt kê phân tích riêng từng station con.
  - Station partial-down (1 phần device) -> cảnh báo riêng theo TỪNG STATION, RCA
    chỉ chạy bước 2 + 3 (rca.engine.analyze_station).

THEO MÚIPLÁN GIỜ: tất cả datetime.now() được thay bằng local_now() để dùng múi giờ
APP_TIMEZONE thay vì UTC của hệ thống.
"""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from config.settings import APP_TIMEZONE
from escalation.engine import EscalationGroup
from escalation.station_status import get_station_full_status
from rca.engine import NodeRCAResult, StationRCAResult

_ICON_DOWN = "🔴"
_ICON_TRANS_DOWN = "🚨"
_ICON_PARTIAL = "🟠"
_ICON_UP = "🟢"
_ICON_TRANS = "📡"
_ICON_STATION = "🗼"


def _html(value) -> str:
    return escape(str(value))


def local_now() -> datetime:
    """Trả về datetime hiện tại theo múi giờ APP_TIMEZONE.
    
    Dùng hàm này thay vì datetime.now() để đảm bảo tất cả timestamp trong alert
    messages đều theo cùng múi giờ cục bộ.
    """
    return datetime.now(ZoneInfo(APP_TIMEZONE))


def _collect_device_types_from_station(station: dict | None) -> list[str]:
    if not isinstance(station, dict):
        return []

    return sorted(
        {
            device.get("device_type") or device.get("type")
            for device in station.get("devices", [])
            if device.get("device_type") is not None or device.get("type") is not None
        }
    )


def format_down_alert_single(group: EscalationGroup, rca_result: NodeRCAResult) -> str:
    """Cảnh báo khi chỉ có 1 station full-down, không có sync node cha liên quan."""
    icon = _ICON_STATION
    title = f"{_ICON_DOWN} <b>MẤT LIÊN LẠC TOÀN BỘ TRẠM</b>"

    station = getattr(rca_result, "station", None)
    if not isinstance(station, dict) and len(group.station_site_ids) == 1:
        site_id = next(iter(group.station_site_ids))
        try:
            station = get_station_full_status(site_id)
        except ValueError:
            station = None

    device_types = _collect_device_types_from_station(station)
    device_label = ", ".join(device_types) if device_types else "không xác định"
    lines = [
        f"{title} {icon} <b>{_html(group.node_name)}</b> ({_html(group.node_code)})",
        f"Loại node: {_html(group.node_type)}",
        f"Thiết bị mất liên lạc: {_html(device_label)}",
        "Trạm: " + ", ".join(_html(code) for code in sorted(group.station_site_codes)),
        "",
        rca_result.to_text(),
        "",
        f"Thời điểm phát hiện: {local_now().strftime('%Y-%m-%d %H:%M:%S')} ({APP_TIMEZONE})",
    ]
    return "\n".join(lines)


def format_down_alert_node(group: EscalationGroup, rca_result: NodeRCAResult) -> str:
    """Cảnh báo khi 1 node truyền dẫn cha mất liên lạc toàn bộ nhiều station cùng nhánh."""
    icon = _ICON_TRANS
    title = f"{_ICON_TRANS_DOWN} <b>MẤT LIÊN LẠC TOÀN BỘ NODE TRUYỀN DẪN</b>"
    lines = [
        f"{title} {icon} <b>{_html(group.node_name)}</b> ({_html(group.node_code)})",
        f"Loại node: {_html(group.node_type)}",
        f"Số trạm ảnh hưởng: {len(group.station_site_ids)}",
        "Trạm: " + ", ".join(_html(code) for code in sorted(group.station_site_codes)),
        "",
        rca_result.to_text(),
        "",
        f"Thời điểm phát hiện: {local_now().strftime('%Y-%m-%d %H:%M:%S')} ({APP_TIMEZONE})",
    ]
    return "\n".join(lines)


def format_down_alert(group: EscalationGroup, rca_result: NodeRCAResult) -> str:
    """Compatibility wrapper: chọn formatter theo loại group."""
    if group.node_type == "STATION" and len(group.station_site_ids) == 1:
        return format_down_alert_single(group, rca_result)
    return format_down_alert_node(group, rca_result)


def format_recovered_alert(group: EscalationGroup) -> str:
    icon = _ICON_STATION if group.node_type == "STATION" else _ICON_TRANS
    lines = [
        f"{_ICON_UP} <b>ĐÃ KHÔI PHỤC</b> {icon} <b>{_html(group.node_name)}</b> ({_html(group.node_code)})",
        f"Số trạm đã khôi phục: {len(group.station_site_ids)}",
        "Trạm: " + ", ".join(_html(code) for code in sorted(group.station_site_codes)),
        "",
        f"Thời điểm: {local_now().strftime('%Y-%m-%d %H:%M:%S')} ({APP_TIMEZONE})",
    ]
    return "\n".join(lines)


def format_partial_station_alert(rca_result: StationRCAResult) -> str:
    """Station chỉ mất liên lạc 1 phần device -> báo riêng, RCA đã bỏ qua bước 1."""
    s = rca_result.station
    lines = [
        f"{_ICON_PARTIAL} <b>MẤT LIÊN LẠC MỘT PHẦN</b> 🗼 <b>{_html(s['site_name'])}</b> ({_html(s['site_code'])})",
        "Thiết bị ảnh hưởng: " + ", ".join(_html(value) for value in s["affected_device_types"]),
        "",
        rca_result.to_text(),
        "",
        f"Thời điểm phát hiện: {local_now().strftime('%Y-%m-%d %H:%M:%S')} ({APP_TIMEZONE})",
    ]
    return "\n".join(lines)


def format_partial_station_recovered(station: dict) -> str:
    """station: {site_code, site_name} — snapshot lần trước (đã khôi phục toàn bộ, không còn partial)."""
    lines = [
        f"{_ICON_UP} <b>ĐÃ KHÔI PHỤC</b> 📶 <b>{_html(station['site_name'])}</b> ({_html(station['site_code'])})",
        "",
        f"Thời điểm: {local_now().strftime('%Y-%m-%d %H:%M:%S')} ({APP_TIMEZONE})",
    ]
    return "\n".join(lines)
