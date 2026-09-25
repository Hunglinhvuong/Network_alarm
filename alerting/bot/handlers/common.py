"""Tiện ích dùng chung giữa các handler."""
from config.settings import TELEGRAM_ADMIN_IDS

UNAUTHORIZED_MSG = "⛔ Bạn không có quyền dùng lệnh này."


def is_authorized(update) -> bool:
    """
    Danh sách trắng theo Telegram user_id. TELEGRAM_ADMIN_IDS rỗng -> cho phép tất cả
    (chỉ nên để trống khi test nội bộ, không dùng ở production).
    """
    if not TELEGRAM_ADMIN_IDS:
        return True
    user = update.effective_user
    return user is not None and str(user.id) in TELEGRAM_ADMIN_IDS
