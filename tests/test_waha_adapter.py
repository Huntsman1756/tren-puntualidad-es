"""Adaptador WAHA: formas de respuesta, descartes con motivo y degradación.

Unitarios (parser) + integración (ciclo completo sobre PostgreSQL aislada).
"""
import json

import httpx
import pytest
from collector.notices import (
    _messages,
    _msg_fields,
    process_pending,
    run_whatsapp_cycle,
)
from fixtures_notices import DESCONOCIDO, M2, M4
from fixtures_waha import (
    ESTRUCTURA_RARA,
    LARGO,
    LISTA_CON_BASURA,
    MEDIA_SIN_TEXTO,
    MS,
    PLANO,
    PREVIEW_ANIDADO,
    PREVIEW_OPCIONALES_AUSENTES,
    SIN_ID,
    SIN_TS,
    VACIA,
)
from sqlalchemy import text

from collector import config as cfg
from collector import notices

# ---------------------------------------------------------------- unitarios

def test_envoltorio_message():
    item = PREVIEW_ANIDADO[0]
    ext, ts, body, why = _msg_fields(item)
    assert why is None
    assert ext == "false_123@newsletter_AAAA" and ts == 1791505200
    assert body == M2


def test_plano():
    ext, ts, body, why = _msg_fields(PLANO[0])
    assert why is None and ext == "true_999@newsletter_AAAA" and body == M2


def test_timestamp_milisegundos():
    ext, ts, body, why = _msg_fields(MS[0])
    assert why is None and ts == 1791505200


def test_opcionales_ausentes():
    ext, ts, body, why = _msg_fields(PREVIEW_OPCIONALES_AUSENTES[0])
    assert why is None and body == M4


def test_multimedia_sin_texto_se_descarta():
    ext, ts, body, why = _msg_fields(MEDIA_SIN_TEXTO[0])
    assert ext is None and why == "sin_texto"


def test_sin_id_y_sin_timestamp():
    assert _msg_fields(SIN_ID[0])[3] == "sin_id"
    assert _msg_fields(SIN_TS[0])[3] == "sin_timestamp"


def test_no_objeto_y_envoltorio_invalido():
    assert _msg_fields(None)[3] == "no_objeto"
    assert _msg_fields(42)[3] == "no_objeto"
    assert _msg_fields({"message": "texto"})[3] == "envoltorio_invalido"


def test_messages_formas():
    assert _messages(PREVIEW_ANIDADO) == (PREVIEW_ANIDADO, None)
    assert _messages(VACIA) == ([], None)
    assert _messages({"messages": PLANO}) == (PLANO, None)
    assert _messages({"data": PLANO}) == (PLANO, None)
    assert _messages(ESTRUCTURA_RARA) == (None, "estructura_desconocida")
    assert _messages({})[0] is None
    assert _messages(None)[0] is None


# --------------------------------------------------------------- integración

class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class _ErrResp:
    def raise_for_status(self):
        raise httpx.HTTPStatusError("422", request=None, response=None)


def _fake_client(channel_payload, seen, session_status="WORKING"):
    """Cliente httpx falso: /api/sessions/* -> estado; /channels/* -> payload."""
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            seen.append(url)
            if "/api/sessions/" in url:
                return _Resp({"name": "default", "status": session_status})
            if isinstance(channel_payload, Exception):
                raise channel_payload
            return _Resp(channel_payload)
    return FakeClient


def _setup(monkeypatch, payload, seen, session_status="WORKING",
           channels="0029VaABC:cercanias-madrid:10", poll=0):
    monkeypatch.setattr(cfg, "WAHA_URL", "http://waha.test")
    monkeypatch.setattr(cfg, "WAHA_API_KEY", "secreto")
    monkeypatch.setattr(cfg, "WAHA_SESSION", "default")
    monkeypatch.setattr(cfg, "WAHA_CHANNELS", channels)
    monkeypatch.setattr(cfg, "POLL_WAHA", poll)
    monkeypatch.setattr(notices, "_last_poll", 0.0)
    monkeypatch.setattr(notices.httpx, "Client",
                        _fake_client(payload, seen, session_status))


def _stats(conn, slug):
    raw = conn.execute(text("SELECT value FROM meta WHERE key=:k"),
                       {"k": f"whatsapp_stats_{slug}"}).scalar()
    return json.loads(raw) if raw else None


def _meta(conn, key):
    return conn.execute(text("SELECT value FROM meta WHERE key=:k"),
                        {"k": key}).scalar()


@pytest.fixture()
def waha(scenario):
    with scenario.begin() as c:
        c.execute(text("DELETE FROM official_notice"))
        c.execute(text("DELETE FROM meta WHERE key LIKE 'whatsapp%'"))
    return scenario


@pytest.mark.integration
def test_ciclo_envoltorio_anidado_inserta_e_idempotente(waha, monkeypatch):
    seen = []
    _setup(monkeypatch, PREVIEW_ANIDADO, seen)
    assert run_whatsapp_cycle() == 2
    assert run_whatsapp_cycle() == 0      # dedupe (source, channel, external_id)
    with waha.begin() as c:
        n = c.execute(text("SELECT count(*) FROM official_notice")).scalar()
        st = _stats(c, "cercanias-madrid")
        last = _meta(c, "whatsapp_last_msg_cercanias-madrid")
        sess = _meta(c, "whatsapp_session_status")
    assert n == 2
    # contadores acumulados: el segundo ciclo volvió a recibir los mismos
    # mensajes y los contó como duplicados
    assert st["received"] == 4 and st["inserted"] == 2 and st["duplicated"] == 2
    assert int(last) == 1791508800 and sess == "WORKING"
    assert seen[0] == "http://waha.test/api/sessions/default"


@pytest.mark.integration
def test_ciclo_plano_y_ms_y_opcionales(waha, monkeypatch):
    seen = []
    _setup(monkeypatch, PLANO + MS + PREVIEW_OPCIONALES_AUSENTES, seen)
    assert run_whatsapp_cycle() == 3
    with waha.begin() as c:
        rows = c.execute(text(
            "SELECT external_id, posted_at, verified FROM official_notice"
            " ORDER BY posted_at")).mappings().all()
    assert {r["external_id"] for r in rows} == {
        "true_999@newsletter_AAAA", "false_1@newsletter_MS", "false_1@newsletter_C"}
    assert all(r["verified"] for r in rows)      # canal allowlist = verificado
    assert all(r["posted_at"] == 1791505200 for r in rows)


@pytest.mark.integration
def test_texto_largo_acentos_emoji_se_conserva(waha, monkeypatch):
    seen = []
    item = {"message": {"id": "false_1@newsletter_L", "timestamp": 1791505200,
                        "body": LARGO}}
    _setup(monkeypatch, [item], seen)
    assert run_whatsapp_cycle() == 1
    with waha.begin() as c:
        txt = c.execute(text("SELECT text FROM official_notice")).scalar()
    assert txt == LARGO


@pytest.mark.integration
def test_multimedia_y_basura_se_contabilizan_sin_insertar(waha, monkeypatch):
    seen = []
    _setup(monkeypatch, MEDIA_SIN_TEXTO + LISTA_CON_BASURA + SIN_ID + SIN_TS, seen)
    assert run_whatsapp_cycle() == 0
    with waha.begin() as c:
        st = _stats(c, "cercanias-madrid")
        n = c.execute(text("SELECT count(*) FROM official_notice")).scalar()
        deg = _meta(c, "whatsapp_degraded_cercanias-madrid")
    assert n == 0
    assert st["received"] == 7
    d = st["discarded"]
    assert d["sin_texto"] == 1 and d["sin_timestamp"] == 1
    assert d["sin_id"] == 2          # preview sin id + dict plano sin id
    assert d["no_objeto"] == 3       # None, 42, 'texto suelto'
    # recibió mensajes pero ninguno aprovechable: degradación registrada
    assert deg == "sin_mensajes_interpretables"


@pytest.mark.integration
def test_respuesta_vacia_no_es_degradacion(waha, monkeypatch):
    seen = []
    _setup(monkeypatch, VACIA, seen)
    assert run_whatsapp_cycle() == 0
    with waha.begin() as c:
        assert _meta(c, "whatsapp_fetch_ok_cercanias-madrid") is not None
        assert _meta(c, "whatsapp_degraded_cercanias-madrid") is None
        assert _stats(c, "cercanias-madrid")["received"] == 0


@pytest.mark.integration
def test_estructura_desconocida_es_degradacion_explicita(waha, monkeypatch):
    seen = []
    _setup(monkeypatch, ESTRUCTURA_RARA, seen)
    assert run_whatsapp_cycle() == 0
    with waha.begin() as c:
        assert _meta(c, "whatsapp_degraded_cercanias-madrid") == "estructura_desconocida"
        assert _meta(c, "whatsapp_fetch_ok_cercanias-madrid") is not None


@pytest.mark.integration
def test_error_http_marca_fuente_no_sistema(waha, monkeypatch):
    seen = []
    _setup(monkeypatch, httpx.ConnectError("caído"), seen)
    assert run_whatsapp_cycle() == 0
    with waha.begin() as c:
        assert _meta(c, "whatsapp_fetch_err_cercanias-madrid") is not None
        assert "caído" in _meta(c, "whatsapp_fetch_errmsg_cercanias-madrid")
        assert _stats(c, "cercanias-madrid")["fetch_errors"] == 1


@pytest.mark.integration
def test_sesion_desconectada_degrada(waha, monkeypatch):
    seen = []
    _setup(monkeypatch, PREVIEW_ANIDADO, seen, session_status="STOPPED")
    assert run_whatsapp_cycle() == 2      # se intenta igualmente
    with waha.begin() as c:
        assert _meta(c, "whatsapp_session_status") == "STOPPED"
        assert _meta(c, "whatsapp_session_degraded") is not None


@pytest.mark.integration
def test_throttle_respeta_poll_waha(waha, monkeypatch):
    seen = []
    _setup(monkeypatch, PREVIEW_ANIDADO, seen, poll=3600)
    assert run_whatsapp_cycle() == 2
    n_calls = len(seen)
    assert run_whatsapp_cycle() == 0
    assert len(seen) == n_calls           # no volvió a llamar a WAHA


# ------------------------------------------------- hilos: orden y ambigüedad

def _ins(conn, posted, body, channel="cercanias-madrid", nucleo="10",
         source="whatsapp", ext=None):
    return conn.execute(text("""
        INSERT INTO official_notice (source, channel, external_id, posted_at,
            received_at, text, nucleo_code, status, is_update)
        VALUES (:s, :ch, :ext, :p, :p, :t, :n, 'pendiente', 0) RETURNING id"""),
        {"s": source, "ch": channel, "ext": ext, "p": posted,
         "t": body, "n": nucleo}).scalar()


def _seed_thread(conn, tid, posted, status, lines, stations, kind="averia_infraestructura"):
    conn.execute(text("""
        INSERT INTO official_notice (source, channel, external_id, posted_at,
            received_at, text, nucleo_code, lines, stations, kind, status,
            is_update, thread_id, parse)
        VALUES ('whatsapp', 'cercanias-madrid', :e, :p, :p, 'x', '10',
            CAST(:l AS jsonb), CAST(:s AS jsonb), :k, :st, 0, :t, '{}')"""),
        {"e": f"seed-{tid}-{posted}", "p": posted, "l": json.dumps(lines),
         "s": json.dumps(stations), "k": kind, "st": status, "t": tid})


@pytest.mark.integration
def test_aviso_tardio_se_une_a_hilo_cerrado(waha):
    """Un 'activa' capturado después de la normalización se une al hilo,
    no abre una incidencia falsa."""
    T0, T1, T2 = 1791400000, 1791410000, 1791420000
    with waha.begin() as c:
        _seed_thread(c, 500, T1, "activa", ["C5"], [{"name": "Zarzaquemada"}])
        _seed_thread(c, 500, T2, "normalizada", ["C5"], [{"name": "Zarzaquemada"}])
        late = _ins(c, T0, M2)      # publicado antes pero capturado ahora
        process_pending(c, T2 + 100)
        row = c.execute(text(
            "SELECT thread_id, status FROM official_notice WHERE id=:i"),
            {"i": late}).mappings().one()
    assert row["thread_id"] == 500          # unido al hilo cerrado
    assert row["status"] == "activa"        # su estado propio, honesto


@pytest.mark.integration
def test_aviso_posterior_al_cierre_es_hilo_nuevo(waha):
    T1, T2 = 1791410000, 1791420000
    with waha.begin() as c:
        _seed_thread(c, 500, T1, "activa", ["C5"], [{"name": "Zarzaquemada"}])
        _seed_thread(c, 500, T2, "normalizada", ["C5"], [{"name": "Zarzaquemada"}])
        new = _ins(c, T2 + 7200, M2)        # mismo problema, posterior al cierre
        process_pending(c, T2 + 7300)
        row = c.execute(text(
            "SELECT thread_id FROM official_notice WHERE id=:i"),
            {"i": new}).mappings().one()
    assert row["thread_id"] == new          # incidencia nueva: hilo propio


@pytest.mark.integration
def test_ambiguo_no_se_adivina(waha):
    T = 1791500000
    with waha.begin() as c:
        _seed_thread(c, 600, T - 3600, "activa", ["C5"], [{"name": "Zarzaquemada"}])
        _seed_thread(c, 601, T - 1800, "activa", ["C5"], [{"name": "Zarzaquemada"}])
        msg = _ins(c, T, M2)
        stats = process_pending(c, T + 10)
        row = c.execute(text(
            "SELECT thread_id, parse->>'ambiguous' amb FROM official_notice"
            " WHERE id=:i"), {"i": msg}).mappings().one()
    assert stats["ambiguous"] == 1
    assert row["thread_id"] is None and row["amb"] == "true"


@pytest.mark.integration
def test_pendientes_sobreviven_al_reinicio(waha):
    """Insertado como 'pendiente' (p. ej. caída antes de clasificar): el
    siguiente ciclo lo procesa sin depender de WAHA."""
    T = 1791500000
    with waha.begin() as c:
        _ins(c, T, DESCONOCIDO, source="manual")
        stats = process_pending(c, T + 10)
        row = c.execute(text(
            "SELECT status, kind FROM official_notice")).mappings().one()
    assert stats["unclassified"] == 1
    assert row["status"] == "activa" and row["kind"] == "otra"
