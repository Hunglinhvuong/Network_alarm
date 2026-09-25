from telegram import Update
from telegram.ext import ContextTypes

from .registry import command
from .common import is_authorized, UNAUTHORIZED_MSG
from db.connection import get_cursor


@command("tra", "Tra cứu trạng thái trạm/thiết bị theo mã — vd: /tra ST001")
async def tra_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    if not context.args:
        await update.message.reply_text("Cú pháp: /tra <site_code hoặc device_code>")
        return

    code = context.args[0].strip()
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT s.site_code, s.site_name, s.status AS site_status,
                   d.device_code, d.device_name, d.type AS device_type,
                   ae.alarm_name, ae.status AS alarm_status, ae.start_time
            FROM station s
            JOIN device d ON d.site_id = s.site_id
            LEFT JOIN alarm_event ae ON ae.device_id = d.device_id AND ae.status = 'active'
            WHERE s.site_code = %(code)s OR d.device_code = %(code)s
            ORDER BY d.device_code
            """,
            {"code": code},
        )
        rows = cur.fetchall()

    if not rows:
        await update.message.reply_text(f"Không tìm thấy trạm/thiết bị với mã: {code}")
        return

    site = rows[0]
    lines = [f"<b>{site['site_name']}</b> ({site['site_code']}) — trạng thái trạm: {site['site_status']}"]
    for r in rows:
        if r["alarm_name"]:
            status_txt = f"⚠️ {r['alarm_name']} — từ {r['start_time']}"
        else:
            status_txt = "✅ bình thường"
        lines.append(f"- {r['device_code']} ({r['device_type']}): {status_txt}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
