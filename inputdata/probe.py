import sys
import traceback

print("python:", sys.version)
print("cwd path[0]:", sys.path[0])

def step(name, fn):
    try:
        r = fn()
        print(f"[OK]   {name}", r if r else "")
    except BaseException:
        print(f"[FAIL] {name}")
        traceback.print_exc()
        sys.exit(1)

def imp_numpy():
    import numpy
    return numpy.__file__

def imp_scipy():
    import scipy.spatial
    return scipy.spatial.__file__

def imp_psycopg2():
    import psycopg2
    return psycopg2.__file__

def connect():
    import psycopg2
    conn = psycopg2.connect(
        host="localhost", dbname="network_alarm",
        user="postgres", password="Mobifone123",
    )
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM station WHERE status='active'")
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return f"active={n}"

def delaunay_smoke():
    import numpy as np
    from scipy.spatial import Delaunay
    pts = np.array([[0, 0], [1, 0], [0, 1], [1, 1], [0.5, 0.4]])
    return f"simplices={len(Delaunay(pts).simplices)}"

step("import numpy", imp_numpy)
step("import scipy.spatial", imp_scipy)
step("import psycopg2", imp_psycopg2)
step("connect + query", connect)
step("delaunay smoke test", delaunay_smoke)
print("Tất cả OK -> lỗi nằm trong logic neighbor_edge.py")