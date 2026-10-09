"""Motor de anomalías del collector: señal, confirmación, cierre y RT caído.

Todas las evaluaciones usan un `now` explícito (tiempo simulado)."""
import pytest
from dbfix import TODAY, epoch
from sqlalchemy import text

T0 = epoch(TODAY, 7 * 3600 + 900)  # 07:15 local: viajes base en marcha
C4 = ("10", "c4")                     # Madrid · C4
BASE = "MAD_C4A_0700"                 # viaje base (route 10T0013C4a, 07:00-07:30)
C4_TRAINS = ["MAD_C4A_0700", "MAD_C4A_0701", "MAD_C4A_0702", "MAD_C4AX_0710"]


def _add_trips(engine, ids, route="10T0013C4a", base=BASE):
    """Copias del viaje base (paradas, trip_span, mismo servicio S_ALL)."""
    with engine.begin() as c:
        for tid in ids:
            c.execute(text("""INSERT INTO trips (feed, trip_id, route_id, service_id,
                train_number) VALUES ('cer', :t, :r, 'S_ALL', :n)"""),
                {"t": tid, "r": route, "n": tid[-4:]})
            c.execute(text("""INSERT INTO stop_times (feed, trip_id, seq, stop_id, arr, dep)
                SELECT feed, :t, seq, stop_id, arr, dep FROM stop_times
                WHERE feed='cer' AND trip_id=:b"""), {"t": tid, "b": base})
            c.execute(text("""INSERT INTO trip_span (feed, trip_id, first_dep, last_arr, n_stops)
                SELECT feed, :t, first_dep, last_arr, n_stops FROM trip_span
                WHERE feed='cer' AND trip_id=:b"""), {"t": tid, "b": base})


def _rt(engine, now, delays, meta_age=0, fleet=None):
    """Refresca RT a `now`: delays {trip_id: seg}, fleet {trip_id: min}.
    Los trenes no incluidos dejan de ser frescos. meta_age simula feed caído."""
    with engine.begin() as c:
        c.execute(text("""INSERT INTO meta (key, value) VALUES ('rt_trip_updates_cer', :v)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"""),
            {"v": str(now - meta_age)})
        for tid, d in delays.items():
            c.execute(text("""INSERT INTO rt_trip (feed, trip_id, delay, next_stop_id,
                next_stop_time, updated_at, first_seen)
                VALUES ('cer', :t, :d, '17000', :n, :u, :u)
                ON CONFLICT (feed, trip_id) DO UPDATE SET delay=EXCLUDED.delay,
                  next_stop_time=EXCLUDED.next_stop_time, updated_at=EXCLUDED.updated_at"""),
                {"t": tid, "d": d, "n": now + 600, "u": now})
        c.execute(text("DELETE FROM rt_fleet WHERE feed='cer'"))
        for tid, m in (fleet or {}).items():
            c.execute(text("""INSERT INTO rt_fleet (feed, trip_id, delay_min, ts)
                VALUES ('cer', :t, :m, :u)"""), {"t": tid, "m": m, "u": now})


def _run(engine, now):
    from collector.anomalies import evaluate
    with engine.begin() as c:
        return evaluate(c, now)


def _episode(engine):
    with engine.connect() as c:
        return c.execute(text("""SELECT * FROM anomaly_episode
            WHERE nucleo_code=:n AND family_slug=:f ORDER BY id DESC LIMIT 1"""),
            {"n": C4[0], "f": C4[1]}).mappings().first()


def _three_of_four(delay=1200):
    """3 de 4 trenes C4 de Madrid con +20 min; el cuarto a tiempo."""
    return {"MAD_C4A_0700": delay, "MAD_C4A_0701": delay,
            "MAD_C4A_0702": delay, "MAD_C4AX_0710": 0}


def _all_on_time():
    return {t: 0 for t in C4_TRAINS}


def _confirm(engine):
    """Lleva el episodio C4 de Madrid a 'confirmada' (señal de T0 a T0+300)."""
    _add_trips(engine, ["MAD_C4A_0701", "MAD_C4A_0702"])
    for dt in (0, 100, 200, 300):
        _rt(engine, T0 + dt, _three_of_four())
        _run(engine, T0 + dt)
    assert _episode(engine)["status"] == "confirmada"


@pytest.mark.integration
def test_signal_opens_observacion_and_confirms_after_persistence(scenario):
    _add_trips(scenario, ["MAD_C4A_0701", "MAD_C4A_0702"])
    _rt(scenario, T0, _three_of_four())
    assert _run(scenario, T0) == {"rt_fresh": True, "candidates": 1,
                                  "opened": 1, "confirmed": 0, "resolved": 0}
    ep = _episode(scenario)
    assert ep["status"] == "observacion" and ep["opened_at"] == T0

    for dt in (100, 200):             # >= 3 evaluaciones, pero solo 200 s
        _rt(scenario, T0 + dt, _three_of_four())
        _run(scenario, T0 + dt)
    ep = _episode(scenario)
    assert ep["status"] == "observacion" and ep["signal_evaluations"] == 3

    _rt(scenario, T0 + 300, _three_of_four())
    assert _run(scenario, T0 + 300)["confirmed"] == 1
    ep = _episode(scenario)
    assert ep["status"] == "confirmada" and ep["confirmed_at"] == T0 + 300
    assert ep["peak_delayed_15"] == 3 and ep["signal_evaluations"] == 4

    # (e) muestras con métricas de la línea
    with scenario.connect() as c:
        rows = c.execute(text("""SELECT ts, monitored, scheduled_now, delayed_15,
            share, signal FROM anomaly_sample WHERE episode_id=:e ORDER BY ts"""),
            {"e": ep["id"]}).mappings().all()
    assert len(rows) == 4 and all(r["signal"] == 1 for r in rows)
    assert rows[0]["monitored"] == 4 and rows[0]["delayed_15"] == 3
    assert rows[0]["share"] == pytest.approx(0.75)
    assert rows[0]["scheduled_now"] == 3    # 07:00-07:30 en vigor a las 07:15


def _clear_steps(engine, start, stop, step=60, meta_age=0):
    """Evaluaciones cada `step` s desde start hasta stop (incluido)."""
    times = list(range(start, stop + 1, step))
    for dt in times:
        _rt(engine, T0 + dt, _all_on_time(), meta_age=meta_age)
        _run(engine, T0 + dt)
    return times


@pytest.mark.integration
def test_delays_drop_resolves_only_after_clear_samples(scenario):
    _confirm(scenario)                    # última señal en T0+300
    # muestras frescas sin señal de T0+400 a T0+880: aún no basta
    _clear_steps(scenario, 400, 940)
    # a T0+940 hay muestras cada 60 s desde T0+400 (>= 510 s de cobertura)
    ep = _episode(scenario)
    assert ep["status"] == "resuelta" and ep["resolved_at"] == T0 + 940
    assert ep["confirmed_at"] == T0 + 300


@pytest.mark.integration
def test_unconfirmed_observacion_resolves_without_confirmation(scenario):
    _add_trips(scenario, ["MAD_C4A_0701", "MAD_C4A_0702"])
    _rt(scenario, T0, _three_of_four())
    _run(scenario, T0)
    _clear_steps(scenario, 100, 1000)
    ep = _episode(scenario)
    assert ep["status"] == "resuelta" and ep["confirmed_at"] is None
    assert ep["resolved_at"] == T0 + 640


@pytest.mark.integration
def test_stale_rt_never_resolves_and_marks_gap(scenario):
    _confirm(scenario)                    # última señal en T0+300
    for dt in (400, 500):
        _rt(scenario, T0 + dt, {}, meta_age=3600)
        assert _run(scenario, T0 + dt) == {"rt_fresh": False}
    ep = _episode(scenario)
    assert ep["status"] == "confirmada" and ep["rt_gap_since"] == T0 + 400
    assert ep["last_eval_at"] == T0 + 500

    _rt(scenario, T0 + 600, _all_on_time())    # RT vuelve: hueco cerrado
    assert _run(scenario, T0 + 600)["rt_fresh"] is True
    ep = _episode(scenario)
    assert ep["rt_gap_since"] is None and ep["status"] == "confirmada"

    _clear_steps(scenario, 660, 1140)     # muestras frescas desde T0+600
    assert _episode(scenario)["status"] == "resuelta"
    assert _episode(scenario)["resolved_at"] == T0 + 1140


@pytest.mark.integration
def test_rt_gap_of_20_min_after_signal_does_not_resolve(scenario):
    """Señal hasta T0+300, RT caído 20 min, luego RT limpio: no resuelve
    al volver, solo tras muestras frescas sin señal que cubran la ventana."""
    _confirm(scenario)                    # última señal en T0+300
    for dt in range(360, 1500, 60):       # 20 min sin RT fresco
        _rt(scenario, T0 + dt, {}, meta_age=3600)
        assert _run(scenario, T0 + dt) == {"rt_fresh": False}
    ep = _episode(scenario)
    assert ep["status"] == "confirmada" and ep["rt_gap_since"] == T0 + 360
    with scenario.connect() as c:          # ningún sample escrito durante el hueco
        n = c.execute(text("SELECT count(*) FROM anomaly_sample WHERE ts > :t"),
                      {"t": T0 + 300}).scalar()
    assert n == 0

    _clear_steps(scenario, 1500, 2030)    # RT vuelve y está limpio
    ep = _episode(scenario)
    assert ep["rt_gap_since"] is None
    assert ep["status"] == "confirmada"   # sin resolver al volver (solo 1 muestra)

    _rt(scenario, T0 + 2040, _all_on_time())   # 2040 s: cubre >= 510 s desde 1500
    _run(scenario, T0 + 2040)
    ep = _episode(scenario)
    assert ep["status"] == "resuelta" and ep["resolved_at"] == T0 + 2040


@pytest.mark.integration
def test_share_rule_15_percent_is_not_a_candidate(scenario):
    from collector.anomalies import line_metrics
    ids = [f"MAD_D_{i:02d}" for i in range(20)]
    _add_trips(scenario, ids)
    delays = {t: (1200 if i < 3 else 0) for i, t in enumerate(ids)}
    _rt(scenario, T0, delays)
    with scenario.connect() as c:
        m = line_metrics(c, T0)[C4]
    assert m["monitored"] == 20 and m["delayed_15"] == 3
    assert _run(scenario, T0) == {"rt_fresh": True, "candidates": 0,
                                  "opened": 0, "confirmed": 0, "resolved": 0}
    assert _episode(scenario) is None


@pytest.mark.integration
def test_implausible_fleet_delay_is_ignored(scenario):
    from collector.anomalies import line_metrics
    _add_trips(scenario, ["MAD_C4A_0701", "MAD_C4A_0702"])
    delays = {"MAD_C4A_0700": 0, "MAD_C4A_0701": 1200,
              "MAD_C4A_0702": 1200, "MAD_C4AX_0710": 1200}
    # -1438 min de flota (cruce de medianoche): se usa la predicción RT
    _rt(scenario, T0, delays, fleet={"MAD_C4A_0700": -1438})
    with scenario.connect() as c:
        m = line_metrics(c, T0)[C4]
    by_trip = {t["trip_id"]: t["delay_sec"] for t in m["trains"]}
    assert by_trip["MAD_C4A_0700"] == 0
    assert m["delayed_15"] == 3 and m["monitored"] == 4
    assert _run(scenario, T0)["candidates"] == 1


def test_effective_delay_rule():
    from collector.anomalies import effective_delay
    assert effective_delay(0, -1438) == 0          # flota fuera de rango
    assert effective_delay(0, 20) == 1200          # flota plausible manda
    assert effective_delay(0, 600) == 36000
    assert effective_delay(50000, None) is None    # RT fuera de rango: sin dato
    assert effective_delay(None, None) is None
