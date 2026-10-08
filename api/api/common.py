"""Utilidades compartidas por los módulos de la API (sin dependencias
circulares con main.py)."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from api.db import engine

TZ = ZoneInfo("Europe/Madrid")

# Copia de collector/lines.py:NUCLEOS (contenedores separados). Un test
# verifica que ambas tablas son idénticas.
NUCLEOS = {
    "10": {"slug": "madrid", "name": "Madrid", "brand": "Cercanías"},
    "20": {"slug": "asturias", "name": "Asturias", "brand": "Cercanías"},
    "30": {"slug": "sevilla", "name": "Sevilla", "brand": "Cercanías"},
    "31": {"slug": "cadiz", "name": "Cádiz", "brand": "Cercanías"},
    "32": {"slug": "malaga", "name": "Málaga", "brand": "Cercanías"},
    "40": {"slug": "valencia", "name": "Valencia", "brand": "Cercanías"},
    "41": {"slug": "murcia-alicante", "name": "Murcia/Alicante",
           "brand": "Cercanías"},
    "45": {"slug": "cartagena", "name": "Cartagena", "brand": "Cercanías"},
    "46": {"slug": "ferrol", "name": "Ferrol", "brand": "Cercanías"},
    "47": {"slug": "leon", "name": "León", "brand": "Cercanías"},
    "50": {"slug": "rodalies-catalunya", "name": "Rodalies de Catalunya",
           "brand": "Rodalies"},
    "60": {"slug": "bilbao", "name": "Bilbao", "brand": "Cercanías"},
    "61": {"slug": "san-sebastian", "name": "San Sebastián",
           "brand": "Cercanías"},
    "62": {"slug": "cantabria", "name": "Cantabria", "brand": "Cercanías"},
    "70": {"slug": "zaragoza", "name": "Zaragoza", "brand": "Cercanías"},
}
OPERATOR = "Renfe Viajeros"
# prefijo route_id GTFS -> NUCLEO oficial (ver collector/lines.py)
GTFS_PREFIX = {p: p for p in NUCLEOS if p != "50"}
GTFS_PREFIX["51"] = "50"
NUCLEO_BY_SLUG = {v["slug"]: k for k, v in NUCLEOS.items()}


def now_parts():
    """(epoch, segundos desde medianoche local, fecha local) en Europe/Madrid."""
    n = datetime.now(TZ)
    return int(n.timestamp()), n.hour * 3600 + n.minute * 60 + n.second, n.date()


def local_midnight(day) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=TZ).timestamp())


def parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date debe ser YYYY-MM-DD") from None


def nucleo_public(code: str | None) -> dict | None:
    n = NUCLEOS.get(code or "")
    if not n:
        return None
    return {"code": code, "slug": n["slug"], "name": n["name"],
            "brand": n["brand"], "operator": OPERATOR}


_lr_cache: dict = {"ts": 0.0, "rows": {}}


def line_routes() -> dict:
    """line_route indexada por (feed, route_id). Cache 300 s."""
    if time.time() - _lr_cache["ts"] > 300:
        try:
            with engine.connect() as c:
                rows = c.execute(text("""
                    SELECT feed, route_id, nucleo_code, line_code, line_slug,
                           family_code, family_slug, mode, status, evidence,
                           long_name, color, text_color, n_trips
                    FROM line_route""")).mappings().all()
            _lr_cache["rows"] = {(r["feed"], r["route_id"]): dict(r) for r in rows}
            _lr_cache["ts"] = time.time()
        except Exception:
            # tabla aún no creada (collector < v0.3.4): sin identidad
            _lr_cache["rows"] = {}
    return _lr_cache["rows"]


def reset_caches():
    _lr_cache["ts"] = 0.0


def line_info(feed: str, route_id: str | None,
              short_name: str | None = None) -> dict | None:
    """Identidad pública de la línea de un viaje.

    {code, slug, family, family_slug, nucleo{...}|None, label, url,
     mode, status, color, text_color, route_id, feed}
    label = 'C4a · Madrid' solo si el núcleo es verificable; nunca se
    infiere el núcleo de la provincia ni del short_name."""
    lr = line_routes().get((feed, route_id or ""))
    code = (lr or {}).get("line_code") or (short_name or "").strip() or None
    if not code:
        return None
    nuc = nucleo_public((lr or {}).get("nucleo_code"))
    url = None
    if nuc and lr and lr.get("line_slug"):
        url = f"/lineas/{nuc['slug']}/{lr['line_slug']}"
    return {
        "code": code,
        "slug": (lr or {}).get("line_slug"),
        "family": (lr or {}).get("family_code"),
        "family_slug": (lr or {}).get("family_slug"),
        "nucleo": nuc,
        "label": f"{code} · {nuc['name']}" if nuc else code,
        "url": url,
        "mode": (lr or {}).get("mode"),
        "status": (lr or {}).get("status") or "unknown",
        "color": (lr or {}).get("color"),
        "text_color": (lr or {}).get("text_color"),
        "feed": feed, "route_id": route_id,
    }


def routes_for(nucleo_slug: str | None, line_slug: str | None) -> set | None:
    """route_ids (feed cer) de un núcleo y, opcionalmente, de una línea.
    `line_slug` acepta familia (c4 -> C4, C4a, C4b) o variante (c4a).
    None = sin filtro. Conjunto vacío = filtro sin coincidencias."""
    if not nucleo_slug and not line_slug:
        return None
    code = NUCLEO_BY_SLUG.get(nucleo_slug or "")
    if nucleo_slug and not code:
        return set()
    out = set()
    for (feed, rid), r in line_routes().items():
        if feed != "cer" or not r["nucleo_code"]:
            continue
        if code and r["nucleo_code"] != code:
            continue
        if line_slug and line_slug not in (r["line_slug"], r["family_slug"]):
            continue
        out.add(rid)
    return out


_RID_RE = __import__("re").compile(r"^(\d{2})T\d{4}([A-Za-z][A-Za-z0-9]*)$")


def line_from_route_id_format(feed: str, route_id: str) -> dict | None:
    """Identidad mínima de un route_id CER que NO está en el GTFS vigente
    (p. ej. avisos de Rodalies con rutas ausentes del GTFS publicado).

    Se apoya solo en el formato oficial del identificador (prefijo de
    núcleo + código comercial final) y se marca status='route_id_format'
    para que nunca se confunda con una correspondencia verificada."""
    if feed != "cer":
        return None
    m = _RID_RE.match(route_id or "")
    if not m or m.group(1) not in GTFS_PREFIX:
        return None
    code = m.group(2)
    nuc = nucleo_public(GTFS_PREFIX[m.group(1)])
    return {"code": code, "slug": code.lower(), "family": None,
            "family_slug": None, "nucleo": nuc,
            "label": f"{code} · {nuc['name']}", "url": None, "mode": None,
            "status": "route_id_format", "color": None, "text_color": None,
            "feed": feed, "route_id": route_id}
