"""
Import mọi module handler ở đây để decorator @command trong từng file được thực thi
(đăng ký vào COMMAND_HANDLERS) khi package này được import 1 lần ở app.py.

Thêm handler mới: viết file mới trong thư mục này rồi import nó thêm 1 dòng bên dưới.
"""
from . import start  # noqa: F401
from . import status  # noqa: F401
from . import lookup  # noqa: F401
from . import path  # noqa: F401
