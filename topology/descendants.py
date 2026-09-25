"""
Duyệt xuống (ngược hướng với path_to_root.py): lấy toàn bộ node con cháu của 1 node,
lọc ra các STATION — dùng để escalation kiểm tra "toàn bộ trạm con của node X có đang
down hết không".
"""
import logging

from db.connection import get_cursor
from config.settings import NODE_TYPE_STATION

logger = logging.getLogger(__name__)

_DESCENDANTS_SQL = """
WITH RECURSIVE subtree AS (
    SELECT node_id, node_code, node_type, 0 AS depth
    FROM node
    WHERE node_id = %(node_id)s

    UNION ALL

    SELECT n.node_id, n.node_code, n.node_type, subtree.depth + 1
    FROM subtree
    JOIN topo_link tl ON tl.parent_node_id = subtree.node_id AND tl.is_active = TRUE
    JOIN node n ON n.node_id = tl.child_node_id
    WHERE subtree.depth < 100  -- chặn an toàn nếu lỡ có cycle
)
SELECT node_id, node_code, node_type
FROM subtree;
"""


def get_descendant_stations(node_id: int) -> set:
    """
    Trả về set các node_id (= site_id) của mọi STATION nằm trong subtree gốc tại
    node_id, bao gồm cả chính node_id nếu nó là STATION. Set rỗng nếu node cô lập
    hoặc không có STATION nào bên dưới (VD: TRANS_NODE lá không hợp lệ).
    """
    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(_DESCENDANTS_SQL, {"node_id": node_id})
        rows = cur.fetchall()
    return {r["node_id"] for r in rows if r["node_type"] == NODE_TYPE_STATION}
