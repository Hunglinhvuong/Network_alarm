import asyncio
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from .registry import command
from .common import is_authorized, UNAUTHORIZED_MSG
from escalation.engine import compute_escalation
from escalation.station_status import (
    get_fully_down_stations,
    get_partial_loss_comm_devices,
    get_station_loss_comm_breakdown,
)


def _load_status():
    breakdown = get_station_loss_comm_breakdown()
    affected = get_fully_down_stations(breakdown)
    partial = get_partial_loss_comm_devices(breakdown)
    groups = compute_escalation(affected) if affected else []
    return affected, partial, groups


@command("status", "Tổng quan số trạm/thiết bị đang mất liên lạc")
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    affected, partial, groups = await asyncio.to_thread(_load_status)

    if not affected and not partial:
        await update.message.reply_text("✅ Hiện không có trạm/thiết bị nào mất liên lạc.")
        return

    lines = []
    if affected:
        lines.append(f"🔴 {len(affected)} trạm mất liên lạc toàn bộ — gộp thành {len(groups)} cảnh báo:")
        for g in groups:
            lines.append(
                f"- <b>{escape(str(g.node_name))}</b> "
                f"({escape(str(g.node_code))}, {escape(str(g.node_type))}): "
                f"{len(g.station_site_ids)} trạm"
            )
    if partial:
        if lines:
            lines.append("")
        lines.append(f"🟠 {len(partial)} thiết bị mất liên lạc riêng lẻ (trạm chưa down toàn bộ):")
        for d in partial:
            lines.append(
                f"- {escape(str(d['device_type']))} ({escape(str(d['device_code']))}) "
                f"tại {escape(str(d['site_name']))} ({escape(str(d['site_code']))})"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
