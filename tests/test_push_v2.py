"""Web Push v2 end-to-end: un navegador, varias reglas independientes."""
from unittest.mock import patch

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

EP_A = "https://push.example/send/browser-A-" + "x" * 40
EP_B = "https://push.example/send/browser-B-" + "y" * 40
KEYS = {"p256dh": "BPk" + "a" * 60, "auth": "auth" + "b" * 18}

R_J1 = {"type": "journey", "from_key": "cer:17000", "to_key": "cer:18000",
        "threshold_min": 5, "label": "Casa → trabajo"}
R_J2 = {"type": "journey", "from_key": "cer:10000", "to_key": "cer:17000",
        "threshold_min": 10}
R_ST = {"type": "station", "station_key": "cer:15211", "threshold_min": 3}


def register(client, ep=EP_A, token=None):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.post("/api/v1/push/devices", json={"endpoint": ep, "keys": KEYS},
                    headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def add(client, tok, cfg):
    r = client.post("/api/v1/push/rules", json={"config": cfg}, headers=auth(tok))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_three_rules_same_browser_crud_and_independent_delete(client):
    dev = register(client)
    tok = dev["token"]
    assert dev["status"] == "created" and tok.startswith(dev["device_id"] + ".")
    ids = [add(client, tok, c) for c in (R_J1, R_J2, R_ST)]
    rules = client.get("/api/v1/push/rules", headers=auth(tok)).json()["rules"]
    assert [r["id"] for r in rules] == ids
    assert rules[0]["config"]["label"] == "Casa → trabajo"
    # editar una regla no toca las demás
    upd = client.put(f"/api/v1/push/rules/{ids[1]}", headers=auth(tok),
                     json={"config": {**R_J2, "threshold_min": 15}})
    assert upd.json()["config"]["threshold_min"] == 15
    assert client.get(f"/api/v1/push/rules/{ids[0]}",
                      headers=auth(tok)).json()["config"]["threshold_min"] == 5
    # borrar una: quedan dos y el dispositivo sigue
    d = client.delete(f"/api/v1/push/rules/{ids[0]}", headers=auth(tok)).json()
    assert d == {"ok": True, "remaining": 2}
    left = client.get("/api/v1/push/rules", headers=auth(tok)).json()["rules"]
    assert [r["id"] for r in left] == ids[1:]
    # re-registrar el mismo navegador con su token no rota ni borra
    again = register(client, token=tok)
    assert again["status"] == "existing" and again["token"] is None
    assert again["rules"] == 2
    # baja total
    assert client.delete("/api/v1/push/devices/me", headers=auth(tok)).json()["ok"]
    assert client.get("/api/v1/push/rules", headers=auth(tok)).status_code == 401


def test_possession_credential_required(client):
    a = register(client, EP_A)
    b = register(client, EP_B)
    rid = add(client, a["token"], R_J1)
    assert client.get("/api/v1/push/rules").status_code == 401
    assert client.get("/api/v1/push/rules",
                      headers=auth(a["device_id"] + ".nope")).status_code == 401
    # conocer el id de regla (o del dispositivo) no basta
    assert client.get(f"/api/v1/push/rules/{rid}",
                      headers=auth(b["token"])).status_code == 404
    assert client.delete(f"/api/v1/push/rules/{rid}",
                         headers=auth(b["token"])).status_code == 404
    assert client.get("/api/v1/push/rules",
                      headers=auth(a["token"])).json()["rules"][0]["id"] == rid
    # quien solo conoce el endpoint no hereda reglas: rotación + borrado
    rot = register(client, EP_A)
    assert rot["status"] == "rotated" and rot["rules"] == 0
    assert client.get("/api/v1/push/rules", headers=auth(a["token"])).status_code == 401


def test_validation(client):
    tok = register(client)["token"]
    bad = client.post("/api/v1/push/rules", headers=auth(tok),
                      json={"config": {"type": "journey", "from_key": "cer:1"}})
    assert bad.status_code == 400
    bad = client.post("/api/v1/push/rules", headers=auth(tok),
                      json={"config": {**R_ST, "from_time": "25:99"}})
    assert bad.status_code == 400
    bad = client.post("/api/v1/push/rules", headers=auth(tok),
                      json={"config": {"type": "station",
                                       "station_key": "evil:1"}})
    assert bad.status_code == 400


def test_selective_sending_per_rule(client, scenario):
    import collector.push as push
    tok = register(client)["token"]
    ids = [add(client, tok, c) for c in (R_J1, R_J2, R_ST)]
    sent = []

    def fake_pick(sub, now):
        cfg = sub["config"]
        # solo el trayecto 1 tiene un tren retrasado
        if cfg.get("from_key") == "cer:17000":
            return {"key": "T1:hoy:2", "title": "C1 · Madrid +10", "body": "",
                    "url": "/trayecto"}
        return None

    with patch.object(push, "VAPID_PRIVATE_KEY", "k"), \
            patch.object(push, "VAPID_PUBLIC_KEY", "k"), \
            patch.object(push, "in_window", lambda cfg, now: True), \
            patch.object(push, "pick_alert", fake_pick), \
            patch.object(push, "send_push",
                         lambda ep, p, a, payload: sent.append(payload) or "ok"):
        assert push.run_push_cycle() == 1
        assert [p["rule_id"] for p in sent] == [ids[0]]
        assert sent[0]["tag"] == f"rule-{ids[0]}"
        # dedup/frecuencia por regla: no se repite inmediatamente
        assert push.run_push_cycle() == 0
    with scenario.connect() as c:
        st = dict(c.execute(text(
            "SELECT id, last_notify_key FROM push_rules")).all())
    assert st[ids[0]] == "T1:hoy:2" and st[ids[1]] is None and st[ids[2]] is None


def test_gone_endpoint_removes_device_and_rules(client, scenario):
    import collector.push as push
    tok = register(client)["token"]
    add(client, tok, R_J1)
    add(client, tok, R_ST)
    with patch.object(push, "VAPID_PRIVATE_KEY", "k"), \
            patch.object(push, "VAPID_PUBLIC_KEY", "k"), \
            patch.object(push, "in_window", lambda cfg, now: True), \
            patch.object(push, "pick_alert",
                         lambda s, n: {"key": "k", "title": "", "body": "", "url": "/"}), \
            patch.object(push, "send_push", lambda *a: "gone"):
        assert push.run_push_cycle() == 0
    with scenario.connect() as c:
        assert c.execute(text("SELECT count(*) FROM push_devices")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM push_rules")).scalar() == 0


def test_legacy_migration_and_claim(client, scenario):
    from collector.db import migrate_push_v2
    with scenario.begin() as c:
        c.execute(text("""INSERT INTO push_subs (endpoint, p256dh, auth, config)
            VALUES (:e, :p, :a, CAST(:c AS jsonb))"""),
            {"e": EP_B, "p": KEYS["p256dh"], "a": KEYS["auth"],
             "c": '{"type":"station","station_key":"cer:17000"}'})
        migrate_push_v2(c)
        migrate_push_v2(c)          # idempotente
        assert c.execute(text("SELECT count(*) FROM push_devices")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM push_rules")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM push_subs")).scalar() == 0
    claim = register(client, EP_B)
    assert claim["status"] == "claimed_legacy" and claim["rules"] == 1
    rules = client.get("/api/v1/push/rules", headers=auth(claim["token"])).json()
    assert rules["rules"][0]["config"]["station_key"] == "cer:17000"
    # el endpoint de cliente antiguo ya no puede añadir reglas a un
    # dispositivo gestionado con token
    old = client.post("/api/v1/push/subscribe", json={
        "endpoint": EP_B, "keys": KEYS, "config": R_ST})
    assert old.status_code == 409
