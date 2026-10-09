"""Avisos oficiales: parser (puro), hilos/ingesta y poller WAHA."""
from datetime import date

import httpx
import pytest
from collector.notices import parse_notice, process_pending, run_whatsapp_cycle
from dbfix import epoch
from fixtures_notices import DESCONOCIDO, M1, M2, M3, M4, MULTI, NORMALIZADA
from sqlalchemy import text

from collector import config as cfg
from collector import notices

DAY = date(2026, 10, 9)
T_M1 = epoch(DAY, 7 * 3600 + 40 * 60)   # 07:40
T_M2 = epoch(DAY, 8 * 3600 + 4 * 60)    # 08:04
T_M3 = epoch(DAY, 9 * 3600 + 34 * 60)   # 09:34
T_M4 = epoch(DAY, 10 * 3600 + 20 * 60)  # 10:20


# ------------------------------------------------------------ parser puro

def test_m1_parla_problema_y_origen():
    p = parse_notice(M1, "10")
    assert p["lines"] == ["C4b"]
    assert "Parla" in p["stations"]
    assert {"name": "Parla", "rol": "origen"} in p["mentions"]
    assert {"name": "Colmenar Viejo", "rol": "destino"} in p["mentions"]
    assert p["kind"] == "averia_infraestructura"
    assert p["evidence"]["kind"] == "averia en la infraestructura"
    assert p["status"] == "activa"
    assert p["is_update"] is False


def test_m2_efectos_y_sin_menciones():
    p = parse_notice(M2, "10")
    assert p["lines"] == ["C5"]
    assert p["stations"] == ["Zarzaquemada"]
    assert p["mentions"] == []
    assert p["kind"] == "averia_infraestructura"
    assert {"demoras", "detenciones", "recorrido_modificado"} <= set(p["effects"])
    assert p["status"] == "activa"


def test_m3_destinos_no_son_problema_y_hora_salida():
    p = parse_notice(M3, "10")
    assert p["stations"] == ["Zarzaquemada"]
    assert "Humanes" not in p["stations"]
    assert {"name": "Humanes", "rol": "origen"} in p["mentions"]
    assert {"name": "Móstoles - El Soto", "rol": "destino"} in p["mentions"]
    assert p["salida_hora"] == "09:57h"
    assert "salida_retrasada" in p["effects"]
    assert p["kind"] == "averia_infraestructura"


def test_m4_subsanada_es_en_recuperacion_y_actualizacion():
    p = parse_notice(M4, "10")
    assert p["status"] == "en_recuperacion"
    assert p["is_update"] is True
    assert p["lines"] == ["C5"]
    assert p["stations"] == ["Zarzaquemada"]
    assert p["kind"] == "averia_infraestructura"
    assert p["evidence"]["status"] == "subsanada"
    assert p["evidence"]["is_update"] == "actualizacion"


def test_normalizada_explicita():
    p = parse_notice(NORMALIZADA, "10")
    assert p["status"] == "normalizada"
    assert p["lines"] == ["C3"]


def test_multiples_lineas():
    p = parse_notice(MULTI, "10")
    assert p["lines"] == ["C3", "C4"]


def test_r2_nord_normaliza_a_r2n():
    assert parse_notice("Línea R2 Nord\n\n🟡 Retrasos por obras.", "10")["lines"] == ["R2N"]


def test_texto_desconocido():
    p = parse_notice(DESCONOCIDO, "10")
    assert p["kind"] == "otra"
    assert p["status"] == "activa"
    assert p["lines"] == [] and p["stations"] == []


# ------------------------------------------------- BD: hilos e ingesta

def _add_lines(conn):
    """Añade a la BD de test C5 (Zarzaquemada) y C4b (Parla) del núcleo 10."""
    conn.execute(text("INSERT INTO stops (feed, stop_id, name) VALUES "
                      "('cer','18002','Parla'), ('cer','18003','Zarzaquemada')"))
    conn.execute(text("INSERT INTO routes (feed, route_id, short_name) VALUES "
                      "('cer','10T0500C5','C5'), ('cer','10T0500C4b','C4b')"))
    conn.execute(text("INSERT INTO trips (feed, trip_id, route_id, service_id) VALUES "
                      "('cer','MAD_C5_0600','10T0500C5','S_ALL'), "
                      "('cer','MAD_C4B_0700','10T0500C4b','S_ALL')"))
    conn.execute(text("INSERT INTO stop_times (feed, trip_id, seq, stop_id, arr, dep) VALUES "
                      "('cer','MAD_C5_0600',1,'18003',21600,21600), "
                      "('cer','MAD_C4B_0700',1,'18002',25200,25200)"))
    conn.execute(text("""INSERT INTO line_route (feed, route_id, nucleo_code, line_code,
        line_slug, family_code, family_slug, mode, status, n_trips) VALUES
        ('cer','10T0500C5','10','C5','c5','C5','c5','tren','verified',1),
        ('cer','10T0500C4b','10','C4b','c4b','C4b','c4b','tren','verified',1)"""))


@pytest.fixture()
def nscen(scenario):
    with scenario.begin() as c:
        c.execute(text("DELETE FROM official_notice"))
        _add_lines(c)
    return scenario


def _insert(conn, posted, body, source="manual", channel="cercanias-madrid",
            external_id=None, nucleo="10"):
    return conn.execute(text("""
        INSERT INTO official_notice (source, channel, external_id, posted_at,
            received_at, text, nucleo_code, status, is_update)
        VALUES (:s, :ch, :ext, :p, :p, :t, :n, 'pendiente', 0) RETURNING id"""),
        {"s": source, "ch": channel, "ext": external_id, "p": posted,
         "t": body, "n": nucleo}).scalar()


def _rows(conn):
    return conn.execute(text(
        "SELECT id, thread_id, status, is_update, lines, stations, kind "
        "FROM official_notice ORDER BY posted_at, id")).mappings().all()


@pytest.mark.integration
def test_hilo_c5_zarzaquemada_y_c4b_independiente(nscen):
    with nscen.begin() as c:
        id1 = _insert(c, T_M1, M1)
        id2 = _insert(c, T_M2, M2)
        id3 = _insert(c, T_M3, M3)
        id4 = _insert(c, T_M4, M4)
        stats = process_pending(c, T_M4 + 60)
        assert stats["processed"] == 4
        rows = {r["id"]: r for r in _rows(c)}

    assert rows[id2]["thread_id"] == id2
    assert rows[id3]["thread_id"] == id2
    assert rows[id4]["thread_id"] == id2
    assert rows[id1]["thread_id"] == id1          # otra línea: hilo propio
    assert rows[id4]["status"] == "en_recuperacion"
    assert rows[id3]["status"] == "activa"
    assert rows[id1]["status"] == "activa"
    assert rows[id4]["is_update"] == 1
    assert rows[id1]["lines"] == ["C4b"]
    assert rows[id2]["stations"] == [{"name": "Zarzaquemada", "stop_id": "18003"}]
    assert rows[id1]["stations"] == [{"name": "Parla", "stop_id": "18002"}]


@pytest.mark.integration
def test_sin_actualizar_tras_seis_horas_no_presume_resuelto(nscen):
    with nscen.begin() as c:
        _insert(c, T_M1, M1)
        stats = process_pending(c, T_M1 + cfg.NOTICE_STALE_SEC + 1)
        assert stats["stale"] == 1
        assert c.execute(text("SELECT status FROM official_notice")).scalar() == "sin_actualizar"


@pytest.mark.integration
def test_waha_desactivado_devuelve_cero(nscen, monkeypatch):
    monkeypatch.setattr(cfg, "WAHA_URL", "")
    assert run_whatsapp_cycle() == 0


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def _fake_client(payload_or_exc, seen):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            seen.append((url, params, headers))
            if "/api/sessions/" in url:
                return _FakeResp({"name": "default", "status": "WORKING"})
            if isinstance(payload_or_exc, Exception):
                raise payload_or_exc
            return _FakeResp(payload_or_exc)
    return FakeClient


@pytest.mark.integration
def test_waha_inserta_una_vez_e_idempotente(nscen, monkeypatch):
    monkeypatch.setattr(cfg, "WAHA_URL", "http://waha.test")
    monkeypatch.setattr(cfg, "WAHA_API_KEY", "secreto")
    monkeypatch.setattr(cfg, "WAHA_SESSION", "default")
    monkeypatch.setattr(cfg, "WAHA_CHANNELS", "0029VaABC:cercanias-madrid:10")
    payload = [
        {"id": "msg-2", "timestamp": T_M2, "body": M2},
        {"key": {"id": "msg-3"}, "timestamp": T_M3 * 1000, "body": M3},
    ]
    seen = []
    monkeypatch.setattr(notices.httpx, "Client", _fake_client(payload, seen))

    assert run_whatsapp_cycle() == 2
    assert run_whatsapp_cycle() == 0   # dedupe por (source, channel, external_id)

    url, params, headers = next(s for s in seen if "/channels/" in s[0])
    assert url == "http://waha.test/api/default/channels/0029VaABC/messages/preview"
    assert params == {"downloadMedia": "false", "limit": 100}
    assert headers == {"X-Api-Key": "secreto"}

    with nscen.begin() as c:
        n = c.execute(text("SELECT count(*) FROM official_notice WHERE source='whatsapp'")).scalar()
        st = c.execute(text("SELECT status, nucleo_code FROM official_notice "
                            "WHERE external_id='msg-2'")).mappings().one()
        meta = c.execute(text("SELECT value FROM meta WHERE key='whatsapp_fetch_ok_cercanias-madrid'")).scalar()
    assert n == 2
    assert st["status"] == "activa" and st["nucleo_code"] == "10"
    assert meta is not None


@pytest.mark.integration
def test_waha_error_de_red_registra_meta_y_no_lanza(nscen, monkeypatch):
    monkeypatch.setattr(cfg, "WAHA_URL", "http://waha.test")
    monkeypatch.setattr(cfg, "WAHA_CHANNELS", "0029VaABC:cercanias-madrid:10")
    monkeypatch.setattr(notices.httpx, "Client",
                        _fake_client(httpx.ConnectError("caído"), []))
    assert run_whatsapp_cycle() == 0
    with nscen.begin() as c:
        err = c.execute(text("SELECT value FROM meta WHERE key='whatsapp_fetch_err_cercanias-madrid'")).scalar()
    assert err is not None
