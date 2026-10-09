"""Avisos oficiales de Renfe (canal de WhatsApp o texto pegado a mano).

- El collector interpreta las filas 'pendiente' y les asigna estado, líneas,
  estaciones y thread_id. Aquí solo se leen y agrupan por hilo.
- Las filas 'pendiente' nunca se muestran.
- POST /admin/avisos: alta manual protegida por ADMIN_TOKEN (Bearer).
"""
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.common import NUCLEO_BY_SLUG, TZ, nucleo_public
from api.db import engine

router = APIRouter(prefix="/api/v1")

OPEN_STATUSES = ("activa", "en_recuperacion", "sin_actualizar")
DEFAULT_CHANNEL = "cercanias-madrid"
_ALPHA_SUFFIX = re.compile(r"(?<=\d)[a-z]$")


def family_slug_of(code: str) -> str:
    """'C4a' -> 'c4', 'R1' -> 'r1', 'C8b' -> 'c8' (sufijo de variante)."""
    return _ALPHA_SUFFIX.sub("", (code or "").strip().lower())


def _thread_key(r) -> int:
    return r["thread_id"] if r["thread_id"] is not None else r["id"]


def _attribution(nuc: dict | None, source: str) -> str:
    name = nuc["name"] if nuc else ""
    base = f"Canal oficial de WhatsApp de Renfe Cercanías {name}".rstrip()
    return base + (" · pegado manualmente" if source == "manual" else "")


def _build(rows) -> list[dict]:
    """Agrupa filas (ya ordenadas por posted_at, id) en hilos."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(_thread_key(r), []).append(r)
    out = []
    for key, msgs in groups.items():
        first, last = msgs[0], msgs[-1]
        code = next((m["nucleo_code"] for m in msgs if m["nucleo_code"]), None)
        nuc = nucleo_public(code)
        stations: dict = {}
        for m in msgs:
            for s in m["stations"] or []:
                sid = str(s.get("stop_id")).strip() if s.get("stop_id") else None
                if not sid and not s.get("name"):
                    continue
                k = f"cer:{sid}" if sid else None
                ident = k or f"name:{s.get('name')}"
                stations.setdefault(ident, {"name": s.get("name"),
                                            "stop_id": sid, "key": k})
        out.append({
            "thread_id": key,
            "channel": first["channel"],
            "source": first["source"],
            "nucleo": nuc,
            "lines": sorted({ln for m in msgs for ln in (m["lines"] or [])}),
            "stations": list(stations.values()),
            "kind": first["kind"],
            "status": last["status"],
            "opened_at": first["posted_at"],
            "updated_at": last["posted_at"],
            "messages": [{"id": m["id"], "posted_at": m["posted_at"],
                          "text": m["text"], "status": m["status"],
                          "is_update": bool(m["is_update"]), "kind": m["kind"]}
                         for m in msgs],
            "attribution": _attribution(nuc, first["source"]),
        })
    out.sort(key=lambda t: -t["updated_at"])
    return out


def load_threads(estado: str = "abiertos", horas: int = 24,
                 nucleo_code: str | None = None, now: int | None = None) -> list[dict]:
    """Hilos de avisos. estado=abiertos: última situación abierta y con
    novedades en las últimas `horas`. estado=todos: sin filtro temporal."""
    now = int(now or time.time())
    cut = now - horas * 3600 if estado == "abiertos" else None
    where = ["status <> 'pendiente'"]
    params: dict = {}
    if nucleo_code:
        where.append("nucleo_code = :n")
        params["n"] = nucleo_code
    if cut is not None:
        where.append("COALESCE(thread_id, id) IN (SELECT COALESCE(thread_id, id)"
                     " FROM official_notice WHERE status <> 'pendiente'"
                     " AND posted_at >= :cut)")
        params["cut"] = cut
    sql = ("SELECT id, source, channel, posted_at, text, nucleo_code, lines,"
           " stations, kind, status, is_update, thread_id"
           " FROM official_notice WHERE " + " AND ".join(where)
           + " ORDER BY posted_at, id")
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text(sql), params).mappings().all()]
    threads = _build(rows)
    if estado == "abiertos":
        threads = [t for t in threads if t["status"] in OPEN_STATUSES
                   and t["updated_at"] >= cut]
    return threads


def threads_for_filters(nucleo: str | None = None, linea: str | None = None,
                        estacion: str | None = None, horas: int = 24) -> list[dict]:
    """Hilos abiertos para las vistas de /incidencias (nucleo, linea, estacion)."""
    code = NUCLEO_BY_SLUG.get(nucleo or "") if nucleo else None
    threads = load_threads("abiertos", horas, code)
    if estacion:
        sids = {p.split(":", 1)[1] for p in estacion.split(",")
                if p.startswith("cer:")}
        return [t for t in threads
                if any(s["stop_id"] in sids for s in t["stations"] if s["stop_id"])]
    if linea:
        return _by_line(threads, linea)
    return threads


def _by_line(threads: list[dict], linea: str) -> list[dict]:
    """Hilos con alguna línea igual a `linea` (C4a) o de su familia (c4)."""
    slug = linea.lower()
    return [t for t in threads if any(
        ln.lower() == slug or family_slug_of(ln) == slug for ln in t["lines"])]


@router.get("/avisos-oficiales")
def avisos_oficiales(nucleo: str | None = None, linea: str | None = None,
                     estado: str = Query("abiertos", pattern="^(abiertos|todos)$"),
                     horas: int = Query(24, ge=1, le=720)):
    """Avisos oficiales agrupados por hilo (WhatsApp / pegado manual)."""
    code = NUCLEO_BY_SLUG.get(nucleo or "") if nucleo else None
    if nucleo and not code:
        raise HTTPException(404, "núcleo desconocido")
    threads = load_threads(estado, horas, code)
    if linea:
        threads = _by_line(threads, linea)
    return {"generated_at": int(time.time()), "estado": estado, "horas": horas,
            "nucleo": nucleo, "linea": linea, "items": threads}


# ---------- alta manual (administrador) ----------

class AvisoIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    channel: str = Field(DEFAULT_CHANNEL, min_length=1, max_length=64)
    nucleo: str = "madrid"
    posted_at: int | str | None = None


def _check_admin(authorization: str | None) -> None:
    # se lee en cada petición (no al importar) para poder fijarla en tests
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        raise HTTPException(503, "admin desactivado")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "falta credencial de administrador")
    given = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "credencial inválida")


def _parse_posted(v) -> int:
    if v is None:
        return int(time.time())
    if isinstance(v, int):
        return v
    s = v.strip()
    if s.isdigit():
        return int(s)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "posted_at inválido (epoch o ISO 8601)") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return int(dt.timestamp())


@router.post("/admin/avisos")
def admin_aviso(body: AvisoIn, authorization: str | None = Header(None)):
    """Pega a mano un aviso oficial; el collector lo interpretará."""
    _check_admin(authorization)
    txt = body.text.strip()
    if not txt:
        raise HTTPException(400, "text vacío")
    code = NUCLEO_BY_SLUG.get(body.nucleo)
    if not code:
        raise HTTPException(400, "núcleo desconocido")
    posted = _parse_posted(body.posted_at)
    ext = hashlib.sha256(f"{txt}{posted}".encode()).hexdigest()[:32]
    now = int(time.time())
    with engine.begin() as c:
        row = c.execute(text("""
            INSERT INTO official_notice (source, channel, external_id, posted_at,
                received_at, text, nucleo_code, lines, stations, status, is_update)
            VALUES ('manual', :ch, :ext, :p, :r, :t, :n,
                CAST(:empty AS jsonb), CAST(:empty AS jsonb), 'pendiente', 0)
            ON CONFLICT (source, channel, external_id) DO NOTHING
            RETURNING id, status"""),
            {"ch": body.channel, "ext": ext, "p": posted, "r": now, "t": txt,
             "n": code, "empty": json.dumps([])}).first()
        if row is None:   # ya existía: idempotente
            row = c.execute(text("""SELECT id, status FROM official_notice
                WHERE source='manual' AND channel=:ch AND external_id=:ext"""),
                {"ch": body.channel, "ext": ext}).first()
    return {"id": row[0], "status": row[1],
            "note": "el collector lo interpretará en ≤1 min"}
