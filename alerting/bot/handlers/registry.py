"""
Registry pattern cho lệnh Telegram — thiết kế để dễ mở rộng thêm chức năng tra cứu
mới ngoài alerting. Muốn thêm lệnh mới: tạo file trong handlers/, viết hàm async,
gắn decorator @command("ten_lenh", "mô tả"), rồi import file đó trong
handlers/__init__.py. KHÔNG cần sửa app.py hay bất kỳ file nào khác.
"""

COMMAND_HANDLERS = []  # list[(command_name, description, async_callback)]


def command(name: str, description: str):
    def decorator(func):
        COMMAND_HANDLERS.append((name, description, func))
        return func
    return decorator
