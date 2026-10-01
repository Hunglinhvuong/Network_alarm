import psycopg2

DB_CONFIG = dict(
    host="localhost",
    dbname="network_alarm",
    user="postgres",
    password="Mobifone123",
)

CHECKS = [
    ("Site không có device nào",
     """SELECT s.site_code FROM station s
        LEFT JOIN device d ON d.site_id = s.site_id
        WHERE d.device_id IS NULL"""),

    ("Site có device trùng loại",
     """SELECT s.site_code, d.type, COUNT(*) AS cnt
        FROM device d JOIN station s ON s.site_id = d.site_id
        GROUP BY s.site_code, d.type HAVING COUNT(*) > 1"""),

    ("device_code sai độ dài",
     """SELECT device_code FROM device WHERE length(trim(device_code)) > 18 """),

    ("Nhiều root trong topo",
     """SELECT n.node_code FROM node n
        LEFT JOIN topo_link tl ON tl.child_node_id = n.node_id
        WHERE tl.parent_node_id IS NULL"""),

    ("Node tự làm cha chính nó",
     """SELECT child_node_id FROM topo_link WHERE child_node_id = parent_node_id"""),

    ("Station có device nhưng chưa nằm trong topo",
     """SELECT s.site_code FROM station s
        LEFT JOIN topo_link tl ON tl.child_node_id = s.site_id
        WHERE tl.child_node_id IS NULL"""),

    ("trans_type thiếu (không phải root)",
     """SELECT n.node_code FROM topo_link tl
        JOIN node n ON n.node_id = tl.child_node_id
        WHERE tl.trans_type IS NULL AND tl.parent_node_id IS NOT NULL"""),

    ("Phát hiện cycle trong topo",
     """WITH RECURSIVE path AS (
            SELECT child_node_id, parent_node_id,
                   ARRAY[child_node_id] AS visited, false AS is_cycle
            FROM topo_link WHERE parent_node_id IS NOT NULL
            UNION ALL
            SELECT p.child_node_id, tl.parent_node_id,
                   p.visited || tl.child_node_id,
                   tl.child_node_id = ANY(p.visited)
            FROM path p JOIN topo_link tl ON tl.child_node_id = p.parent_node_id
            WHERE NOT p.is_cycle
        )
        SELECT DISTINCT n.node_code FROM path
        JOIN node n ON n.node_id = path.child_node_id
        WHERE is_cycle = true"""),
]


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    total_issues = 0

    for title, query in CHECKS:
        cur.execute(query)
        rows = cur.fetchall()
        status = "✅ OK" if not rows else f"❌ {len(rows)} vấn đề"
        print(f"\n[{status}] {title}")
        if rows:
            total_issues += len(rows)
            for r in rows[:20]:
                print("   -", r)
            if len(rows) > 20:
                print(f"   ... còn {len(rows) - 20} dòng nữa")

    print(f"\n{'='*40}\nTổng số vấn đề: {total_issues}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()