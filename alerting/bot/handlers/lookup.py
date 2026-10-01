import asyncio
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from .registry import command
from .common import is_authorized, UNAUTHORIZED_MSG
from db.connection import get_cursor


def _lookup_rows(code):
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT s.site_code, s.site_name, s.status AS site_status,
                   d.device_code, d.device_name, d.type AS device_type,
                   ae.alarm_name, ae.status AS alarm_status, ae.start_time
            FROM station s
            JOIN device d ON d.site_id = s.site_id
            LEFT JOIN alarm_event ae ON ae.device_id = d.device_id AND ae.status = 'active'
            WHERE LOWER(s.site_code) = LOWER(%(code)s)
               OR LOWER(d.device_code) = LOWER(%(code)s)
            ORDER BY d.device_code
            """,
            {"code": code},
        )
        return cur.fetchall()


@command("check", "Tra cứu trạng thái trạm/thiết bị theo mã — vd: /check ST001")
async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    if not context.args:
        await update.message.reply_text("Cú pháp: /check <site_code hoặc device_code>")
        return

    code = context.args[0].strip()
    rows = await asyncio.to_thread(_lookup_rows, code)

    if not rows:
        await update.message.reply_text(f"Không tìm thấy trạm/thiết bị với mã: {code}")
        return

    site = rows[0]
    lines = [
        f"<b>{escape(str(site['site_name']))}</b> ({escape(str(site['site_code']))}) "
        f"— trạng thái trạm: {escape(str(site['site_status']))}"
    ]
    for r in rows:
        if r["alarm_name"]:
            status_txt = f"⚠️ {escape(str(r['alarm_name']))} — từ {escape(str(r['start_time']))}"
        else:
            status_txt = "✅ bình thường"
        lines.append(
            f"- {escape(str(r['device_code']))} ({escape(str(r['device_type']))}): {status_txt}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
