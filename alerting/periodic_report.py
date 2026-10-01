"""Tổng hợp và lên lịch báo cáo định kỳ tình hình mất liên lạc."""
from datetime import date, datetime, time, timedelta, timezone
from html import escape
import math

from alerting.messages import local_now
from escalation.engine import compute_escalation
from escalation.station_status import get_fully_down_stations, get_station_loss_comm_breakdown


def format_periodic_report(groups: list, breakdown: dict, report_time: datetime) -> str:
    """Định dạng báo cáo, không liệt kê lại station đã gộp trong node truyền dẫn."""
    parent_groups = [group for group in groups if len(group.station_site_ids) > 1]
    covered_site_ids = {
        site_id
        for group in parent_groups
        for site_id in group.station_site_ids
    }
    full_down_count = sum(1 for info in breakdown.values() if info["all_down"])
    partial_down_count = len(breakdown) - full_down_count

    lines = [
        f"📅 <b>Báo cáo tổng hợp định kỳ vào lúc {report_time.strftime('%H:%M')}:</b>",
        f"🔸 Tổng số trạm đang có thiết bị mất liên lạc: {len(breakdown)} "
        f"(full-down: {full_down_count}, partial-down: {partial_down_count}).",
        "🔸 Danh sách các node truyền dẫn đang mất liên lạc toàn bộ:",
    ]
    if parent_groups:
        for group in sorted(parent_groups, key=lambda item: (item.node_name or item.node_code, item.node_code)):
            node_name = escape(group.node_name or group.node_code)
            node_code = escape(group.node_code)
            lines.append(
                f"   ▫️{node_name} ({node_code}): "
                f"{len(group.station_site_ids)} trạm ảnh hưởng."
            )
    else:
        lines.append("   ▫️Không có.")

    lines.append("🔸Danh sách các trạm lẻ đang mất liên lạc (ngoài các node ở trên):")
    listed_stations = [
        (site_id, info)
        for site_id, info in breakdown.items()
        if site_id not in covered_site_ids
    ]
    if listed_stations:
        for _, info in sorted(listed_stations, key=lambda item: item[1]["site_code"]):
            device_types = sorted(
                {
                    device["device_type"]
                    for device in info["down_devices"]
                    if device.get("device_type")
                }
            )
            types_label = ", ".join(escape(value) for value in device_types) or "không xác định"
            state = "full-down" if info["all_down"] else "partial-down"
            lines.append(
                f"   ▫️{escape(info['site_name'])} ({escape(info['site_code'])}) "
                f"[{state}]: {types_label}."
            )
    else:
        lines.append("   ▫️Không có.")

    return "\n".join(lines)


def build_periodic_report() -> str:
    """Tạo báo cáo từ trạng thái alarm và topology hiện tại trong DB."""
    breakdown = get_station_loss_comm_breakdown()
    return format_periodic_report(
        compute_escalation(get_fully_down_stations(breakdown)),
        breakdown,
        local_now(),
    )


def next_report_time(now: datetime, start_time: str, interval_minutes: int) -> datetime:
    """Tính mốc chạy kế tiếp, neo lịch vào giờ bắt đầu theo giờ địa phương."""
    if interval_minutes <= 0:
        raise ValueError("PERIODIC_REPORT_INTERVAL_MINUTES phải lớn hơn 0")
    try:
        start_clock = time.fromisoformat(start_time)
    except ValueError as exc:
        raise ValueError("PERIODIC_REPORT_START_TIME phải có định dạng HH:MM") from exc
    if start_clock.second or start_clock.microsecond or start_clock.tzinfo:
        raise ValueError("PERIODIC_REPORT_START_TIME phải có định dạng HH:MM")

    start_at = datetime.combine(date(2000, 1, 1), start_clock, tzinfo=now.tzinfo)
    now_utc = now.astimezone(timezone.utc)
    start_at_utc = start_at.astimezone(timezone.utc)
    if now_utc <= start_at_utc:
        return start_at

    interval_seconds = interval_minutes * 60
    elapsed_seconds = (now_utc - start_at_utc).total_seconds()
    intervals = math.ceil(elapsed_seconds / interval_seconds)
    return (start_at_utc + timedelta(minutes=intervals * interval_minutes)).astimezone(now.tzinfo)