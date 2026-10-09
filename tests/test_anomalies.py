"""Posibles incidencias inferidas (/anomalias): lectura de episodios persistidos."""
import json
import time

import pytest
from sqlalchemy import text

ENDPOINT = "/api/v1/anomalias"


@pytest.fixture(autouse=True)
def _rate_limit_reset():
    """El limitador es un dict global: no dejar que estos tests agoten el cupo
    de los siguientes (todos llegan desde 'testclient')."""
    import api.main as m
    m._rl.clear()
    yield
    m._rl.clear()


def _clean(engine):
    with engine.begin() as c:
        for t in ("anomaly_sample", "anomaly_episode", "official_notice"):
            c.execute(text(f"DELETE FROM {t}"))


def _meta_rt(engine, now, age=0):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO meta (key, value) VALUES ('rt_trip_updates_cer', :v)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"""),
            {"v": str(now - age)})


def _ep(engine, nucleo, fam, status, opened, confirmed=None, resolved=None,
        delayed=3, monitored=4, rt_gap=None):
    metrics = {"monitored": monitored, "scheduled_now": 5, "delayed_15": delayed,
               "delayed_30": 0, "share": delayed / monitored if monitored else None,
               "max_delay_sec": 1200 if delayed else None, "median_delay_sec": 1200,
               "trains": [{"trip_id": "MAD_C4A_0700", "train_number": "22000",
                           "delay_sec": 1200, "next_stop_name": "Getafe Centro"}]}
    with engine.begin() as c:
        return c.execute(text("""INSERT INTO anomaly_episode (nucleo_code, family_slug,
            status, opened_at, confirmed_at, last_signal_at, resolved_at, rt_gap_since,
            evaluations, signal_evaluations, peak_delayed_15, peak_share,
            peak_max_delay_sec, last_metrics)
            VALUES (:n, :f, :s, :o, :c, :ls, :r, :g, 5, 4, :pd, :ps, 1200,
                    CAST(:m AS jsonb)) RETURNING id"""),
            {"n": nucleo, "f": fam, "s": status, "o": opened, "c": confirmed,
             "ls": opened, "r": resolved, "g": rt_gap, "pd": delayed + 1,
             "ps": 0.75, "m": json.dumps(metrics)}).scalar()


def _samples(engine, ep, pts):
    """pts: [(ts, delayed_15)]"""
    with engine.begin() as c:
        c.execute(text("""INSERT INTO anomaly_sample (episode_id, ts, monitored,
            scheduled_now, delayed_15, delayed_30, share, max_delay_sec,
            median_delay_sec, signal) VALUES (:e, :t, 4, 5, :d, 0, 0.5, 1200, 1200, 1)"""),
            [{"e": ep, "t": t, "d": d} for t, d in pts])


@pytest.fixture()
def env(client, scenario):
    now = int(time.time())
    _clean(scenario)
    _meta_rt(scenario, now)
    return scenario, now


@pytest.mark.integration
def test_lifecycle_visibility_and_ordering(client, env):
    eng, now = env
    obs_c1 = _ep(eng, "10", "c1", "observacion", now - 200, delayed=2)
    conf_c4 = _ep(eng, "10", "c4", "confirmada", now - 900, confirmed=now - 500,
                  delayed=3)
    _ep(eng, "10", "c8", "resuelta", now - 4000, confirmed=now - 3000,
        resolved=now - 3600)                       # confirmada y resuelta hace 1 h
    _ep(eng, "10", "c3", "resuelta", now - 1000, confirmed=None,
        resolved=now - 600)                        # descartada: nunca se muestra
    _ep(eng, "10", "c9", "resuelta", now - 30000, confirmed=now - 29000,
        resolved=now - 7 * 3600)                   # >6 h: fuera
    _ep(eng, "20", "c1", "confirmada", now - 900, confirmed=now - 500, delayed=4)

    r = client.get(ENDPOINT).json()
    assert r["rt_fresh"] is True and r["rt_age_sec"] <= 60
    assert r["rule"]["min_monitored"] == 3 and r["rule"]["clear_sec"] == 600
    assert r["rule"]["confirm_sec"] == 300 and r["rule"]["min_share"] == 0.2
    assert r["semantics"].startswith("Posible incidencia inferida")
    got = [(i["status"], i["line"]["slug"], i["nucleo"]["slug"]) for i in r["items"]]
    # confirmada (delayed 4 Asturias antes que 3 Madrid), observacion, resuelta
    assert got == [("confirmada", "c1", "asturias"), ("confirmada", "c4", "madrid"),
                   ("observacion", "c1", "madrid"), ("resuelta", "c8", "madrid")]

    c4 = next(i for i in r["items"] if i["id"] == conf_c4)
    assert c4["line"] == {"code": "C4", "slug": "c4", "url": "/lineas/madrid/c4",
                          "label": "C4 · Madrid", "color": c4["line"]["color"]}
    assert c4["current"]["delayed_15"] == 3 and c4["current"]["share_pct"] == 75.0
    assert c4["current"]["trains"][0]["train_number"] == "22000"
    assert c4["peak"] == {"delayed_15": 4, "share_pct": 75.0, "max_delay_sec": 1200}
    assert c4["duration_sec"] == 900 and c4["resolved_at"] is None
    assert c4["confirmed_at"] == now - 500
    assert c4["official_alerts"] == 0 and c4["official_notices"] == 0
    resolved = next(i for i in r["items"] if i["status"] == "resuelta")
    assert resolved["duration_sec"] == 4000 - 3600
    assert obs_c1 in {i["id"] for i in r["items"]}


@pytest.mark.integration
def test_filters_resolved_toggle_and_unknown_slugs(client, env):
    eng, now = env
    _ep(eng, "10", "c4", "confirmada", now - 900, confirmed=now - 500)
    _ep(eng, "10", "c1", "observacion", now - 200)
    _ep(eng, "20", "c1", "confirmada", now - 900, confirmed=now - 500)
    _ep(eng, "10", "c8", "resuelta", now - 4000, confirmed=now - 3000,
        resolved=now - 3600)
    madrid = client.get(ENDPOINT, params={"nucleo": "madrid"}).json()["items"]
    assert {i["line"]["slug"] for i in madrid} == {"c4", "c1", "c8"}
    assert all(i["nucleo"]["slug"] == "madrid" for i in madrid)
    only_c4a = client.get(ENDPOINT, params={"nucleo": "madrid", "linea": "c4a"}).json()
    assert [i["line"]["slug"] for i in only_c4a["items"]] == ["c4"]
    c1 = client.get(ENDPOINT, params={"linea": "c1"}).json()["items"]
    assert {i["nucleo"]["slug"] for i in c1} == {"madrid", "asturias"}
    sin = client.get(ENDPOINT, params={"nucleo": "madrid",
                                       "incluir_resueltas": "false"}).json()["items"]
    assert "resuelta" not in {i["status"] for i in sin}
    assert client.get(ENDPOINT, params={"nucleo": "narnia"}).status_code == 404
    assert client.get(ENDPOINT, params={"nucleo": "madrid", "linea": "zz9"}).status_code == 404


@pytest.mark.integration
def test_stale_rt_keeps_episodes_with_gap_flag(client, env):
    eng, now = env
    _meta_rt(eng, now, age=3600)
    _ep(eng, "10", "c4", "confirmada", now - 900, confirmed=now - 500,
        rt_gap=now - 1800)
    r = client.get(ENDPOINT).json()
    assert r["rt_fresh"] is False and r["rt_age_sec"] >= 3600
    assert len(r["items"]) == 1 and r["items"][0]["rt_gap_since"] == now - 1800


@pytest.mark.integration
def test_evolution_is_thinned_and_limited_to_3h(client, env):
    eng, now = env
    ep = _ep(eng, "10", "c4", "confirmada", now - 3000, confirmed=now - 2500)
    pts = [(now - 3000 + i * 10, i % 5) for i in range(150)]
    pts.append((now - 4 * 3600, 9))              # fuera de la ventana de 3 h
    _samples(eng, ep, pts)
    it = client.get(ENDPOINT).json()["items"][0]
    evo = it["evolution"]
    assert 2 <= len(evo) <= 60
    assert evo[0]["ts"] == now - 3000 and evo[-1]["ts"] == now - 3000 + 149 * 10
    assert [p["ts"] for p in evo] == sorted(p["ts"] for p in evo)
    assert all(p["ts"] >= now - 3 * 3600 for p in evo)
    assert set(evo[0]) == {"ts", "delayed_15", "monitored", "share_pct",
                           "max_delay_sec", "signal"}
    assert evo[0]["share_pct"] == 50.0 and evo[0]["signal"] is True


@pytest.mark.integration
def test_official_notice_count_matches_family_variant(client, env):
    eng, now = env
    _ep(eng, "10", "c4", "confirmada", now - 900, confirmed=now - 500)
    with eng.begin() as c:
        c.execute(text("""INSERT INTO official_notice (source, channel, external_id,
            posted_at, received_at, text, nucleo_code, lines, stations, status,
            is_update, thread_id) VALUES ('whatsapp', 'cercanias-madrid', 'x1', :p, :p,
            'Incidencia C4a', '10', CAST('["C4a"]' AS jsonb), '[]', 'activa', 0, 701)"""),
            {"p": now - 300})
    it = client.get(ENDPOINT).json()["items"][0]
    assert it["official_notices"] == 1
