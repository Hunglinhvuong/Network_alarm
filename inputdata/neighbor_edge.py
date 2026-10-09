import numpy as np
from collections import defaultdict
from scipy.spatial import Delaunay
import psycopg2
from psycopg2.extras import execute_values

DB_CONFIG = dict(
    host="localhost",
    dbname="network_alarm",
    user="postgres",
    password="Mobifone123",
)

MAX_DISTANCE_KM = 7.0
EARTH_RADIUS_KM = 6371.0
COORD_PRECISION = 6  # ~0.1m, dùng để gom các trạm trùng toạ độ


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def fetch_active_stations(cur):
    cur.execute("""
        SELECT site_id, lat, long FROM station
        WHERE status = 'active'
          AND lat IS NOT NULL AND long IS NOT NULL
    """)
    rows = cur.fetchall()

    # Dedupe theo site_id (giữ bản ghi đầu tiên), ép về float
    seen = {}
    dup_ids = 0
    for site_id, lat, lon in rows:
        if site_id in seen:
            dup_ids += 1
            continue
        seen[site_id] = (site_id, float(lat), float(lon))
    if dup_ids:
        print(f"[station] bỏ {dup_ids} bản ghi trùng site_id")

    stations = list(seen.values())
    if len(stations) < 4:
        raise ValueError(
            f"Cần tối thiểu 4 station active hợp lệ để chạy Delaunay (hiện có {len(stations)})"
        )
    return stations


def build_delaunay_edges(stations):
    """stations: list[(site_id, lat, long)] -> list[(site_a, site_b, dist_km)] đã sort, unique, <= MAX_DISTANCE_KM"""
    # Gom các trạm trùng toạ độ
    groups = defaultdict(list)
    for s in stations:
        key = (round(s[1], COORD_PRECISION), round(s[2], COORD_PRECISION))
        groups[key].append(s)

    keys = list(groups.keys())
    n_colocated = len(stations) - len(keys)
    if n_colocated:
        print(f"[station] {n_colocated} trạm trùng toạ độ -> gom nhóm, nối trực tiếp với nhau")
    if len(keys) < 4:
        raise ValueError(f"Chỉ có {len(keys)} toạ độ khác nhau, không đủ để chạy Delaunay")

    # Chiếu phẳng (equirectangular) để Delaunay không bị méo theo kinh độ
    lats = np.array([k[0] for k in keys])
    lons = np.array([k[1] for k in keys])
    lat0 = np.radians(lats.mean())
    coords = np.column_stack([lons * np.cos(lat0), lats])

    tri = Delaunay(coords)

    raw_edges = set()
    for i, j, k in tri.simplices:
        raw_edges.add(tuple(sorted((i, j))))
        raw_edges.add(tuple(sorted((j, k))))
        raw_edges.add(tuple(sorted((i, k))))

    edges = {}

    def add_edge(sa, sb):
        if sa[0] == sb[0]:
            return
        dist = float(haversine_km(sa[1], sa[2], sb[1], sb[2]))
        if dist <= MAX_DISTANCE_KM:
            a, b = sorted((sa[0], sb[0]))
            edges[(a, b)] = round(dist, 3)

    # Cạnh giữa các nhóm khác nhau (mở rộng ra mọi trạm trong nhóm)
    for i, j in raw_edges:
        for sa in groups[keys[i]]:
            for sb in groups[keys[j]]:
                add_edge(sa, sb)

    # Cạnh nội bộ nhóm trùng toạ độ
    for members in groups.values():
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                add_edge(members[x], members[y])

    return [(a, b, d) for (a, b), d in edges.items()]


def sync_neighbor_edges(cur, edges):
    cur.execute("SELECT site_id_a, site_id_b FROM neighbor_edge")
    existing = set(cur.fetchall())
    new_set = {(a, b) for a, b, _ in edges}

    to_delete = existing - new_set
    if to_delete:
        execute_values(
            cur,
            "DELETE FROM neighbor_edge WHERE (site_id_a, site_id_b) IN (VALUES %s)",
            list(to_delete),
        )
        print(f"[neighbor_edge] xoá {len(to_delete)} cạnh cũ không còn hợp lệ")

    if edges:
        execute_values(
            cur,
            """
            INSERT INTO neighbor_edge (site_id_a, site_id_b, distance_km)
            VALUES %s
            ON CONFLICT (site_id_a, site_id_b) DO UPDATE
            SET distance_km = EXCLUDED.distance_km
            """,
            edges,
        )
    print(f"[neighbor_edge] upsert {len(edges)} cạnh (Delaunay + lọc <= {MAX_DISTANCE_KM}km)")


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        stations = fetch_active_stations(cur)
        edges = build_delaunay_edges(stations)
        sync_neighbor_edges(cur, edges)
        conn.commit()
        print("Hoàn tất sinh neighbor_edge.")
    except Exception as e:
        conn.rollback()
        print(f"LỖI, đã rollback: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()