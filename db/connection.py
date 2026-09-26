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


def get_connection():
    """Trả về 1 connection dùng chung (singleton nhẹ), tự reconnect nếu đã đóng/lỗi.
    
    Sau khi kết nối thành công, ép DB session timezone thành APP_TIMEZONE để tất cả
    TIMESTAMPTZ được xử lý theo múi giờ cục bộ.
    """
    global _connection
    if _connection is None or _connection.closed:
        logger.info("Đang mở kết nối PostgreSQL tới %s:%s/%s", DB_CONFIG["host"], DB_CONFIG["port"], DB_CONFIG["dbname"])
        _connection = psycopg2.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            dbname=DB_CONFIG["dbname"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            connect_timeout=DB_CONFIG["connect_timeout"],
        )
        _connection.autocommit = False
        
        # Ép session timezone của DB thành APP_TIMEZONE
        # Điều này đảm bảo tất cả TIMESTAMPTZ được chuyển đổi khi query
        try:
            with _connection.cursor() as cur:
                cur.execute(f"SET TIME ZONE '{APP_TIMEZONE}'")
            _connection.commit()
            logger.info("Đã cấu hình DB session timezone thành: %s", APP_TIMEZONE)
        except Exception:
            logger.exception("Lỗi khi cấu hình DB timezone, tiếp tục...")
    
    return _connection


@contextmanager
def get_cursor(dict_cursor=True, commit=True):
    """
    Context manager: mở cursor, commit khi thành công, rollback khi lỗi.
    dict_cursor=True -> trả về row dạng dict (RealDictCursor) thay vì tuple.
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
