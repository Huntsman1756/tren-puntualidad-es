"""Posibles incidencias inferidas de retrasos RT (/anomalias)."""
import time

import pytest
from sqlalchemy import text

ENDPOINT = "/api/v1/anomalias"


def _rt(engine, trips, now=None, meta_age=0):
    """trips: [(trip_id, delay_sec, fleet_delay_min|None)] con dato RT fresco."""
    now = int(time.time()) if now is None else now
    with engine.begin() as c:
        c.execute(text("""INSERT INTO meta (key, value) VALUES ('rt_trip_updates_cer', :v)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"""),
            {"v": str(now - meta_age)})
        for trip_id, delay, fleet in trips:
            c.execute(text("""INSERT INTO rt_trip (feed, trip_id, delay, next_stop_id,
                next_stop_time, updated_at) VALUES ('cer', :t, :d, '17000', :n, :u)"""),
                {"t": trip_id, "d": delay, "n": now + 600, "u": now})
            if fleet is not None:
                c.execute(text("""INSERT INTO rt_fleet (feed, trip_id, delay_min, ts)
                    VALUES ('cer', :t, :m, :u)"""), {"t": trip_id, "m": fleet, "u": now})


def _extra_trips(engine, route, base, new_ids):
    """Copias de un viaje (mismo servicio y paradas) para tener varios trenes."""
    with engine.begin() as c:
        for tid in new_ids:
            c.execute(text("""INSERT INTO trips (feed, trip_id, route_id, service_id,
                train_number) VALUES ('cer', :t, :r, 'S_ALL', :n)"""),
                {"t": tid, "r": route, "n": tid[-4:]})
            c.execute(text("""INSERT INTO stop_times (feed, trip_id, seq, stop_id, arr, dep)
                SELECT feed, :t, seq, stop_id, arr, dep FROM stop_times
                WHERE feed='cer' AND trip_id=:b"""), {"t": tid, "b": base})


C4A_TRIPS = ["MAD_C4A_0700", "MAD_C4A_0701", "MAD_C4A_0702"]


@pytest.mark.integration
def test_three_delayed_c4a_trains_make_one_signal(client, scenario):
    _extra_trips(scenario, "10T0013C4a", "MAD_C4A_0700", C4A_TRIPS[1:])
    _rt(scenario, [(t, 1200, None) for t in C4A_TRIPS]
        + [("AST_C1_0600", 2400, None)])            # Asturias: 1 tren, no señal
    r = client.get(ENDPOINT).json()
    assert r["rt_fresh"] is True and r["rt_age_sec"] <= 60
    assert len(r["items"]) == 1
    it = r["items"][0]
    assert it["nucleo"]["slug"] == "madrid"
    assert it["line"] == {"code": "C4", "slug": "c4", "url": "/lineas/madrid/c4",
                          "label": "C4 · Madrid", "color": it["line"]["color"]}
    assert (it["monitored"], it["delayed_15"], it["delayed_30"]) == (3, 3, 0)
    assert it["max_delay_sec"] == 1200 and it["median_delay_sec"] == 1200
    assert it["official_alerts"] == 0
    assert it["scheduled_now"] >= 0
    assert len(it["trains"]) == 3
    assert it["trains"][0]["delay_source"] == "predicted"
    assert r["rule"]["min_delayed"] == 3 and r["rule"]["delay_min_sec"] == 900
    assert r["semantics"].startswith("Posible incidencia inferida")


@pytest.mark.integration
def test_implausible_fleet_delay_is_ignored_and_pair_rule(client, scenario):
    # MAD_C1_0600: flota -1438 min (absurdo) -> cuenta el retraso predicho (1200 s)
    _rt(scenario, [("MAD_C1_0600", 1200, -1438), ("MAD_C1_2350", 1200, None)])
    it = client.get(ENDPOINT, params={"nucleo": "madrid", "linea": "c1"}).json()["items"]
    assert len(it) == 1 and it[0]["line"]["slug"] == "c1"
    assert it[0]["delayed_15"] == 2 and it[0]["monitored"] == 2
    t = {x["trip_id"]: x for x in it[0]["trains"]}
    assert t["MAD_C1_0600"]["delay_sec"] == 1200
    assert t["MAD_C1_0600"]["delay_source"] == "predicted"


@pytest.mark.integration
def test_stale_rt_feed_returns_no_items(client, scenario):
    _extra_trips(scenario, "10T0013C4a", "MAD_C4A_0700", C4A_TRIPS[1:])
    _rt(scenario, [(t, 1200, None) for t in C4A_TRIPS], meta_age=3600)
    r = client.get(ENDPOINT).json()
    assert r["rt_fresh"] is False and r["rt_age_sec"] >= 3600
    assert r["items"] == []


@pytest.mark.integration
def test_filters_and_unknown_slugs(client, scenario):
    _extra_trips(scenario, "10T0013C4a", "MAD_C4A_0700", C4A_TRIPS[1:])
    _rt(scenario, [(t, 1200, None) for t in C4A_TRIPS])
    assert len(client.get(ENDPOINT, params={"nucleo": "madrid"}).json()["items"]) == 1
    assert len(client.get(ENDPOINT, params={"nucleo": "madrid", "linea": "c4a"}).json()["items"]) == 1
    assert client.get(ENDPOINT, params={"nucleo": "asturias"}).json()["items"] == []
    assert client.get(ENDPOINT, params={"nucleo": "narnia"}).status_code == 404
    assert client.get(ENDPOINT, params={"nucleo": "madrid", "linea": "zz9"}).status_code == 404
