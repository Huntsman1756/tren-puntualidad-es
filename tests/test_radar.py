"""Adaptador RadarDeTrenes → rt_ext_ld: escritura, deduplicación,
caducidad, reconciliación por tren+fecha, fallo del proveedor y
superficie `ext` en la API con trazabilidad."""

import json
import time
from datetime import timedelta

import pytest
from dbfix import TODAY
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _fleet_payload(trains):
    return {
        "trains": trains,
        "count": len(trains),
        "updatedAt": "2026-10-09T12:00:00Z",
    }


def _train(code, **kw):
    t = {
        "id": f"ld-{code}",
        "trainCode": code,
        "type": "long_distance",
        "timestamp": "2026-10-09T11:59:50Z",
        "platform": "7",
        "rollingStock": ["112001"],
        "nextStationCode": "71801",
        "nextStationArrival": "2026-10-09T14:30:00+02:00",
        "delayMinutes": 5,
        "productName": "AVE",
    }
    t.update(kw)
    return t


class _Resp:
    def __init__(self, body):
        self._b = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._b


def _patch_radar(monkeypatch, radar_mod, payload=None, exc=None):
    radar_mod.RADAR_ENABLED = True
    if exc is not None:

        def boom(*a, **k):
            raise exc

        monkeypatch.setattr(radar_mod.httpx, "get", boom)
    else:
        monkeypatch.setattr(radar_mod.httpx, "get", lambda *a, **k: _Resp(payload or _fleet_payload([])))


@pytest.fixture()
def radar_mod():
    import collector.radar as m

    old = m.RADAR_ENABLED
    yield m
    m.RADAR_ENABLED = old


def test_disabled_by_default(radar_mod):
    radar_mod.RADAR_ENABLED = False
    assert radar_mod.poll_radar() == 0


def test_writes_rows_and_identity(scenario, radar_mod, monkeypatch):
    payload = _fleet_payload(
        [
            _train("03100"),  # coincide con el trip LD_03100 del fixture
            _train("99999"),  # no existe en nuestro GTFS
            _train(""),  # sin código: se descarta
        ]
    )
    _patch_radar(monkeypatch, radar_mod, payload)
    n = radar_mod.poll_radar()
    assert n == 2  # la vacía no se escribe
    with scenario.connect() as c:
        r = c.execute(
            text(
                "SELECT train_number, service_date, platform, rolling_stock,"
                " next_stop_id, delay_min, product, source"
                " FROM rt_ext_ld ORDER BY train_number"
            )
        ).all()
        assert [x[0] for x in r] == ["03100", "99999"]
        assert r[0][2] == "7" and r[0][6] == "AVE" and r[0][7] == "radar"
        # conciliación: solo 03100 casa con (train_number, service_date)
        src = {r[0]: r[1] for r in c.execute(text("SELECT train_number, identity_src FROM rt_ext_ld")).all()}
        # 03100 cubre varios días -> instancia ambigua marcada, no "verificada"
        assert src["03100"] == "coverage_multi"
        assert src["99999"] == "civil"  # el número no existe en nuestro GTFS
        stats = json.loads(c.execute(text("SELECT value FROM meta WHERE key='radar_stats'")).scalar())
        assert stats["provider_rows"] == 2 and stats["matched"] == 1
        assert stats["identity_src"]["coverage_multi"] == 1
        assert stats["identity_src"]["civil"] == 1


def test_dedup_updates_in_place(scenario, radar_mod, monkeypatch):
    _patch_radar(monkeypatch, radar_mod, _fleet_payload([_train("03100")]))
    radar_mod.poll_radar()
    _patch_radar(monkeypatch, radar_mod, _fleet_payload([_train("03100", platform="9", delayMinutes=12)]))
    radar_mod.poll_radar()
    with scenario.connect() as c:
        rows = c.execute(text("SELECT platform, delay_min FROM rt_ext_ld WHERE train_number='03100'")).all()
        assert len(rows) == 1 and rows[0] == ("9", 12)


def test_provider_failure_keeps_data(scenario, radar_mod, monkeypatch):
    _patch_radar(monkeypatch, radar_mod, _fleet_payload([_train("03100")]))
    radar_mod.poll_radar()
    _patch_radar(monkeypatch, radar_mod, exc=RuntimeError("caido"))
    with pytest.raises(RuntimeError):
        radar_mod.poll_radar()  # el loop del collector captura la excepción
    with scenario.connect() as c:
        assert (
            c.execute(text("SELECT count(*) FROM rt_ext_ld WHERE train_number='03100'")).scalar() == 1
        )  # el dato previo se conserva para fallback


def test_expiry_purges_old(scenario, radar_mod, monkeypatch):
    now = int(time.time())
    with scenario.begin() as c:
        c.execute(
            text("INSERT INTO rt_ext_ld(train_number,service_date,observed_at) VALUES('00000',:d,:o)"),
            {"d": TODAY - timedelta(days=2), "o": now - 30 * 3600},
        )
    _patch_radar(monkeypatch, radar_mod, _fleet_payload([_train("03100")]))
    radar_mod.poll_radar()
    with scenario.connect() as c:
        assert c.execute(text("SELECT count(*) FROM rt_ext_ld WHERE train_number='00000'")).scalar() == 0


def test_ext_block_in_train_endpoint(client, scenario):
    now = int(time.time())
    with scenario.begin() as c:
        c.execute(
            text(
                "INSERT INTO rt_ext_ld(train_number,service_date,platform,"
                "rolling_stock,next_stop_id,next_eta,delay_min,product,"
                "provider_ts,observed_at,source)"
                " VALUES('03100',:d,'7','[\"112001\"]'::jsonb,'71801',:e,5,"
                "'AVE',:p,:o,'radar')"
            ),
            {"d": TODAY + timedelta(days=1), "e": now + 3600, "p": now - 60, "o": now - 30},
        )
    r = client.get("/api/v1/trains/ld/LD_03100", params={"date": str(TODAY + timedelta(days=1))})
    assert r.status_code == 200
    ext = r.json()["ext"]
    assert ext["source"] == "radar" and ext["platform"] == "7"
    assert ext["rolling_stock"] == ["112001"] and ext["stale"] is False
    assert ext["provider_ts"] and ext["observed_at"]


def test_ext_stale_flag(client, scenario):
    now = int(time.time())
    with scenario.begin() as c:
        c.execute(
            text(
                "INSERT INTO rt_ext_ld(train_number,service_date,provider_ts,"
                "observed_at,source)"
                " VALUES('03100',:d,:p,:o,'radar')"
            ),
            {"d": TODAY + timedelta(days=1), "p": now - 3600, "o": now - 3500},
        )
    r = client.get("/api/v1/trains/ld/LD_03100", params={"date": str(TODAY + timedelta(days=1))})
    assert r.json()["ext"]["stale"] is True


def test_ext_absent_for_cer_and_without_row(client):
    r = client.get("/api/v1/trains/cer/MAD_C1_0600")
    assert r.status_code == 200 and r.json()["ext"] is None


def test_service_date_by_coverage_yesterday(scenario, radar_mod, monkeypatch):
    """Un tren cuyo número solo cubre AYER en service_days (p.ej. nocturno
    aún en circulación pasada medianoche) se ancla a esa fecha, no a hoy."""
    with scenario.begin() as c:
        c.execute(
            text(
                "INSERT INTO trips (feed,trip_id,route_id,service_id,train_number)"
                " VALUES('ld','LD_NOCHE','LD_AVE_MAD_BCN','S_Y1','77777')"
            )
        )
        c.execute(text("INSERT INTO service_days VALUES('ld','S_Y1',:d)"), {"d": TODAY - timedelta(days=1)})
    _patch_radar(monkeypatch, radar_mod, _fleet_payload([_train("77777")]))
    radar_mod.poll_radar()
    with scenario.connect() as c:
        sd = c.execute(text("SELECT service_date FROM rt_ext_ld WHERE train_number='77777'")).scalar()
        assert sd == TODAY - timedelta(days=1)
        # única coincidencia verificable -> marcado como 'coverage'
        assert (
            c.execute(text("SELECT identity_src FROM rt_ext_ld WHERE train_number='77777'")).scalar()
            == "coverage"
        )


def test_service_date_prefers_today(scenario, radar_mod, monkeypatch):
    """Si el número cubre hoy Y ayer, gana hoy (instancia vigente); la
    identidad multi-día queda marcada como ambigua, nunca 'verificada'."""
    _patch_radar(monkeypatch, radar_mod, _fleet_payload([_train("03100")]))
    radar_mod.poll_radar()
    with scenario.connect() as c:
        sd = c.execute(text("SELECT service_date FROM rt_ext_ld WHERE train_number='03100'")).scalar()
        assert sd == TODAY
        # el fixture cubre todos los días: identidad multi-día, marcada
        assert (
            c.execute(text("SELECT identity_src FROM rt_ext_ld WHERE train_number='03100'")).scalar()
            == "coverage_multi"
        )
