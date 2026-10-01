"""
Duyệt xuống (ngược hướng với path_to_root.py): lấy toàn bộ node con cháu của 1 node,
lọc ra các STATION — dùng để escalation kiểm tra "toàn bộ trạm con của node X có đang
down hết không".
"""
import logging
from collections import defaultdict

from db.connection import get_cursor
from config.settings import NODE_TYPE_STATION

logger = logging.getLogger(__name__)

_DESCENDANTS_SQL = """
WITH RECURSIVE subtree AS (
    SELECT roots.node_id AS root_node_id, n.node_id, n.node_code, n.node_type, 0 AS depth
    FROM unnest(%(node_ids)s::bigint[]) AS roots(node_id)
    JOIN node n ON n.node_id = roots.node_id

    UNION ALL

    SELECT subtree.root_node_id, n.node_id, n.node_code, n.node_type, subtree.depth + 1
    FROM subtree
    JOIN topo_link tl ON tl.parent_node_id = subtree.node_id AND tl.is_active = TRUE
    JOIN node n ON n.node_id = tl.child_node_id
    WHERE subtree.depth < 100  -- chặn an toàn nếu lỡ có cycle
)
SELECT root_node_id, node_id, node_type
FROM subtree
ORDER BY root_node_id, depth;
"""


def get_descendant_stations_many(node_ids: set[int]) -> dict[int, set[int]]:
    """Lấy tập station con cháu cho nhiều node bằng một recursive query."""
    if not node_ids:
        return {}

    with get_cursor(dict_cursor=True, commit=False) as cur:
        cur.execute(_DESCENDANTS_SQL, {"node_ids": list(node_ids)})
        rows = cur.fetchall()

    descendants = defaultdict(set)
    for row in rows:
        if row["node_type"] == NODE_TYPE_STATION:
            descendants[row["root_node_id"]].add(row["node_id"])
    return dict(descendants)


def get_descendant_stations(node_id: int) -> set:
    """
    Trả về set các node_id (= site_id) của mọi STATION nằm trong subtree gốc tại
    node_id, bao gồm cả chính node_id nếu nó là STATION. Set rỗng nếu node cô lập
    hoặc không có STATION nào bên dưới (VD: TRANS_NODE lá không hợp lệ).
    """
    return get_descendant_stations_many({node_id}).get(node_id, set())
