"""
Ví dụ minh hoạ việc mở rộng bot: thêm 1 lệnh tra cứu mới chỉ cần 1 file thế này,
không đụng vào app.py hay các handler khác.
"""
import asyncio
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from .registry import command
from .common import is_authorized, UNAUTHORIZED_MSG
from db.connection import get_cursor
from topology.path_to_root import get_path_to_root


def _lookup_path(site_code):
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            "SELECT site_id FROM station WHERE LOWER(site_code) = LOWER(%(code)s)",
            {"code": site_code},
        )
        row = cur.fetchone()
    if row is None:
        return None, []
    return row["site_id"], get_path_to_root(row["site_id"])


@command("path", "Xem đường đi lên root của 1 trạm — vd: /path ST001")
async def path_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    if not context.args:
        await update.message.reply_text("Cú pháp: /path <site_code>")
        return

    site_code = context.args[0].strip()
    site_id, path = await asyncio.to_thread(_lookup_path, site_code)
    if site_id is None:
        await update.message.reply_text(f"Không tìm thấy trạm với site_code: {site_code}")
        return

    if not path:
        await update.message.reply_text("Trạm chưa được gán vào topo (chưa có trong topo_link).")
        return

    lines = [f"<b>Đường đi lên root từ {escape(str(site_code))}:</b>"]
    for n in path:
        lines.append(
            f"{'  ' * n['depth']}↳ {escape(str(n['node_name']))} "
            f"({escape(str(n['node_code']))}, {escape(str(n['node_type']))})"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
