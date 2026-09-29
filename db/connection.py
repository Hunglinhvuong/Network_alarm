"""
Quản lý kết nối PostgreSQL. Không dùng ORM (SQLAlchemy...) để nhẹ RAM trên Dell Wyse 5010.
Dùng psycopg2 thuần + context manager để đảm bảo connection/cursor luôn được đóng.

Đặc biệt: ép DB session timezone thành múi giờ của ứng dụng (APP_TIMEZONE).
"""
import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from config.settings import DB_CONFIG, APP_TIMEZONE

logger = logging.getLogger(__name__)

_connection = None


def create_connection():
    """Mở kết nối PostgreSQL độc lập; dùng cho worker chạy trên thread riêng."""
    connection = psycopg2.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        dbname=DB_CONFIG["dbname"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        connect_timeout=DB_CONFIG["connect_timeout"],
    )
    connection.autocommit = False
    try:
        with connection.cursor() as cur:
            cur.execute("SET TIME ZONE %s", (APP_TIMEZONE,))
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection


def get_connection():
    """Trả về 1 connection dùng chung (singleton nhẹ), tự reconnect nếu đã đóng/lỗi."""
    global _connection
    if _connection is None or _connection.closed:
        logger.info("Đang mở kết nối PostgreSQL tới %s:%s/%s", DB_CONFIG["host"], DB_CONFIG["port"], DB_CONFIG["dbname"])
        _connection = create_connection()
        logger.info("Đã cấu hình DB session timezone thành: %s", APP_TIMEZONE)
    return _connection


@contextmanager
def get_cursor(dict_cursor=True, commit=True):
    """
    dict_cursor=True -> trả về row dạng dict (RealDictCursor) thay vì tuple.
    commit=True -> tự commit khi thành công; rollback nếu có exception.
    """
    conn = get_connection()
    cursor_factory = psycopg2.extras.RealDictCursor if dict_cursor else None
    cur = conn.cursor(cursor_factory=cursor_factory)
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Lỗi khi thao tác DB, đã rollback")
        raise
    finally:
        cur.close()


def close_connection():
    global _connection
    if _connection is not None and not _connection.closed:
        _connection.close()
        _connection = None
