from telegram import Update
from telegram.ext import ContextTypes

from .registry import command, COMMAND_HANDLERS


@command("start", "Bắt đầu / giới thiệu bot")
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot cảnh báo & tra cứu hệ thống mạng viễn thông.\nGõ /help để xem danh sách lệnh."
    )


@command("help", "Danh sách các lệnh hiện có")
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["<b>Danh sách lệnh:</b>"]
    for name, desc, _ in COMMAND_HANDLERS:
        lines.append(f"/{name} — {desc}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
