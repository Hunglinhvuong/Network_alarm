from telegram import Update
from telegram.ext import ContextTypes

from .registry import command
from .common import is_authorized, UNAUTHORIZED_MSG
from escalation.engine import get_active_loss_comm_stations, compute_escalation
from escalation.station_status import get_partial_loss_comm_devices


@command("status", "Tổng quan số trạm/thiết bị đang mất liên lạc")
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    affected = get_active_loss_comm_stations()  # chỉ station mọi device đều loss_comm
    partial = get_partial_loss_comm_devices()   # device lẻ, station chưa down toàn bộ

    if not affected and not partial:
        await update.message.reply_text("✅ Hiện không có trạm/thiết bị nào mất liên lạc.")
        return

    lines = []
    if affected:
        groups = compute_escalation()
        lines.append(f"🔴 {len(affected)} trạm mất liên lạc toàn bộ — gộp thành {len(groups)} cảnh báo:")
        for g in groups:
            lines.append(f"- <b>{g.node_name}</b> ({g.node_code}, {g.node_type}): {len(g.station_site_ids)} trạm")
    if partial:
        if lines:
            lines.append("")
        lines.append(f"🟠 {len(partial)} thiết bị mất liên lạc riêng lẻ (trạm chưa down toàn bộ):")
        for d in partial:
            lines.append(f"- {d['device_type']} ({d['device_code']}) tại {d['site_name']} ({d['site_code']})")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
