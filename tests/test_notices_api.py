"""Avisos oficiales (WhatsApp / manual): alta admin, hilos y cruce con incidencias."""
import json
import time

import pytest
from sqlalchemy import text

TOKEN = "token-de-prueba-123"
ADMIN = "/api/v1/admin/avisos"
THREADS = "/api/v1/avisos-oficiales"
BODY = {"text": "Incidencia en Getafe Centro, trenes C4a con retrasos",
        "posted_at": 1_800_000_000}

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
        c.execute(text("DELETE FROM official_notice"))


def _row(engine, now, ext, posted, status, thread_id=None, lines=(), stations=(),
         kind=None, is_update=0, source="whatsapp", nucleo="10", body="texto",
         verified=None):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO official_notice (source, channel, external_id,
            posted_at, received_at, text, nucleo_code, lines, stations, kind, status,
            is_update, thread_id, verified) VALUES (:src, 'cercanias-madrid', :ext,
            :p, :p, :t, :n, CAST(:l AS jsonb), CAST(:s AS jsonb), :k, :st, :u,
            :th, :v)"""),
            {"src": source, "ext": ext, "p": posted, "t": body, "n": nucleo,
             "l": json.dumps(list(lines)), "s": json.dumps(list(stations)),
             "k": kind, "st": status, "u": is_update, "th": thread_id,
             "v": verified})


@pytest.fixture()
def notices(client, scenario, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    _clean(scenario)
    return scenario, int(time.time())


def _seed_thread(eng, now):
    """Hilo parseado (como lo deja el collector): alta + actualización."""
    _row(eng, now, "w-pend", now - 60, "pendiente", body="pendiente sin interpretar")
    _row(eng, now, "w1", now - 7200, "activa", thread_id=9001, kind="averia_infraestructura",
         lines=["C5"], stations=[{"name": "Getafe Centro", "stop_id": "10000"}],
         body="Avería en Getafe Centro")
    _row(eng, now, "w2", now - 600, "en_recuperacion", thread_id=9001, is_update=1,
         lines=["C5", "C4a"],
         stations=[{"name": "Getafe Centro", "stop_id": "10000"}, {"name": "Parla"}],
         body="Se recuperan frecuencias")


@pytest.mark.integration
def test_admin_503_when_token_unset(client, scenario, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.post(ADMIN, json=BODY).status_code == 503
    monkeypatch.setenv("ADMIN_TOKEN", "   ")          # vacío = desactivado
    r = client.post(ADMIN, json=BODY, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 503 and r.json()["detail"] == "admin desactivado"


@pytest.mark.integration
def test_admin_auth_rejects_missing_and_wrong_token(client, notices):
    assert client.post(ADMIN, json=BODY).status_code == 401
    assert client.post(ADMIN, json=BODY,
                       headers={"Authorization": "Bearer otro"}).status_code == 401
    assert client.post(ADMIN, json=BODY,
                       headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401


@pytest.mark.integration
def test_admin_insert_is_idempotent(client, notices):
    eng, _ = notices
    h = {"Authorization": f"Bearer {TOKEN}"}
    r1 = client.post(ADMIN, json=BODY, headers=h)
    assert r1.status_code == 200
    out = r1.json()
    assert out["status"] == "pendiente" and out["note"].startswith("el collector")
    r2 = client.post(ADMIN, json={**BODY, "posted_at": "2027-01-15T10:00:00+01:00"},
                     headers=h)
    r3 = client.post(ADMIN, json=BODY, headers=h)
    assert r3.json()["id"] == out["id"]                       # mismo texto+hora
    assert r2.json()["id"] != out["id"]                       # otra hora = otro aviso
    with eng.connect() as c:
        row = c.execute(text("""SELECT source, nucleo_code, status, external_id, text
            FROM official_notice WHERE id=:i"""), {"i": out["id"]}).mappings().one()
        n = c.execute(text("SELECT count(*) FROM official_notice")).scalar()
    assert row["source"] == "manual" and row["nucleo_code"] == "10"
    assert row["status"] == "pendiente" and len(row["external_id"]) == 32
    assert n == 2
    bad = client.post(ADMIN, json={**BODY, "nucleo": "narnia"}, headers=h)
    assert bad.status_code == 400
    assert client.post(ADMIN, json={"text": ""}, headers=h).status_code == 422


@pytest.mark.integration
def test_threads_group_status_from_latest_and_hide_pending(client, notices):
    eng, now = notices
    _seed_thread(eng, now)
    r = client.get(THREADS, params={"nucleo": "madrid"}).json()
    assert len(r["items"]) == 1
    t = r["items"][0]
    assert t["thread_id"] == 9001 and t["channel"] == "cercanias-madrid"
    assert t["source"] == "whatsapp" and t["kind"] == "averia_infraestructura"
    assert t["status"] == "en_recuperacion"                   # último mensaje manda
    assert t["opened_at"] == now - 7200 and t["updated_at"] == now - 600
    assert t["lines"] == ["C4a", "C5"]
    keys = {s["key"] for s in t["stations"]}
    assert keys == {"cer:10000", None}
    assert [m["text"] for m in t["messages"]] == ["Avería en Getafe Centro",
                                                  "Se recuperan frecuencias"]
    assert [m["status"] for m in t["messages"]] == ["activa", "en_recuperacion"]
    assert t["messages"][1]["is_update"] is True
    assert t["nucleo"]["slug"] == "madrid"
    assert t["attribution"] == "Canal oficial de WhatsApp de Renfe Cercanías Madrid"
    assert all("sin interpretar" not in m["text"] for m in t["messages"])
    assert client.get(THREADS, params={"nucleo": "madrid", "linea": "c5"}).json()["items"]
    assert client.get(THREADS, params={"nucleo": "madrid", "linea": "c1"}).json()["items"] == []
    assert client.get(THREADS, params={"nucleo": "narnia"}).status_code == 404


@pytest.mark.integration
def test_threads_open_vs_all_and_manual_attribution(client, notices):
    eng, now = notices
    _row(eng, now, "old", now - 30 * 3600, "activa", thread_id=9100, lines=["C1"])
    _row(eng, now, "closed", now - 300, "normalizada", thread_id=9200, lines=["C2"])
    _row(eng, now, "man", now - 900, "activa", thread_id=9300, source="manual",
         lines=["C3"], body="Pegado a mano")
    _row(eng, now, "manv", now - 800, "activa", thread_id=9400, source="manual",
         lines=["C7"], body="Pegado y verificado", verified=True)
    abiertos = {t["thread_id"] for t in client.get(THREADS).json()["items"]}
    assert abiertos == {9300, 9400}            # normalizada fuera; >24 h sin novedades fuera
    todos = {t["thread_id"]: t for t in
             client.get(THREADS, params={"estado": "todos"}).json()["items"]}
    assert set(todos) == {9100, 9200, 9300, 9400}
    # sin verificación administrativa no se afirma la atribución oficial
    assert todos[9300]["attribution"].endswith(
        "pegado manualmente (pendiente de verificación)")
    assert todos[9300]["source"] == "manual" and todos[9300]["verified"] is False
    assert todos[9400]["attribution"].endswith("· pegado manualmente")
    assert todos[9400]["verified"] is True
    assert not todos[9100]["attribution"].endswith("manualmente")


def test_admin_rows_stay_pending_until_collector_parses(client, notices):
    eng, _ = notices
    h = {"Authorization": f"Bearer {TOKEN}"}
    manual = client.post(ADMIN, json=BODY, headers=h).json()
    assert manual["status"] == "pendiente"
    ids = {t["thread_id"] for t in
           client.get(THREADS, params={"estado": "todos"}).json()["items"]}
    assert manual["id"] not in ids


@pytest.mark.integration
def test_incidencias_includes_official_notices(client, notices):
    eng, now = notices
    _seed_thread(eng, now)
    madrid = client.get("/api/v1/incidencias", params={"nucleo": "madrid"}).json()
    assert [t["thread_id"] for t in madrid["official_notices"]] == [9001]
    todo = client.get("/api/v1/incidencias").json()
    assert [t["thread_id"] for t in todo["official_notices"]] == [9001]
    st = client.get("/api/v1/incidencias", params={"estacion": "cer:10000"}).json()
    assert [t["thread_id"] for t in st["official_notices"]] == [9001]
    other = client.get("/api/v1/incidencias", params={"estacion": "cer:17000"}).json()
    assert other["official_notices"] == []
    c4a = client.get("/api/v1/incidencias", params={"nucleo": "madrid", "linea": "c4a"}).json()
    assert [t["thread_id"] for t in c4a["official_notices"]] == [9001]
    c1 = client.get("/api/v1/incidencias", params={"nucleo": "madrid", "linea": "c1"}).json()
    assert c1["official_notices"] == []
    asturias = client.get("/api/v1/incidencias", params={"nucleo": "asturias"}).json()
    assert asturias["official_notices"] == []


@pytest.mark.integration
def test_admin_verificar_marca_procedencia(client, notices):
    eng, _ = notices
    h = {"Authorization": f"Bearer {TOKEN}"}
    r = client.post(ADMIN, json={**BODY, "source_url": "https://example.test/avisos"},
                    headers=h).json()
    vid = r["id"]
    assert client.post(f"{ADMIN}/{vid}/verificar", json={}).status_code == 401
    r2 = client.post(f"{ADMIN}/{vid}/verificar", json={}, headers=h)
    assert r2.status_code == 200 and r2.json()["verified"] is True
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT verified, source_url FROM official_notice WHERE id=:i"),
            {"i": vid}).mappings().one()
    assert row["verified"] is True
    assert row["source_url"] == "https://example.test/avisos"
    assert client.post(f"{ADMIN}/999999/verificar", json={}, headers=h).status_code == 404


@pytest.mark.integration
def test_admin_reasignar_hilo_corrige_ambiguo(client, notices):
    eng, now = notices
    h = {"Authorization": f"Bearer {TOKEN}"}
    _row(eng, now, "t-a", now - 600, "activa", thread_id=8001, lines=["C5"])
    # aviso ambiguo: thread_id NULL
    _row(eng, now, "amb", now - 300, "activa", thread_id=None, lines=["C5"])
    amb = eng.connect().execute(text(
        "SELECT id FROM official_notice WHERE external_id='amb'")).scalar()
    assert client.post(f"{ADMIN}/{amb}/hilo", json={"thread_id": 8001}).status_code == 401
    r = client.post(f"{ADMIN}/{amb}/hilo", json={"thread_id": 8001}, headers=h)
    assert r.status_code == 200 and r.json()["thread_id"] == 8001
    assert client.post(f"{ADMIN}/{amb}/hilo", json={"thread_id": 7777},
                       headers=h).status_code == 404
    items = {m["id"]: t for t in
             client.get(THREADS, params={"estado": "todos"}).json()["items"]
             for m in t["messages"]}
    assert items[amb]["thread_id"] == 8001


@pytest.mark.integration
def test_salud_whatsapp_en_incidencias(client, notices):
    """La respuesta de /incidencias informa de la fuente WhatsApp sin
    confundirla con la salud del feed GTFS-RT."""
    eng, now = notices
    with eng.begin() as c:
        c.execute(text("DELETE FROM meta WHERE key LIKE 'whatsapp%'"))
    r = client.get("/api/v1/incidencias").json()
    assert r["sources"]["whatsapp"]["status"] == "disabled"
    with eng.begin() as c:
        c.execute(text("INSERT INTO meta(key, value) VALUES "
                       "('whatsapp_session_status','WORKING'),"
                       "('whatsapp_fetch_ok_cercanias-madrid',:ok)"),
                  {"ok": str(now)})
    r = client.get("/api/v1/incidencias").json()
    wa = r["sources"]["whatsapp"]
    assert wa["status"] == "ok" and wa["session"] == "WORKING"
    assert wa["channels"]["cercanias-madrid"]["status"] == "ok"
    with eng.begin() as c:
        c.execute(text("INSERT INTO meta(key, value) VALUES "
                       "('whatsapp_session_status','STOPPED') "
                       "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value"))
    r = client.get("/api/v1/incidencias").json()
    assert r["sources"]["whatsapp"]["status"] == "degraded"
