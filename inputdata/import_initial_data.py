import csv
import sys
import psycopg2
from psycopg2.extras import execute_values

DB_CONFIG = dict(
    host="localhost",
    dbname="network_alarm",
    user="postgres",
    password="Mobifone123",
)


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def upsert_node(cur, node_code, node_name, node_type):
    cur.execute(
        """
        INSERT INTO node (node_code, node_name, node_type)
        VALUES (%s, %s, %s)
        ON CONFLICT (node_code) DO UPDATE
        SET node_name = EXCLUDED.node_name
        RETURNING node_id
        """,
        (node_code, node_name, node_type),
    )
    return cur.fetchone()[0]


def import_stations(cur, rows):
    data = []
    for r in rows:
        node_id = upsert_node(cur, r["site_code"], r["site_name"], "STATION")
        data.append((node_id, r["site_code"], r["site_name"], float(r["lat"]), float(r["long"]), r["status"]))

    execute_values(
        cur,
        """
        INSERT INTO station (site_id, site_code, site_name, lat, long, status)
        VALUES %s
        ON CONFLICT (site_id) DO UPDATE
        SET site_name = EXCLUDED.site_name,
            lat = EXCLUDED.lat,
            long = EXCLUDED.long,
            status = EXCLUDED.status,
            updated_at = now()
        """,
        data,
    )
    print(f"[station] upsert {len(data)} dòng")


def import_trans_nodes(cur, rows):
    data = []
    for r in rows:
        node_id = upsert_node(cur, r["node_code"], r["node_name"], "TRANS_NODE")
        data.append((node_id, r.get("equipment_type") or None))

    execute_values(
        cur,
        """
        INSERT INTO trans_node (node_id, equipment_type)
        VALUES %s
        ON CONFLICT (node_id) DO UPDATE
        SET equipment_type = EXCLUDED.equipment_type
        """,
        data,
    )
    print(f"[trans_node] upsert {len(data)} dòng")


def import_devices(cur, rows):
    cur.execute("SELECT site_code, site_id FROM station")
    site_map = dict(cur.fetchall())

    data = []
    for r in rows:
        site_id = site_map.get(r["site_code"])
        if site_id is None:
            print(f"[device] BỎ QUA {r['device_code']}: site_code {r['site_code']} không tồn tại")
            continue
        raw_num_cell = r.get("NumCell") or r.get("num_cell") or r.get("numcell")
        try:
            num_cell = int(raw_num_cell) if raw_num_cell and raw_num_cell.strip() else None
        except ValueError as exc:
            raise ValueError(
                f"NumCell không hợp lệ cho device {r['device_code']}: {raw_num_cell!r}"
            ) from exc
        if num_cell is not None and num_cell < 0:
            raise ValueError(f"NumCell không được âm cho device {r['device_code']}: {num_cell}")
        data.append((r["device_code"], r["device_name"], site_id, r["type"], num_cell))

    execute_values(
        cur,
        """
        INSERT INTO device (device_code, device_name, site_id, type, num_cell)
        VALUES %s
        ON CONFLICT (device_code) DO UPDATE
        SET device_name = EXCLUDED.device_name,
            site_id = EXCLUDED.site_id,
            type = EXCLUDED.type,
            num_cell = EXCLUDED.num_cell
        """,
        data,
    )
    print(f"[device] upsert {len(data)} dòng")


def validate_tree(edges, all_node_codes):
    """edges: dict child_code -> parent_code (None = root)."""
    roots = [c for c, p in edges.items() if p is None]
    no_parent_defined = set(all_node_codes) - set(edges.keys())
    roots += list(no_parent_defined)

    if len(roots) > 1:
        return False, f"Có {len(roots)} root (kỳ vọng 1): {roots}"
    if len(roots) == 0:
        return False, "Không tìm thấy root -> chắc chắn có cycle"

    visited = {}

    def has_cycle(node, path):
        if node in path:
            return True
        if node in visited:
            return False
        path.add(node)
        parent = edges.get(node)
        if parent and parent in edges:
            if has_cycle(parent, path):
                return True
        visited[node] = True
        path.discard(node)
        return False

    for node in edges:
        if has_cycle(node, set()):
            return False, f"Phát hiện cycle liên quan đến node {node}"

    return True, "OK"


def import_topo(cur, rows):
    cur.execute("SELECT node_code, node_id FROM node")
    node_map = dict(cur.fetchall())
    all_codes = list(node_map.keys())

    edges = {}
    trans_types = {}
    for r in rows:
        child = r["child_node_code"]
        parent = r["parent_node_code"] or None
        edges[child] = parent
        trans_types[child] = r.get("trans_type") or None

    ok, msg = validate_tree(edges, all_codes)
    if not ok:
        print(f"[topo] LỖI - KHÔNG IMPORT: {msg}")
        sys.exit(1)

    data = []
    for child, parent in edges.items():
        child_id = node_map.get(child)
        parent_id = node_map.get(parent) if parent else None
        if child_id is None:
            print(f"[topo] BỎ QUA {child}: node_code không tồn tại (chưa import station/trans_node)")
            continue
        data.append((child_id, parent_id, trans_types.get(child)))

    execute_values(
        cur,
        """
        INSERT INTO topo_link (child_node_id, parent_node_id, trans_type)
        VALUES %s
        ON CONFLICT (child_node_id) DO UPDATE
        SET parent_node_id = EXCLUDED.parent_node_id,
            trans_type = EXCLUDED.trans_type,
            effective_from = now()
        """,
        data,
    )
    print(f"[topo] upsert {len(data)} dòng - cây hợp lệ ({msg})")


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        import_stations(cur, load_csv("station.csv"))
        import_trans_nodes(cur, load_csv("trans_node.csv"))
        import_devices(cur, load_csv("device.csv"))
        import_topo(cur, load_csv("topo.csv"))
        conn.commit()
        print("Hoàn tất import.")
    except Exception as e:
        conn.rollback()
        print(f"LỖI, đã rollback toàn bộ: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()