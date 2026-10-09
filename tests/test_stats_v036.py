"""Estadísticas v0.3.6 sobre PostgreSQL real: identidad de línea
(line_route), días representativos, gate de comparativa y serie diaria.

El escenario de dbfix aporta rutas y line_route; aquí se siembran
circulaciones, snapshots, observaciones y salud de captura a mano para
controlar cada caso de representatividad.
"""
from datetime import timedelta

import pytest
from dbfix import TODAY, epoch
from sqlalchemy import text

pytestmark = pytest.mark.integration

PRE = TODAY - timedelta(days=6)    # antes del inicio de captura
START = TODAY - timedelta(days=5)  # día de inicio de captura (tarde)
LATE = TODAY - timedelta(days=4)   # snapshot retrospectivo (late=1)
CLEAN = TODAY - timedelta(days=3)  # día válido
GAP = TODAY - timedelta(days=2)    # hueco de 2 h entre sondeos
DAYS = [PRE, START, LATE, CLEAN, GAP, TODAY]
WINDOW = {"date_from": str(PRE), "date_to": str(TODAY)}

# trips del escenario (dbfix): feed, trip_id, route_id
CIRC = [
    ("cer", "MAD_C1_0600", "10T0001C1"),
    ("cer", "MAD_C1_2350", "10T0001C1"),
    ("cer", "MAD_C4A_0700", "10T0013C4a"),
    ("cer", "MAD_C4AX_0710", "10T0099C4A"),
    ("cer", "AST_C1_0600", "20T0001C1"),
]


def _seed(conn):
    start = epoch(START, 9 * 3600)   # captura empezada a las 09:00
    for src in ("fleet", "trip_update"):
        conn.execute(text("INSERT INTO meta(key,value) VALUES(:k,:v)"),
                     {"k": f"capture_start_cer_{src}", "v": str(start)})
    conn.execute(text("INSERT INTO meta(key,value) VALUES('capture_start_ld_trip_update', :v)"),
                 {"v": str(start)})
    for d in DAYS:
        conn.execute(text("""INSERT INTO sched_capture (feed, day, captured_at,
            closed, late) VALUES ('cer', :d, :now, :closed, :late)"""),
            {"d": d, "now": epoch(d, 0), "closed": int(d < TODAY),
             "late": int(d == LATE)})
        conn.execute(text("""INSERT INTO circulation (feed, day, trip_id,
            route_id, first_stop, last_stop, dep_secs, arr_secs, n_stops)
            VALUES ('cer', :d, :t, :r, '17000', '18000', 0, 0, 2)"""),
            [{"d": d, "t": t, "r": r} for _f, t, r in CIRC])
    conn.execute(text("""INSERT INTO circulation (feed, day, trip_id, route_id,
        first_stop, last_stop, dep_secs, arr_secs, n_stops)
        VALUES ('ld', :d, 'LD_03100', 'LD_AVE_MAD_BCN', '17000', '71801', 0, 0, 2)"""),
        {"d": CLEAN})
    # salud de captura: PRE sin registro, START tardío, LATE/CLEAN sanos,
    # GAP con hueco de 2 h, TODAY sin registro (día en curso)
    health = [(CLEAN, 3, 23 * 3600 + 2700, 600), (LATE, 3, 23 * 3600 + 2700, 600),
              (GAP, 3, 23 * 3600 + 2700, 7200), (START, 9, 23 * 3600 + 2700, 600)]
    for src in ("fleet", "trip_update"):
        conn.execute(text("""INSERT INTO capture_health (feed, source, day, polls,
            first_ts, last_ts, max_gap_sec) VALUES ('cer', :s, :d, 10, :a, :b, :g)"""),
            [{"s": src, "d": d, "a": epoch(d, a), "b": epoch(d, b), "g": g}
             for d, a, b, g in health])
    # observaciones 'reported' de flota en los dos trips C4 de cada día
    delays = {PRE: (3000, 3000), START: (5000, 5000), LATE: (9000, 9000),
              CLEAN: (300, 600), GAP: (7200, 7200), TODAY: (4000, 4000)}
    obs = []
    for d, (d1, d2) in delays.items():
        obs.append(("MAD_C4A_0700", d, d1, "fleet", "reported"))
        obs.append(("MAD_C4AX_0710", d, d2, "fleet", "reported"))
    obs.append(("MAD_C4A_0700", CLEAN, 120, "trip_update", "prediction"))
    obs.append(("MAD_C1_0600", CLEAN, 60, "fleet", "reported"))
    conn.execute(text("""INSERT INTO observations (feed, trip_id, service_date,
        stop_id, delay, time, source, kind, observed_at)
        VALUES ('cer', :t, :d, '17000', :dl, :tm, :s, :k, :o)"""),
        [{"t": t, "d": d, "dl": dl, "tm": epoch(d, 9 * 3600), "s": s,
          "k": k, "o": epoch(d, 9 * 3600)} for t, d, dl, s, k in obs])


@pytest.fixture()
def seeded(scenario):
    with scenario.begin() as c:
        for t in ("observations", "circulation", "circulation_stop",
                  "sched_capture", "capture_health"):
            c.execute(text(f"DELETE FROM {t}"))
        c.execute(text("DELETE FROM meta WHERE key LIKE 'capture_start_%'"))
        _seed(c)
    return scenario


def _get(client, path, **params):
    r = client.get(f"/api/v1/stats/{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


class TestLineIdentity:
    def test_c4_family_only_within_madrid(self, seeded, client):
        c4 = _get(client, "delays", nucleo="madrid", line="c4", **WINDOW)
        # 2 trips C4 por día x 6 días; sin los C1 de Madrid
        assert c4["kinds"]["reported"]["scheduled"] == 12
        assert _get(client, "delays", nucleo="madrid", line="C4",
                    **WINDOW)["kinds"]["reported"]["scheduled"] == 12
        assert _get(client, "delays", nucleo="madrid", line="c4a",
                    **WINDOW)["kinds"]["reported"]["scheduled"] == 12
        assert _get(client, "delays", nucleo="madrid", line="c9",
                    **WINDOW)["kinds"]["reported"]["scheduled"] == 0

    def test_asturias_c1_never_mixes_with_madrid_c1(self, seeded, client):
        ast = _get(client, "delays", nucleo="asturias", line="c1", **WINDOW)
        mad = _get(client, "delays", nucleo="madrid", line="c1", **WINDOW)
        assert ast["kinds"]["reported"]["scheduled"] == 6   # solo 20T0001C1
        assert mad["kinds"]["reported"]["scheduled"] == 12  # solo 10T0001C1
        assert _get(client, "delays", nucleo="madrid",
                    **WINDOW)["kinds"]["reported"]["scheduled"] == 24

    def test_unknown_nucleo_is_400(self, seeded, client):
        assert client.get("/api/v1/stats/delays",
                          params={"nucleo": "narnia"}).status_code == 400


class TestOptions:
    def test_options_shape_exact(self, seeded, client):
        d = _get(client, "options")
        assert set(d) == {"nucleos", "ld_lines", "gate",
                          "representativity", "semantics"}
        assert d["ld_lines"] == [{"line": "AVE", "slug": "ave"}]
        assert d["nucleos"] == [
            {"slug": "madrid", "name": "Madrid", "stations": 3,
             "lines": [{"line": "C1", "slug": "c1", "routes": 1},
                       {"line": "C4", "slug": "c4", "routes": 2}]},
            {"slug": "asturias", "name": "Asturias", "stations": 2,
             "lines": [{"line": "C1", "slug": "c1", "routes": 1}]},
            {"slug": "rodalies-catalunya", "name": "Rodalies de Catalunya",
             "stations": 2,
             "lines": [{"line": "R1", "slug": "r1", "routes": 1}]},
        ]
        assert d["gate"]["comparative"]["min_units"] == 2
        assert "day_gate" in d["representativity"]
        assert all(set(n) == {"slug", "name", "stations", "lines"}
                   for n in d["nucleos"])


class TestRepresentativeDays:
    def test_exclusions_and_gated_median_from_clean_day(self, seeded, client):
        rep = _get(client, "delays", nucleo="madrid", line="c4",
                   **WINDOW)["kinds"]["reported"]
        # solo el día limpio entra en lo gated; el bruto cuenta todo
        assert rep["with_data"] == 2 and rep["with_data_all"] == 12
        assert rep["representative_days"] == 1
        assert rep["days_observed"] == 1
        assert rep["scheduled_representative"] == 2
        assert rep["coverage_pct"] == 100.0
        # mediana/p90 solo de [300, 600] (las 3000..9000 quedan fuera)
        assert rep["delay_median_sec"] == 600
        assert rep["delay_p90_sec"] == 600
        ex = {e["day"]: e["reasons"] for e in rep["excluded_days"]}
        assert "antes_de_captura" in ex[str(PRE)]
        assert "dia_inicio_captura" in ex[str(START)]
        assert "snapshot_retrospectivo" in ex[str(LATE)]
        assert "hueco_captura" in ex[str(GAP)]
        assert "dia_en_curso" in ex[str(TODAY)]
        assert str(CLEAN) not in ex

    def test_reported_on_ld_is_sin_fuente(self, seeded, scenario):
        from api.stats import representative_days
        with scenario.connect() as c:
            rep = representative_days(c, ["ld"], "reported", PRE, TODAY, TODAY)
        assert rep[("ld", CLEAN)]["ok"] is False
        assert "sin_fuente" in rep[("ld", CLEAN)]["reasons"]


class TestCompare:
    def test_units_failing_gate_have_null_stats(self, seeded, client):
        d = _get(client, "compare", by="line", nucleo="madrid", **WINDOW)
        assert d["enabled"] is False
        units = {u["unit"]: u for u in d["units"]}
        assert set(units) == {"c1", "c4"}
        assert {u["label"] for u in d["units"]} == {"C1 · Madrid", "C4 · Madrid"}
        assert units["c4"]["with_data"] == 2
        assert units["c1"]["with_data"] == 1
        for u in d["units"]:
            assert u["gate"]["pass"] is False
            assert u["delay_median_sec"] is None
            assert u["delay_p90_sec"] is None
            assert u["coverage_pct"] is None


class TestDaily:
    def test_daily_series_separates_kinds_and_nulls_failing_days(self, seeded, client):
        d = _get(client, "daily", nucleo="madrid", line="c4", **WINDOW)
        assert set(d) == {"scope", "window", "representativity", "days",
                          "semantics"}
        days = d["days"]
        assert [x["day"] for x in days] == [str(x) for x in DAYS]
        by = {x["day"]: x for x in days}

        clean = by[str(CLEAN)]
        assert clean["representative"] is True and clean["reasons"] == []
        assert clean["scheduled"] == 2
        rep, pred = clean["kinds"]["reported"], clean["kinds"]["prediction"]
        assert rep["with_data"] == 2 and pred["with_data"] == 1  # separados
        assert rep["coverage_pct"] == 100.0
        assert rep["gate_day"] is False                 # 2 < 30 obs
        assert rep["delay_median_sec"] is None
        assert rep["delay_p90_sec"] is None
        assert rep["over_5min_pct"] is None
        assert pred["gate_day"] is False and pred["delay_median_sec"] is None

        late = by[str(LATE)]
        assert late["representative"] is False
        assert "snapshot_retrospectivo" in late["reasons"]
        assert late["kinds"]["reported"]["with_data"] == 2   # conteo visible
        assert late["kinds"]["reported"]["gate_day"] is False
        assert late["kinds"]["reported"]["delay_median_sec"] is None

        today = by[str(TODAY)]
        assert today["representative"] is False
        assert "dia_en_curso" in today["reasons"]

    def test_day_gate_passes_only_with_volume(self):
        from api.stats import DAY_GATE, _daily_entries
        n = DAY_GATE["min_with_data"]
        day = CLEAN
        r = {
            "sched_rows": [{"feed": "cer", "day": day, "n": n + 10}],
            "numer": {"reported": [{"feed": "cer", "service_date": day,
                                    "delay": 400}] * n,
                      "prediction": []},
            "rep": {k: {("cer", day): {"ok": True, "reasons": []}}
                    for k in ("reported", "prediction")},
        }
        e = _daily_entries(r)[0]["kinds"]["reported"]
        assert e["gate_day"] is True
        assert e["delay_median_sec"] == 400
        assert e["over_5min_pct"] == 100.0
