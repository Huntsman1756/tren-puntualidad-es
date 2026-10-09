"""Geometrías de recorrido (GTFS shapes.txt) con control de calidad.

Renfe publica shapes solo en el GTFS de Cercanías (`NN_LÍNEA[_INV]`). Una
parte importante está incompleta (p. ej. 10_C1 cubre 3 de sus 7 estaciones),
así que cada shape se valida contra las estaciones REALES de los viajes que
la usan: solo se dibuja si al menos SHAPE_MIN_SHARE de esas estaciones queda
a menos de SHAPE_MAX_DIST_M del trazado. El resto se conserva para auditoría
pero nunca se presenta como recorrido (no se inventan geometrías).
"""
import math

from sqlalchemy import text

SHAPE_MAX_DIST_M = 300
SHAPE_MIN_SHARE = 0.9


def _hav(a, b) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(x))


def evaluate_shape(points: list[tuple], stations: list[tuple]) -> dict:
    """points/stations: [(lat, lon)]. Devuelve métricas y estado."""
    n = len(stations)
    if len(points) < 2:
        return {"stations": n, "near": 0, "share": None, "length_m": 0,
                "status": "invalid"}
    near = sum(1 for s in stations
               if min(_hav(s, p) for p in points) <= SHAPE_MAX_DIST_M)
    length = sum(_hav(points[i], points[i + 1]) for i in range(len(points) - 1))
    share = near / n if n else None
    status = ("unused" if not n else
              "valid" if share >= SHAPE_MIN_SHARE else "partial")
    return {"stations": n, "near": near, "share": share,
            "length_m": round(length), "status": status}


def compute_shape_quality(conn, feed: str) -> dict:
    conn.execute(text("DELETE FROM shape_quality WHERE feed=:f"), {"f": feed})
    pts: dict = {}
    for sid, lat, lon in conn.execute(text(
            "SELECT shape_id, lat, lon FROM shapes WHERE feed=:f ORDER BY shape_id, seq"),
            {"f": feed}):
        pts.setdefault(sid, []).append((lat, lon))
    if not pts:
        return {}
    st: dict = {}
    for sid, lat, lon in conn.execute(text("""
            SELECT DISTINCT t.shape_id, s.lat, s.lon
            FROM trips t
            JOIN stop_times x ON x.feed=t.feed AND x.trip_id=t.trip_id
            JOIN stops s ON s.feed=x.feed AND s.stop_id=x.stop_id
            WHERE t.feed=:f AND t.shape_id IS NOT NULL AND t.shape_id <> ''
              AND s.lat IS NOT NULL"""), {"f": feed}):
        st.setdefault(sid, []).append((lat, lon))
    rows, stats = [], {}
    for sid, p in pts.items():
        ev = evaluate_shape(p, st.get(sid, []))
        stats[ev["status"]] = stats.get(ev["status"], 0) + 1
        rows.append({"f": feed, "s": sid, "np": len(p), **ev})
    conn.execute(text("""
        INSERT INTO shape_quality (feed, shape_id, n_points, stations,
            stations_near, share, length_m, status)
        VALUES (:f, :s, :np, :stations, :near, :share, :length_m, :status)"""),
        rows)
    return stats
