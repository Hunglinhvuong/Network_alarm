import numpy as np
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
    """)
    rows = cur.fetchall()
    if len(rows) < 4:
        raise ValueError(
            f"Cần tối thiểu 4 station active để chạy Delaunay (hiện có {len(rows)})"
        )
    return rows


def build_delaunay_edges(stations):
    """stations: list[(site_id, lat, long)] -> set of (site_id_a, site_id_b) đã sort, distance <=7km"""
    site_ids = [s[0] for s in stations]
    coords = np.array([[s[2], s[1]] for s in stations])  # (long, lat) cho Delaunay phẳng

    tri = Delaunay(coords)

    raw_edges = set()
    for simplex in tri.simplices:
        i, j, k = simplex
        raw_edges.add(tuple(sorted((i, j))))
        raw_edges.add(tuple(sorted((j, k))))
        raw_edges.add(tuple(sorted((i, k))))

    valid_edges = []
    for i, j in raw_edges:
        site_a, lat_a, lon_a = stations[i]
        site_b, lat_b, lon_b = stations[j]
        dist = haversine_km(lat_a, lon_a, lat_b, lon_b)
        if dist <= MAX_DISTANCE_KM:
            a, b = sorted((site_a, site_b))
            valid_edges.append((a, b, round(float(dist), 3)))

    return valid_edges


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