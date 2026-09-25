"""
Ví dụ minh hoạ việc mở rộng bot: thêm 1 lệnh tra cứu mới chỉ cần 1 file thế này,
không đụng vào app.py hay các handler khác.
"""
from telegram import Update
from telegram.ext import ContextTypes

from .registry import command
from .common import is_authorized, UNAUTHORIZED_MSG
from db.connection import get_cursor
from topology.path_to_root import get_path_to_root


@command("path", "Xem đường đi lên root của 1 trạm — vd: /path ST001")
async def path_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    if not context.args:
        await update.message.reply_text("Cú pháp: /path <site_code>")
        return

    site_code = context.args[0].strip()
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute("SELECT site_id FROM station WHERE site_code = %(code)s", {"code": site_code})
        row = cur.fetchone()

    if row is None:
        await update.message.reply_text(f"Không tìm thấy trạm với site_code: {site_code}")
        return

    path = get_path_to_root(row["site_id"])
    if not path:
        await update.message.reply_text("Trạm chưa được gán vào topo (chưa có trong topo_link).")
        return

    lines = [f"<b>Đường đi lên root từ {site_code}:</b>"]
    for n in path:
        lines.append(f"{'  ' * n['depth']}↳ {n['node_name']} ({n['node_code']}, {n['node_type']})")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
