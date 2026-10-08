"""Fixtures de BD para tests de integración (PostgreSQL real, aislado).

Escenario sintético mínimo pero representativo:
- C1 existe en Madrid (10T0001C1) y en Asturias (20T0001C1): homónimas.
- C4a aparece como 'C4a' y 'C4A' (grafías distintas del GTFS).
- Rodalies: prefijo GTFS 51 -> núcleo oficial 50.
- 60T0009C3: prefijo Bilbao pero sus paradas son de León -> conflicto.
- Un viaje nocturno cruza medianoche (dep 23:50, llegada 24:10).
"""
import os
from datetime import date, datetime, timedelta

import pytest
from collector.gtfsutil import TZINFO
from sqlalchemy import text

TODAY = datetime.now(TZINFO).date()

STOPS = [
    ("cer", "17000", "Madrid-Atocha Cercanías", 40.406, -3.690),
    ("cer", "18000", "Madrid-Chamartín", 40.472, -3.682),
    ("cer", "10000", "Getafe Centro", 40.307, -3.732),
    ("cer", "15211", "Gijón Sanz Crespo", 43.537, -5.672),
    ("cer", "15410", "Oviedo", 43.367, -5.856),
    ("cer", "71801", "Barcelona-Sants", 41.379, 2.140),
    ("cer", "72400", "Granollers Centre", 41.598, 2.288),
    ("cer", "05778", "León", 42.600, -5.580),
    ("cer", "05761", "Cistierna", 42.800, -5.130),
    ("cer", "99999", "Aislada", 40.0, -4.0),
    ("ld", "17000", "Madrid-Puerta de Atocha", 40.406, -3.690),
    ("ld", "71801", "Barcelona-Sants", 41.379, 2.140),
]
OFFICIAL = {  # CODIGO_ESTACION -> (NUCLEO, LINEAS) del visor oficial
    "17000": ("10", "C1,C4a"), "18000": ("10", "C1,C4a"),
    "10000": ("10", "C4a"), "15211": ("20", "C1"), "15410": ("20", "C1"),
    "71801": ("50", "R1"), "72400": ("50", "R1"), "05778": ("47", "C1"),
    "05761": ("47", "C1"),
}
ROUTES = [
    ("cer", "10T0001C1", "C1", 2, "75B6E0"),
    ("cer", "20T0001C1", "C1", 2, "EC3541"),
    ("cer", "10T0013C4a", "C4a", 2, "2C2A86"),
    ("cer", "10T0099C4A", "C4A", 2, "2C2A86"),
    ("cer", "51T0001R1", "R1", 2, "7DB9E8"),
    ("cer", "60T0009C3", "C3", 2, "000000"),
    ("cer", "10T0109C8b", "C8b", 3, "868584"),   # bus alternativo
    ("ld", "LD_AVE_MAD_BCN", "AVE", 2, None),
]
H = 3600


def _st(trip, rows):
    return [("cer" if not trip.startswith("LD") else "ld", trip, i + 1, s, a, d)
            for i, (s, a, d) in enumerate(rows)]


TRIPS = [  # feed, trip_id, route, service, train_number
    ("cer", "MAD_C1_0600", "10T0001C1", "S_ALL", "21000"),
    ("cer", "MAD_C1_2350", "10T0001C1", "S_ALL", "21001"),
    ("cer", "MAD_C4A_0700", "10T0013C4a", "S_ALL", "22000"),
    ("cer", "MAD_C4AX_0710", "10T0099C4A", "S_D2", "22001"),
    ("cer", "AST_C1_0600", "20T0001C1", "S_ALL", "23000"),
    ("cer", "BCN_R1_0800", "51T0001R1", "S_ALL", "24000"),
    ("cer", "BIL_C3_0900", "60T0009C3", "S_ALL", "25000"),
    ("ld", "LD_03100", "LD_AVE_MAD_BCN", "S_ALL", "03100"),
]
STOP_TIMES = (
    _st("MAD_C1_0600", [("17000", 6 * H, 6 * H), ("18000", 6 * H + 1200, 6 * H + 1200)])
    + _st("MAD_C1_2350", [("17000", 23 * H + 3000, 23 * H + 3000),
                          ("18000", 24 * H + 600, 24 * H + 600)])
    + _st("MAD_C4A_0700", [("10000", 7 * H, 7 * H), ("17000", 7 * H + 900, 7 * H + 900),
                           ("18000", 7 * H + 1800, 7 * H + 1800)])
    + _st("MAD_C4AX_0710", [("10000", 7 * H + 600, 7 * H + 600),
                            ("17000", 7 * H + 1500, 7 * H + 1500)])
    + _st("AST_C1_0600", [("15211", 6 * H, 6 * H), ("15410", 6 * H + 1800, 6 * H + 1800)])
    + _st("BCN_R1_0800", [("72400", 8 * H, 8 * H), ("71801", 8 * H + 2400, 8 * H + 2400)])
    + _st("BIL_C3_0900", [("05778", 9 * H, 9 * H), ("05761", 9 * H + 3600, 9 * H + 3600)])
    + _st("LD_03100", [("17000", 9 * H, 9 * H), ("71801", 12 * H, 12 * H)])
)

TABLES = ["stops", "routes", "trips", "stop_times", "service_days",
          "trip_flags", "trip_span", "line_route", "station_nucleo",
          "alerts", "alerts_seen", "rt_trip", "rt_stop_update", "rt_fleet",
          "rt_vehicle", "push_rules", "push_devices", "push_subs", "meta"]


def service_days(days_all=range(-1, 8), d2=(2,)):
    rows = []
    for f in ("cer", "ld"):
        rows += [(f, "S_ALL", TODAY + timedelta(days=i)) for i in days_all]
        rows += [(f, "S_D2", TODAY + timedelta(days=i)) for i in d2]
    return rows


def load_scenario(conn):
    from collector.lines import compute_line_routes, compute_trip_spans
    for t in TABLES:
        conn.execute(text(f"DELETE FROM {t}"))
    conn.execute(text("INSERT INTO stops VALUES (:f,:s,:n,:la,:lo)"),
                 [dict(f=a, s=b, n=c, la=d, lo=e) for a, b, c, d, e in STOPS])
    conn.execute(text("""INSERT INTO routes (feed, route_id, short_name, long_name,
        route_type, color) VALUES (:f,:r,:s,:ln,:t,:c)"""),
        [dict(f=f, r=r, s=s, ln=f"Ruta {r}", t=t, c=c) for f, r, s, t, c in ROUTES])
    conn.execute(text("""INSERT INTO trips (feed, trip_id, route_id, service_id,
        train_number) VALUES (:f,:t,:r,:s,:n)"""),
        [dict(f=f, t=t, r=r, s=s, n=n) for f, t, r, s, n in TRIPS])
    conn.execute(text("""INSERT INTO stop_times (feed, trip_id, seq, stop_id, arr, dep)
        VALUES (:f,:t,:q,:s,:a,:d)"""),
        [dict(f=f, t=t, q=q, s=s, a=a, d=d) for f, t, q, s, a, d in STOP_TIMES])
    conn.execute(text("INSERT INTO service_days VALUES (:f,:s,:d)"),
                 [dict(f=f, s=s, d=d) for f, s, d in service_days()])
    conn.execute(text("""INSERT INTO station_nucleo (code, nucleo_code, lineas,
        fetched_at) VALUES (:c,:n,:l,0)"""),
        [dict(c=c, n=n, l=lin) for c, (n, lin) in OFFICIAL.items()])
    for f in ("cer", "ld"):
        compute_trip_spans(conn, f)
    compute_line_routes(conn)


def reset_api_caches():
    import api.common as common
    import api.incidents as inc
    import api.main as m
    common.reset_caches()
    inc._stop_cache["ts"] = 0
    inc._station_routes_cache.clear()
    inc._route_stops_cache.clear()
    if hasattr(m._coverage_days, "c"):
        del m._coverage_days.c
    m._stations_cache["ts"] = 0


@pytest.fixture(scope="session")
def db_engine():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL no definido (BD de test aislada)")
    from collector.db import engine, wait_and_create
    try:
        wait_and_create(retries=2)
    except Exception as e:
        pytest.skip(f"PostgreSQL de test no disponible: {e}")
    return engine


@pytest.fixture()
def scenario(db_engine):
    with db_engine.begin() as c:
        load_scenario(c)
    reset_api_caches()
    return db_engine


@pytest.fixture()
def client(scenario):
    from api.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        reset_api_caches()
        yield c


def epoch(d: date, secs: int) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=TZINFO).timestamp()) + secs
