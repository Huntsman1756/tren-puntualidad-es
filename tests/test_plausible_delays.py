"""Retrasos absurdos y fechas de servicio futuras no deben llegar a observations.

Caso real: flota.json publica retrasoMin=-1438 (≈ -24 h) en trenes que cruzan
medianoche; el ancla salía de mañana y la observación quedaba con service_date
futura. Aquí se fuerzan esos datos sobre el escenario sintético de dbfix.
"""
from datetime import datetime, timedelta

import pytest
from collector.gtfsutil import TZINFO
from dbfix import TODAY, epoch
from sqlalchemy import text

H = 3600
NOW = epoch(TODAY, 6 * H + 600)  # hoy 06:10 local


@pytest.fixture()
def db(scenario):
    with scenario.begin() as c:
        c.execute(text("DELETE FROM observations"))
    return scenario


def _obs(db):
    with db.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT trip_id, service_date, stop_id, delay, time"
            " FROM observations ORDER BY id")).mappings()]


def _stamp(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZINFO).strftime("%Y-%m-%dT%H:%M:%S")


@pytest.mark.integration
def test_fleet_implausible_delay_not_observed(db):
    from collector.realtime import poll_fleet
    # Tren normal: 3 min de retraso en 17000 (dep 06:00), llega 18000 a 06:23
    # (programado 06:20 -> 3 min). Tren absurdo: cruza medianoche, -1438 min.
    p = {"fechaActualizacion": _stamp(NOW), "trenes": [
        {"tripId": "MAD_C1_0600", "codTren": "21000", "codLinea": "C1",
         "retrasoMin": "3", "codEstAct": "17000", "codEstSig": "18000",
         "horaLlegadaSigEst": _stamp(NOW + 13 * 60)},
        {"tripId": "MAD_C1_2350", "codTren": "21001", "codLinea": "C1",
         "retrasoMin": "-1438", "codEstAct": "17000", "codEstSig": "18000",
         "horaLlegadaSigEst": None},
    ]}
    poll_fleet(data=p, now=NOW)

    with db.connect() as c:
        dm = dict(c.execute(text(
            "SELECT trip_id, delay_min FROM rt_fleet")).all())
    # rt_fleet conserva ambas filas, tal cual
    assert dm == {"MAD_C1_0600": 3, "MAD_C1_2350": -1438}

    rows = _obs(db)
    assert [r["trip_id"] for r in rows] == ["MAD_C1_0600"]
    assert str(rows[0]["service_date"]) == TODAY.isoformat()
    assert rows[0]["delay"] == 3 * 60
    assert all(str(r["service_date"]) <= TODAY.isoformat() for r in rows)


@pytest.mark.integration
def test_trip_updates_implausible_delay_not_observed(db):
    from collector.realtime import poll_trip_updates
    data = {
        "header": {"timestamp": NOW},
        "entity": [
            {"id": "a", "tripUpdate": {
                "trip": {"tripId": "MAD_C1_0600"},
                "stopTimeUpdate": [{"stopId": "17000", "departure": {
                    "time": epoch(TODAY, 6 * H) + 120, "delay": 120}}]}},
            {"id": "b", "tripUpdate": {
                "trip": {"tripId": "MAD_C1_2350"},
                "stopTimeUpdate": [{"stopId": "17000", "departure": {
                    "time": NOW + 90000, "delay": 90000}}]}},
        ],
    }
    poll_trip_updates("cer", data=data, now=NOW)

    rows = _obs(db)
    assert [r["trip_id"] for r in rows] == ["MAD_C1_0600"]
    assert rows[0]["delay"] == 120
    assert str(rows[0]["service_date"]) == TODAY.isoformat()

    # rt_stop_update sigue guardando ambas filas (sin cambios de escritura)
    with db.connect() as c:
        n = c.execute(text("SELECT count(*) FROM rt_stop_update")).scalar()
    assert n == 2


@pytest.mark.integration
def test_future_service_date_guard_drops_observation():
    from collector.realtime import _descartar_futuras
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    obs = [
        {"svc": TODAY.isoformat(), "t": "a"},
        {"svc": (TODAY - timedelta(days=1)).isoformat(), "t": "b"},
        {"svc": None, "t": "c"},
        {"svc": tomorrow, "t": "d"},
    ]
    kept = _descartar_futuras(obs, NOW, "cer")
    assert [o["t"] for o in kept] == ["a", "b", "c"]


def test_delay_window_bounds():
    """Límites de la ventana -60 … +600 min (sin BD)."""
    from collector.realtime import _retraso_plausible
    assert _retraso_plausible(None)
    assert _retraso_plausible(-3600) and _retraso_plausible(36000)
    assert _retraso_plausible(0)
    assert not _retraso_plausible(-3601)
    assert not _retraso_plausible(36001)
    assert not _retraso_plausible(-1438 * 60)
    assert not _retraso_plausible(90000)
