"""Tests de integración sobre PostgreSQL real (esquema del collector).

Ejecutan ingestas sucesivas reales (poll_trip_updates / poll_fleet /
load_feed) y verifican deduplicación, identidad
(feed, trip_id, service_date) ante cambios de día, retrasos extremos y
ambigüedad, inmutabilidad del histórico de programación ante recargas
GTFS, y la consulta del histórico sobre el snapshot.

Requieren una BD de test aislada (TEST_DATABASE_URL o el postgres de
CI/dev). Se saltan si no hay conexión.
"""
import io
import time
import zipfile
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

from collector.gtfsutil import TZINFO  # noqa: E402

TODAY = datetime.now(TZINFO).date()
Y = TODAY - timedelta(days=1)
D2 = TODAY + timedelta(days=2)

ALL_TABLES = ["observations", "circulation", "circulation_stop",
              "sched_capture", "stop_times", "service_days", "trips",
              "routes", "stops", "rt_trip", "rt_stop_update", "rt_vehicle",
              "rt_fleet", "alerts", "trip_flags", "geo_station",
              "route_core", "meta"]


def _mid(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=TZINFO).timestamp())


def _l(d: date, h: int, m: int = 0) -> int:
    """epoch de h:m (hora local) del día d."""
    return _mid(d) + h * 3600 + m * 60


def gtfs_zip(stops, routes, trips, cal_dates, stop_times):
    """GTFS mínimo en memoria para load_feed.

    stops: [(id, name)] · routes: [(id, short)]
    trips: [(trip_id, route_id, service_id)]
    cal_dates: [(service_id, date)] — alta explícita (exception_type=1)
    stop_times: [(trip_id, arr, dep, stop_id, seq)] tiempos 'HH:MM:SS'."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n"
                   + "".join(f"{s},{n},40.4,-3.7\n" for s, n in stops))
        z.writestr("routes.txt",
                   "route_id,route_short_name,route_long_name,route_type,"
                   "route_color,route_text_color\n"
                   + "".join(f"{r},{sn},Ruta {sn},2,,\n" for r, sn in routes))
        z.writestr("trips.txt",
                   "route_id,service_id,trip_id,trip_headsign,trip_short_name\n"
                   + "".join(f"{r},{s},{t},,\n" for t, r, s in trips))
        z.writestr("calendar.txt",
                   "service_id,monday,tuesday,wednesday,thursday,friday,"
                   "saturday,sunday,start_date,end_date\n")
        z.writestr("calendar_dates.txt", "service_id,date,exception_type\n"
                   + "".join(f"{s},{d:%Y%m%d},1\n" for s, d in cal_dates))
        z.writestr("stop_times.txt",
                   "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                   + "".join(f"{t},{a},{d},{s},{q}\n"
                             for t, a, d, s, q in stop_times))
    buf.seek(0)
    return buf


def base_zip(dep_s1="10:00:00", arr_s2="10:30:00"):
    """Una línea C9 con dos viajes: T1 (diario, Y y D) y T2 nocturno
    (cruza medianoche, dep 25:30 solo el día Y)."""
    return gtfs_zip(
        stops=[("S1", "Origen"), ("S2", "Intermedia"), ("S3", "Destino")],
        routes=[("R9", "C9")],
        trips=[("T1", "R9", "SVC1"), ("T2", "R9", "SVC2"),
               ("T3", "R9", "SVC3")],
        cal_dates=[("SVC1", Y), ("SVC1", TODAY), ("SVC1", D2),
                   ("SVC2", Y), ("SVC2", TODAY),
                   ("SVC3", Y), ("SVC3", TODAY)],
        stop_times=[("T1", "10:00:00", dep_s1, "S1", 1),
                    ("T1", arr_s2, "10:31:00", "S2", 2),
                    ("T1", "11:00:00", "11:00:00", "S3", 3),
                    ("T2", "25:00:00", "25:00:00", "S1", 1),
                    ("T2", "25:30:00", "25:30:00", "S2", 2),
                    ("T3", "12:00:00", "12:00:00", "S1", 1),
                    ("T3", "12:30:00", "12:30:00", "S3", 2)],
    )


def tu_payload(feed_ts, entries):
    """entries: [(trip_id, [(stop_id, delay_sec, event_epoch)])]."""
    return {"header": {"timestamp": feed_ts},
            "entity": [{"id": tid, "tripUpdate": {
                "trip": {"tripId": tid},
                "stopTimeUpdate": [
                    {"stopId": s, "arrival": {"delay": d, "time": t}}
                    for s, d, t in stus]}} for tid, stus in entries]}


def fleet_payload(ts_local: str, entries):
    """entries: [(trip_id, retrasoMin, cur, next, eta_epoch|None)]."""
    trenes = []
    for tid, dm, cur, ns, eta in entries:
        trenes.append({
            "tripId": tid, "codTren": "12345", "codLinea": "C9",
            "retrasoMin": str(dm), "codEstAct": cur, "codEstSig": ns,
            "horaLlegadaSigEst": eta, "codEstOrig": "S1",
            "codEstDest": "S3"})
    return {"fechaActualizacion": ts_local, "trenes": trenes}


@pytest.fixture()
def db():
    from collector.db import engine, wait_and_create
    try:
        wait_and_create(retries=1)
    except Exception as e:
        pytest.skip(f"BD de test no disponible: {e}")
    with engine.begin() as c:
        for t in ALL_TABLES:
            c.execute(text(f"DELETE FROM {t}"))
    return engine


def load(db, buf):
    from collector.static_load import load_feed
    with db.begin() as conn:
        return load_feed("cer", buf, conn)


def obs_rows(db):
    with db.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT feed, trip_id, service_date, stop_id, delay, time,"
            " source, kind, provider_ts, observed_at FROM observations"
            " ORDER BY id")).mappings()]


class TestTripUpdatesIngestion:
    def test_dedup_successive_polls(self, db):
        from collector.realtime import poll_trip_updates
        load(db, base_zip())
        ts = _l(TODAY, 10, 35)
        p = tu_payload(ts, [("T1", [("S2", 300, _mid(TODAY) + 10 * 3600 + 2100)])])
        poll_trip_updates("cer", data=p, now=ts)
        assert len(obs_rows(db)) == 1
        poll_trip_updates("cer", data=p, now=ts + 30)
        assert len(obs_rows(db)) == 1      # mismo delay+time -> dedup
        p2 = tu_payload(ts + 60,
                        [("T1", [("S2", 600, _mid(TODAY) + 10 * 3600 + 2400)])])
        poll_trip_updates("cer", data=p2, now=ts + 60)
        rows = obs_rows(db)
        assert len(rows) == 2              # cambió el delay -> nueva obs
        assert {r["delay"] for r in rows} == {300, 600}
        assert all(r["kind"] == "prediction" and r["source"] == "trip_update"
                   for r in rows)

    def test_identity_across_day_change(self, db):
        """Un servicio de día Y con dep 25:30 observado a la 01:35 del día
        D conserva service_date=Y (cruce de medianoche)."""
        from collector.realtime import poll_trip_updates
        load(db, base_zip())
        # T2 dep@S2 = 25:30 del día Y -> evento real D 01:35, delay 300 s
        ev = _mid(Y) + 25 * 3600 + 1800 + 300
        p = tu_payload(ev, [("T2", [("S2", 300, ev)])])
        poll_trip_updates("cer", data=p, now=ev)
        rows = obs_rows(db)
        assert len(rows) == 1
        assert str(rows[0]["service_date"]) == Y.isoformat()

    def test_same_trip_two_days_are_distinct_instances(self, db):
        """T1 corre ayer y hoy: las observaciones de cada día quedan
        separadas por service_date."""
        from collector.realtime import poll_trip_updates
        load(db, base_zip())
        ev_y = _mid(Y) + 10 * 3600 + 2100      # ayer 10:35 retraso 5m
        ev_t = _mid(TODAY) + 10 * 3600 + 2100  # hoy 10:35
        poll_trip_updates("cer",
                          data=tu_payload(ev_y, [("T1", [("S2", 300, ev_y)])]),
                          now=ev_y)
        poll_trip_updates("cer",
                          data=tu_payload(ev_t, [("T1", [("S2", 300, ev_t)])]),
                          now=ev_t)
        rows = obs_rows(db)
        assert len(rows) == 2    # el cambio de día NO deduplica entre sí
        assert {str(r["service_date"]) for r in rows} == {str(Y), str(TODAY)}

    def test_extreme_delay_still_resolves_when_consistent(self, db):
        """Retraso de 14 h pero coherente con el horario: la instancia
        sigue siendo la de hoy (el feed dice time - delay = programado)."""
        from collector.realtime import poll_trip_updates
        load(db, base_zip())
        dep = _mid(TODAY) + 10 * 3600          # T1 dep@S1 10:00
        ev = dep + 14 * 3600                   # evento estimado 00:00 D+1
        p = tu_payload(ev, [("T1", [("S1", 14 * 3600, ev)])])
        poll_trip_updates("cer", data=p, now=ev)
        rows = obs_rows(db)
        assert len(rows) == 1
        assert str(rows[0]["service_date"]) == TODAY.isoformat()

    def test_inconsistent_observation_is_not_inferred(self, db):
        """time y delay incoherentes (el evento no encaja con ningún día
        programado): svc queda NULL — no se inventa."""
        from collector.realtime import poll_trip_updates
        load(db, base_zip())
        # T3 dep@S1 12:00. Feed dice time=09:00 con delay=15h ->
        # programado implícito = 18:00 del día anterior -> fuera de tol.
        ev = _mid(TODAY) + 9 * 3600
        p = tu_payload(ev, [("T3", [("S1", 15 * 3600, ev)])])
        poll_trip_updates("cer", data=p, now=ev)
        rows = obs_rows(db)
        assert len(rows) == 1
        assert rows[0]["service_date"] is None

    def test_missing_time_is_honest_null(self, db):
        """Sin time del feed no hay ancla: svc=None."""
        from collector.realtime import poll_trip_updates
        load(db, base_zip())
        p = {"header": {"timestamp": _l(TODAY, 10)},
             "entity": [{"id": "T1", "tripUpdate": {
                 "trip": {"tripId": "T1"},
                 "stopTimeUpdate": [{"stopId": "S2",
                                     "arrival": {"delay": 300}}]}}]}
        poll_trip_updates("cer", data=p, now=_l(TODAY, 10, 5))
        rows = obs_rows(db)
        assert len(rows) == 1 and rows[0]["service_date"] is None


class TestFleetIngestion:
    def test_fleet_resolves_via_calendar(self, db):
        from collector.realtime import poll_fleet
        load(db, base_zip())
        # 22:10 local, tren con 7 min de retraso en S1 (dep 22:00)? No:
        # usamos T1 dep@S1 10:00 -> feed_ts 10:07 con retraso 7 min.
        fts = datetime(TODAY.year, TODAY.month, TODAY.day, 10, 7, tzinfo=TZINFO)
        p = fleet_payload(fts.strftime("%Y-%m-%dT%H:%M:%S"),
                          [("T1", 7, "S1", "S2",
                            fts.replace(hour=10, minute=38)
                            .strftime("%Y-%m-%dT%H:%M:%S"))])
        now = int(fts.timestamp())
        poll_fleet(data=p, now=now)
        rows = obs_rows(db)
        assert len(rows) == 1
        r = rows[0]
        assert str(r["service_date"]) == TODAY.isoformat()
        assert r["kind"] == "reported" and r["source"] == "fleet"
        assert r["delay"] == 7 * 60

    def test_fleet_dedup_by_instance(self, db):
        from collector.realtime import poll_fleet
        load(db, base_zip())
        fts = datetime(TODAY.year, TODAY.month, TODAY.day, 10, 7, tzinfo=TZINFO)
        p = fleet_payload(fts.strftime("%Y-%m-%dT%H:%M:%S"),
                          [("T1", 7, "S1", "S2", None)])
        now = int(fts.timestamp())
        poll_fleet(data=p, now=now)
        poll_fleet(data=p, now=now + 45)     # mismo retraso -> dedup
        assert len(obs_rows(db)) == 1
        p2 = fleet_payload(fts.strftime("%Y-%m-%dT%H:%M:%S"),
                           [("T1", 12, "S1", "S2", None)])
        poll_fleet(data=p2, now=now + 90)
        assert len(obs_rows(db)) == 2

    def test_fleet_ambiguous_day_is_null(self, db):
        """Medianoche exacta entre dos instancias diarias equidistantes:
        svc=None (no se infiere)."""
        from collector.realtime import poll_fleet
        load(db, base_zip())
        # T3 dep@S1 = 12:00, en días Y y D. feed_ts = 00:00 de D con
        # retraso 0 -> approx = medianoche: err igual a ambos candidatos.
        fts = datetime(TODAY.year, TODAY.month, TODAY.day, 0, 0, tzinfo=TZINFO)
        p = fleet_payload(fts.strftime("%Y-%m-%dT%H:%M:%S"),
                          [("T3", 0, "S1", "S3", None)])
        poll_fleet(data=p, now=int(fts.timestamp()))
        rows = obs_rows(db)
        assert len(rows) == 1
        assert rows[0]["service_date"] is None


class TestScheduleSnapshot:
    def test_snapshot_frozen_after_day_closes(self, db):
        """La recarga del GTFS no altera el denominador de un día pasado:
        el dep capturado para ayer sigue siendo el de la versión 1."""
        load(db, base_zip(dep_s1="10:00:00"))
        with db.connect() as c:
            dep_y = c.execute(text(
                "SELECT dep FROM circulation_stop"
                " WHERE feed='cer' AND trip_id='T1' AND day=:d AND seq=1"),
                {"d": Y}).scalar()
            assert dep_y == 36000  # 10:00
        # recarga con horario cambiado (T1 dep@S1 10:15)
        load(db, base_zip(dep_s1="10:15:00"))
        with db.connect() as c:
            dep_y2 = c.execute(text(
                "SELECT dep FROM circulation_stop"
                " WHERE feed='cer' AND trip_id='T1' AND day=:d AND seq=1"),
                {"d": Y}).scalar()
            dep_t = c.execute(text(
                "SELECT dep FROM circulation_stop"
                " WHERE feed='cer' AND trip_id='T1' AND day=:d AND seq=1"),
                {"d": TODAY}).scalar()
            caps = {r.day: (r.closed, r.late) for r in c.execute(text(
                "SELECT day, closed, late FROM sched_capture WHERE feed='cer'"
            )).mappings()}
        assert dep_y2 == 36000          # día pasado: congelado
        assert dep_t == 36900           # hoy: refrescado con la v2
        assert caps[Y] == (1, 1)        # cerrado y marcado 'late'
        assert caps[TODAY][0] == 0      # hoy sigue abierto

    def test_observation_survives_gtfs_reload(self, db):
        """Una obs escrita con la versión 1 conserva su service_date tras
        recargar el GTFS (la identidad no se recalcula)."""
        from collector.realtime import poll_trip_updates
        load(db, base_zip())
        ev = _mid(TODAY) + 10 * 3600 + 2100
        p = tu_payload(ev, [("T1", [("S2", 300, ev)])])
        poll_trip_updates("cer", data=p, now=ev)
        load(db, base_zip(dep_s1="10:15:00", arr_s2="10:45:00"))
        rows = obs_rows(db)
        assert len(rows) == 1
        assert str(rows[0]["service_date"]) == TODAY.isoformat()


class TestCaptureStartAndQueries:
    def test_capture_start_marked_per_source(self, db):
        from collector.realtime import poll_fleet, poll_trip_updates
        load(db, base_zip())
        poll_trip_updates("cer", data=tu_payload(
            _l(TODAY, 10), [("T1", [("S2", 0, _mid(TODAY) + 10 * 3600)])]),
            now=_l(TODAY, 10))
        poll_fleet(data=fleet_payload(
            datetime(TODAY.year, TODAY.month, TODAY.day, 10, 5, tzinfo=TZINFO)
            .strftime("%Y-%m-%dT%H:%M:%S"),
            [("T1", 0, "S1", "S2", None)]), now=_l(TODAY, 10, 5))
        with db.connect() as c:
            keys = {r[0] for r in c.execute(text(
                "SELECT key FROM meta WHERE key LIKE 'capture_start_%'"))}
        assert "capture_start_cer_trip_update" in keys
        assert "capture_start_cer_fleet" in keys

    def test_punctuality_endpoint_uses_snapshot(self, db):
        """El denominador del histórico sale del snapshot (no del GTFS
        vigente) y la ventana empieza en la captura tipificada."""
        from collector.realtime import poll_trip_updates
        load(db, base_zip())
        ev = _mid(TODAY) + 10 * 3600 + 2100
        poll_trip_updates("cer", data=tu_payload(
            ev, [("T1", [("S2", 300, ev)])]), now=ev)
        from api.main import app
        from fastapi.testclient import TestClient
        r = TestClient(app).get("/api/v1/stations/cer/S2/punctuality?days=30")
        assert r.status_code == 200
        d = r.json()
        # la ventana se acota al inicio efectivo de captura (hoy), no a 30d
        assert d["window"]["from"] == TODAY.isoformat()
        assert d["circulations_scheduled"] == 2   # T1 y T2 paran en S2 hoy
        assert d["circulations_with_rt"] == 1
        assert d["semantics"] == "reported_delay"


def _client():
    from api.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def _seed_lines(db, n_trips=35):
    """Línea C9 con n_trips viajes diarios (ayer y hoy) más geo mínimo."""
    trips = [(f"T{i:03d}", "R9", "SVC1") for i in range(n_trips)]
    stop_times = []
    for i, (t, _r, _s) in enumerate(trips):
        h = 6 + (i % 14)                    # salidas entre 06:00 y 19:00
        stop_times += [(t, f"{h:02d}:00:00", f"{h:02d}:00:00", "S1", 1),
                       (t, f"{h:02d}:30:00", f"{h:02d}:30:00", "S2", 2)]
    z = gtfs_zip(
        stops=[("S1", "Origen"), ("S2", "Intermedia"), ("S3", "Destino")],
        routes=[("R9", "C9")], trips=trips,
        cal_dates=[("SVC1", Y), ("SVC1", TODAY)],
        stop_times=stop_times)
    load(db, z)
    # forzar el inicio de captura tipificada hacia atrás para que las
    # circulaciones de ayer también cuenten como monitorizadas
    past = str(int(time.mktime((Y - timedelta(days=3)).timetuple())))
    with db.begin() as c:
        for k in ("capture_start_cer_fleet", "capture_start_cer_trip_update",
                  "capture_start_ld_trip_update"):
            c.execute(text("INSERT INTO meta(key,value) VALUES(:k,:v)"
                           " ON CONFLICT(key) DO UPDATE SET value=:v"),
                      {"k": k, "v": past})
    return trips


def _ingest_reported(db, trips, day, delay_min=8):
    """Una obs 'reported' por viaje en S1 del día dado (flota informa en
    la parada actual ~5 min después de la salida programada)."""
    from collector.realtime import poll_fleet
    for h in range(6, 20):   # por hora de salida, feed_ts coherente
        group = [(t, delay_min + (i % 4) * 3, "S1", "S2", None)
                 for i, (t, _r, _s) in enumerate(trips) if 6 + i % 14 == h]
        if not group:
            continue
        fts = datetime(day.year, day.month, day.day, h, 5, tzinfo=TZINFO)
        poll_fleet(data=fleet_payload(fts.strftime("%Y-%m-%dT%H:%M:%S"),
                                      group), now=int(fts.timestamp()))


class TestStatsApi:
    def test_stats_gate_and_separation(self, db):
        trips = _seed_lines(db)
        _ingest_reported(db, trips, Y, 8)
        _ingest_reported(db, trips, TODAY, 10)
        r = _client().get("/api/v1/stats/delays",
                          params={"line": "C9"})
        assert r.status_code == 200
        d = r.json()
        rep = d["kinds"]["reported"]
        assert rep["with_data"] == 70 and rep["scheduled"] == 70
        assert rep["coverage_pct"] == 100.0  # 35/35 en el único día cerrado
        assert rep["days_observed"] == 2
        assert rep["delay_median_sec"] is not None
        assert rep["gate_descriptive"]["pass"] is True
        assert rep["gate_comparative"]["pass"] is False  # faltan días/n
        assert d["kinds"]["prediction"]["with_data"] == 0
        assert d["semantics"]                     # siempre explícita
        assert d["days_monitored"] == 2

    def test_stats_gate_blocks_small_sample(self, db):
        trips = _seed_lines(db, n_trips=5)
        _ingest_reported(db, trips, TODAY, 5)
        d = _client().get("/api/v1/stats/delays",
                          params={"line": "C9"}).json()
        g = d["kinds"]["reported"]["gate_descriptive"]
        assert g["pass"] is False
        assert g["checks"]["min_instances"]["pass"] is False

    def test_stats_station_scope(self, db):
        trips = _seed_lines(db, n_trips=5)
        _ingest_reported(db, trips, TODAY, 5)
        d = _client().get("/api/v1/stats/delays",
                          params={"station": "cer:S1"}).json()
        rep = d["kinds"]["reported"]
        assert rep["scheduled"] == 10     # 5 viajes x 2 días capturados
        assert rep["with_data"] == 5      # obs solo de hoy
        assert d["links"]["station"] == "/estacion/cer:S1"

    def test_stats_journey_scope(self, db):
        trips = _seed_lines(db, n_trips=4)
        _ingest_reported(db, trips, TODAY, 9)
        d = _client().get("/api/v1/stats/delays",
                          params={"from": "cer:S1", "to": "cer:S2"}).json()
        rep = d["kinds"]["reported"]
        # el numerador cuenta obs EN destino (S2): flota informa en S1
        assert rep["scheduled"] == 8      # 4 viajes x 2 días
        assert rep["with_data"] == 0
        assert d["links"]["journey"] == "/trayecto?o=cer:S1&d=cer:S2"

    def test_stats_hour_filter(self, db):
        trips = _seed_lines(db, n_trips=6)
        _ingest_reported(db, trips, TODAY, 5)
        d = _client().get("/api/v1/stats/delays",
                          params={"line": "C9", "hour_from": "06:00",
                                  "hour_to": "09:00"}).json()
        # salidas 06:00-09:00 inclusive: trips en 06,07,08,09 = 4 x 2 días
        assert d["kinds"]["reported"]["scheduled"] == 8
        assert d["window"]["hour"] == ["06:00", "09:00"]

    def test_stats_compare_disabled(self, db):
        _seed_lines(db, n_trips=3)
        d = _client().get("/api/v1/stats/compare",
                          params={"by": "line", "feed": "ld"}).json()
        assert d["enabled"] is False
        assert "gate" in d and "min_units" in d["gate"]

    def test_stats_options_shape(self, db):
        _seed_lines(db, n_trips=2)
        d = _client().get("/api/v1/stats/options").json()
        assert "nucleos" in d and "gate" in d
        assert d["gate"]["comparative"]["min_units"] >= 2

    def test_stats_bad_params(self, db):
        assert _client().get("/api/v1/stats/delays",
                             params={"hour_from": "10:00"}).status_code == 400
        assert _client().get("/api/v1/stats/delays",
                             params={"station": "XX"}).status_code == 400
