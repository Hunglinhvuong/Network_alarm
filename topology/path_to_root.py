"""
Truy vấn cây topo (node -> topo_link) bằng Recursive CTE.
Dùng cho: escalation logic, quy TRANS_NODE về STATION gần nhất khi có cảnh báo.
"""
import logging

from db.connection import get_cursor
from config.settings import NODE_TYPE_STATION

logger = logging.getLogger(__name__)

_PATH_TO_ROOT_SQL = """
WITH RECURSIVE path AS (
    SELECT
        n.node_id,
        n.node_code,
        n.node_name,
        n.node_type,
        tl.parent_node_id,
        0 AS depth
    FROM node n
    LEFT JOIN topo_link tl ON tl.child_node_id = n.node_id AND tl.is_active = TRUE
    WHERE n.node_id = %(node_id)s

    UNION ALL

    SELECT
        n.node_id,
        n.node_code,
        n.node_name,
        n.node_type,
        tl.parent_node_id,
        path.depth + 1
    FROM path
    JOIN node n ON n.node_id = path.parent_node_id
    LEFT JOIN topo_link tl ON tl.child_node_id = n.node_id AND tl.is_active = TRUE
    WHERE path.depth < 100  -- chặn an toàn nếu lỡ có cycle lọt qua validate
)
SELECT node_id, node_code, node_name, node_type, depth
FROM path
ORDER BY depth ASC;
"""


def get_path_to_root(node_id: int) -> list:
    """
    Trả về list dict từ node hiện tại (depth=0) đi dần lên root (depth cao nhất),
    mỗi phần tử: {node_id, node_code, node_name, node_type, depth}.
    List rỗng nếu node_id không tồn tại.
    """
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(_PATH_TO_ROOT_SQL, {"node_id": node_id})
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_nearest_station_ancestor(node_id: int):
    """
    Trả về node dict (STATION) gần nhất tính từ node_id đi lên root, bao gồm cả
    chính node_id nếu nó đã là STATION. Trả về None nếu không tìm thấy STATION
    nào trên toàn bộ đường đi (topo lỗi / node cô lập).

    Lý do cần hàm này: TRANS_NODE không có device/alarm riêng, nên khi 1 nhánh
    truyền dẫn trung gian bị escalate lên, phải quy về STATION thật để biết
    "trạm nào đang bị ảnh hưởng".
    """
    path = get_path_to_root(node_id)
    for n in path:
        if n["node_type"] == NODE_TYPE_STATION:
            return n
    logger.warning("Không tìm thấy STATION nào trên đường đi từ node_id=%s lên root", node_id)
    return None


def get_children(node_id: int) -> list:
    """Trả về danh sách node con trực tiếp (1 cấp) của node_id — hỗ trợ escalation/duyệt cây."""
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT n.node_id, n.node_code, n.node_name, n.node_type
            FROM topo_link tl
            JOIN node n ON n.node_id = tl.child_node_id
            WHERE tl.parent_node_id = %(node_id)s AND tl.is_active = TRUE
            """,
            {"node_id": node_id},
        )
        return [dict(r) for r in cur.fetchall()]


def get_root_node():
    """Trả về node gốc (không có parent) — dùng làm điểm bắt đầu duyệt cây cho escalation."""
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(
            """
            SELECT n.node_id, n.node_code, n.node_name, n.node_type
            FROM node n
            LEFT JOIN topo_link tl ON tl.child_node_id = n.node_id AND tl.is_active = TRUE
            WHERE tl.parent_node_id IS NULL
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
    if len(rows) != 1:
        logger.warning("Kỳ vọng đúng 1 root nhưng tìm thấy %d -> kiểm tra lại topo_link", len(rows))
    return rows[0] if rows else None
