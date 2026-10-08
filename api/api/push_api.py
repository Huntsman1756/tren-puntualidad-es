"""Web Push v2: un dispositivo (navegador) con varias reglas independientes.

Modelo de seguridad:
- Al registrar el dispositivo se entrega UNA vez un token aleatorio de 256
  bits (`<device_id>.<secreto>`). Solo se guarda su SHA-256. Todas las
  operaciones sobre reglas exigen `Authorization: Bearer <token>`.
- Los identificadores de regla son UUID4 aleatorios, pero nunca bastan por
  sí solos: una regla solo es visible/editable con el token de su
  dispositivo (404 en otro caso, sin revelar si existe).
- Reclamación de dispositivos migrados de v0.3.x (token_hash NULL): se
  permite UNA vez presentando el endpoint de push, que era la credencial
  que ya usaba esa versión. Después solo vale el token.
- Re-registro de un endpoint ya reclamado sin token válido: se rota el
  token y se BORRAN sus reglas (quien solo conoce el endpoint no puede
  leer ni heredar reglas ajenas).
"""
import hashlib
import hmac
import json
import os
import secrets
import uuid

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.db import engine

router = APIRouter(prefix="/api/v1/push")

MAX_RULES = 20


class Keys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class DeviceIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2000)
    keys: Keys


class RuleIn(BaseModel):
    config: dict = Field(default_factory=dict)
    enabled: bool = True


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def clean_config(cfg: dict) -> dict:
    """Valida y normaliza la configuración de una regla."""
    days = cfg.get("days") or []

    def hm(v):
        v = str(v or "")[:5]
        if not v:
            return ""
        try:
            h, m = v.split(":")
            if 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
                return f"{int(h):02d}:{int(m):02d}"
        except ValueError:
            pass
        raise HTTPException(400, "hora inválida (HH:MM)")

    def key(v):
        v = str(v or "")[:200]
        for part in filter(None, v.split(",")):
            f, _, s = part.partition(":")
            if f not in ("cer", "ld") or not s or len(s) > 32:
                raise HTTPException(400, f"clave de estación inválida: {part}")
        return v

    def intr(v, default, lo, hi):
        try:
            return min(max(int(v if v not in (None, "") else default), lo), hi)
        except (TypeError, ValueError):
            raise HTTPException(400, "valor numérico inválido") from None

    clean = {
        "type": cfg.get("type") if cfg.get("type") in ("journey", "station") else None,
        "from_key": key(cfg.get("from_key")),
        "to_key": key(cfg.get("to_key")),
        "station_key": key(cfg.get("station_key")),
        "label": str(cfg.get("label") or "")[:120],
        "days": sorted({int(d) for d in days if isinstance(d, int) and 0 <= d <= 6}),
        "from_time": hm(cfg.get("from_time")),
        "to_time": hm(cfg.get("to_time")),
        "threshold_min": intr(cfg.get("threshold_min"), 5, 1, 120),
        "min_interval_min": intr(cfg.get("min_interval_min"), 30, 10, 1440),
    }
    ok = (clean["type"] == "journey" and clean["from_key"] and clean["to_key"]) \
        or (clean["type"] == "station" and clean["station_key"])
    if not ok:
        raise HTTPException(400, "config incompleta")
    return clean


def _device_from_token(c, authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "falta credencial del dispositivo")
    tok = authorization.split(" ", 1)[1].strip()
    did, _, secret = tok.partition(".")
    if not did or not secret:
        raise HTTPException(401, "credencial inválida")
    row = c.execute(text(
        "SELECT id, token_hash FROM push_devices WHERE id=:i"),
        {"i": did}).mappings().first()
    if not row or not row["token_hash"] or not hmac.compare_digest(
            row["token_hash"], _hash(secret)):
        raise HTTPException(401, "credencial inválida")
    return dict(row)


def _rule_out(r) -> dict:
    return {"id": r["id"], "config": r["config"], "enabled": bool(r["enabled"]),
            "last_notify_at": r["last_notify_at"].isoformat()
            if r["last_notify_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None}


@router.get("/public-key")
def push_public_key():
    pk = os.environ.get("VAPID_PUBLIC_KEY", "")
    if not pk:
        raise HTTPException(503, "push no configurado")
    return {"key": pk}


@router.post("/devices", status_code=201)
def register_device(body: DeviceIn, authorization: str | None = Header(None)):
    """Registra (o re-vincula) el navegador. Devuelve el token SOLO aquí."""
    with engine.begin() as c:
        row = c.execute(text(
            "SELECT id, token_hash FROM push_devices WHERE endpoint=:e"),
            {"e": body.endpoint}).mappings().first()
        if row and authorization:
            try:
                dev = _device_from_token(c, authorization)
                if dev["id"] == row["id"]:
                    c.execute(text("""UPDATE push_devices SET p256dh=:p, auth=:a,
                        last_seen_at=now() WHERE id=:i"""),
                        {"p": body.keys.p256dh, "a": body.keys.auth, "i": row["id"]})
                    n = c.execute(text("SELECT count(*) FROM push_rules WHERE device_id=:i"),
                                  {"i": row["id"]}).scalar()
                    return {"device_id": row["id"], "token": None,
                            "rules": n, "status": "existing"}
            except HTTPException:
                pass
        secret = secrets.token_urlsafe(32)
        if row:
            claimed = row["token_hash"] is None
            if not claimed:
                # endpoint conocido sin token válido: rotación + reglas fuera
                c.execute(text("DELETE FROM push_rules WHERE device_id=:i"),
                          {"i": row["id"]})
            c.execute(text("""UPDATE push_devices SET token_hash=:h, p256dh=:p,
                auth=:a, last_seen_at=now() WHERE id=:i"""),
                {"h": _hash(secret), "p": body.keys.p256dh,
                 "a": body.keys.auth, "i": row["id"]})
            did = row["id"]
            status = "claimed_legacy" if claimed else "rotated"
        else:
            did = str(uuid.uuid4())
            c.execute(text("""INSERT INTO push_devices (id, endpoint, p256dh,
                auth, token_hash, last_seen_at)
                VALUES (:i, :e, :p, :a, :h, now())"""),
                {"i": did, "e": body.endpoint, "p": body.keys.p256dh,
                 "a": body.keys.auth, "h": _hash(secret)})
            status = "created"
        n = c.execute(text("SELECT count(*) FROM push_rules WHERE device_id=:i"),
                      {"i": did}).scalar()
    return {"device_id": did, "token": f"{did}.{secret}", "rules": n,
            "status": status}


@router.delete("/devices/me")
def delete_device(authorization: str | None = Header(None)):
    """Baja total del navegador: borra el dispositivo y todas sus reglas."""
    with engine.begin() as c:
        dev = _device_from_token(c, authorization)
        c.execute(text("DELETE FROM push_rules WHERE device_id=:i"), {"i": dev["id"]})
        c.execute(text("DELETE FROM push_devices WHERE id=:i"), {"i": dev["id"]})
    return {"ok": True}


@router.get("/rules")
def list_rules(authorization: str | None = Header(None)):
    with engine.connect() as c:
        dev = _device_from_token(c, authorization)
        rows = c.execute(text("""SELECT * FROM push_rules WHERE device_id=:i
            ORDER BY created_at"""), {"i": dev["id"]}).mappings().all()
    return {"device_id": dev["id"], "rules": [_rule_out(r) for r in rows]}


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, authorization: str | None = Header(None)):
    cfg = clean_config(body.config)
    rid = str(uuid.uuid4())
    with engine.begin() as c:
        dev = _device_from_token(c, authorization)
        n = c.execute(text("SELECT count(*) FROM push_rules WHERE device_id=:i"),
                      {"i": dev["id"]}).scalar()
        if n >= MAX_RULES:
            raise HTTPException(409, f"máximo {MAX_RULES} reglas por dispositivo")
        c.execute(text("""INSERT INTO push_rules (id, device_id, config, enabled,
            created_at, updated_at)
            VALUES (:r, :d, CAST(:c AS jsonb), :e, now(), now())"""),
            {"r": rid, "d": dev["id"], "c": json.dumps(cfg),
             "e": 1 if body.enabled else 0})
        row = c.execute(text("SELECT * FROM push_rules WHERE id=:r"),
                        {"r": rid}).mappings().first()
    return _rule_out(row)


def _own_rule(c, dev_id: str, rule_id: str):
    row = c.execute(text(
        "SELECT * FROM push_rules WHERE id=:r AND device_id=:d"),
        {"r": rule_id, "d": dev_id}).mappings().first()
    if not row:
        raise HTTPException(404, "regla no encontrada")
    return row


@router.get("/rules/{rule_id}")
def get_rule(rule_id: str, authorization: str | None = Header(None)):
    with engine.connect() as c:
        dev = _device_from_token(c, authorization)
        return _rule_out(_own_rule(c, dev["id"], rule_id))


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleIn,
                authorization: str | None = Header(None)):
    cfg = clean_config(body.config)
    with engine.begin() as c:
        dev = _device_from_token(c, authorization)
        _own_rule(c, dev["id"], rule_id)
        # cambiar la regla reinicia su dedup: nueva configuración, nuevo aviso
        c.execute(text("""UPDATE push_rules SET config=CAST(:c AS jsonb),
            enabled=:e, updated_at=now(), last_notify_key=NULL
            WHERE id=:r"""),
            {"c": json.dumps(cfg), "e": 1 if body.enabled else 0, "r": rule_id})
        return _rule_out(_own_rule(c, dev["id"], rule_id))


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, authorization: str | None = Header(None)):
    """Borra UNA regla. El dispositivo y las demás reglas siguen activos;
    `remaining` permite al cliente decidir si desuscribir el navegador."""
    with engine.begin() as c:
        dev = _device_from_token(c, authorization)
        _own_rule(c, dev["id"], rule_id)
        c.execute(text("DELETE FROM push_rules WHERE id=:r"), {"r": rule_id})
        n = c.execute(text("SELECT count(*) FROM push_rules WHERE device_id=:d"),
                      {"d": dev["id"]}).scalar()
    return {"ok": True, "remaining": n}


# ---------- compatibilidad v0.3.x (clientes antiguos en caché) ----------

class LegacySubIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2000)
    keys: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)


@router.post("/subscribe", deprecated=True)
def legacy_subscribe(body: LegacySubIn, response: Response):
    """v0.3.x: añadía/sobrescribía la única config del endpoint. Ahora
    AÑADE una regla al dispositivo (nunca sobrescribe otras)."""
    keys = body.keys or {}
    if not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(400, "faltan claves p256dh/auth")
    cfg = clean_config(body.config)
    with engine.begin() as c:
        row = c.execute(text(
            "SELECT id, token_hash FROM push_devices WHERE endpoint=:e"),
            {"e": body.endpoint}).first()
        if row and row[1] is not None:
            # dispositivo ya gestionado con token: el cliente antiguo no
            # puede añadir reglas sin él
            raise HTTPException(409, "dispositivo gestionado: actualiza la página")
        if row:
            did = row[0]
        else:
            did = str(uuid.uuid4())
            c.execute(text("""INSERT INTO push_devices (id, endpoint, p256dh,
                auth, token_hash) VALUES (:i, :e, :p, :a, NULL)"""),
                {"i": did, "e": body.endpoint, "p": keys["p256dh"],
                 "a": keys["auth"]})
        c.execute(text("""INSERT INTO push_rules (id, device_id, config,
            enabled, created_at, updated_at)
            VALUES (:r, :d, CAST(:c AS jsonb), 1, now(), now())"""),
            {"r": str(uuid.uuid4()), "d": did, "c": json.dumps(cfg)})
    response.headers["Deprecation"] = "true"
    return {"ok": True}


@router.post("/unsubscribe", deprecated=True)
def legacy_unsubscribe(body: dict, response: Response):
    """v0.3.x: baja total por endpoint (solo dispositivos no reclamados:
    uno con token exige DELETE /devices/me)."""
    endpoint = str(body.get("endpoint") or "")
    if len(endpoint) < 20:
        raise HTTPException(400, "endpoint inválido")
    with engine.begin() as c:
        row = c.execute(text("""SELECT id FROM push_devices
            WHERE endpoint=:e AND token_hash IS NULL"""), {"e": endpoint}).first()
        if row:
            c.execute(text("DELETE FROM push_rules WHERE device_id=:i"), {"i": row[0]})
            c.execute(text("DELETE FROM push_devices WHERE id=:i"), {"i": row[0]})
    response.headers["Deprecation"] = "true"
    return {"ok": True}
