"""Incidencias oficiales (GTFS-RT Service Alerts de Renfe).

Principios:
- Se respetan los campos GTFS-RT tal cual llegan: activePeriod,
  informedEntity (agency/route/route_type/trip/stop/direction), textos por
  idioma, url, cause, effect y severityLevel. Lo que no viene no se inventa
  (no hay severidad si el feed no la publica).
- El ALCANCE oficial manda: un aviso con solo stopId es de esa estación y
  nunca se presenta como incidencia de toda la línea; un aviso con routeId
  es de la línea; routeId+stopId en la misma entidad = esa línea en esa
  estación (AND, especificación GTFS-RT).
- La clasificación (accesibilidad, obras, servicio interrumpido, servicio
  alternativo, otras) es explícita y auditable: cada categoría lleva el
  motivo (effect/cause oficial o regla de texto) que la justifica.
- Salud de la fuente separada del contenido: fuente operativa sin avisos
  ≠ fuente caída ≠ cobertura desconocida (AV/LD no tiene feed de avisos).
"""
import re
import time
import unicodedata
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from api.common import (
    NUCLEO_BY_SLUG,
    TZ,
    line_from_route_id_format,
    line_info,
    line_routes,
    nucleo_public,
)
from api.db import engine
from api.notices import threads_for_filters, whatsapp_health

router = APIRouter(prefix="/api/v1")

FEED_URLS = {"cer": "https://gtfsrt.renfe.com/alerts.json"}
FETCH_OK_MAX_AGE = 300        # sondeo cada 60 s: 5 min sin éxito = problema
RECENT_DAYS = 7
# contenido del feed oficial sin cambios desde hace más de 6 h = obsoleto
CONTENT_STALE_SEC = 21600
# Renfe publica incidencias de última hora en estos canales (sin datos abiertos)
OFFICIAL_CHANNELS = [
    {"name": "Canal de WhatsApp de Cercanías (Renfe)",
     "url": "https://www.renfe.com/es/es/ayuda/suscripcion-avisos-whatsapp"},
    {"name": "X / Twitter @CercaniasMadrid",
     "url": "https://x.com/CercaniasMadrid"},
    {"name": "Avisos de Renfe (sala de prensa)",
     "url": "https://grupo.renfe.com/es/es/sala-de-prensa/avisos"},
]

CAUSE_ES = {
    "UNKNOWN_CAUSE": "causa desconocida", "OTHER_CAUSE": "otra causa",
    "TECHNICAL_PROBLEM": "problema técnico", "STRIKE": "huelga",
    "DEMONSTRATION": "manifestación", "ACCIDENT": "accidente",
    "HOLIDAY": "festivo", "WEATHER": "meteorología",
    "MAINTENANCE": "mantenimiento", "CONSTRUCTION": "obras",
    "POLICE_ACTIVITY": "actuación policial",
    "MEDICAL_EMERGENCY": "emergencia médica",
}
EFFECT_ES = {
    "NO_SERVICE": "sin servicio", "REDUCED_SERVICE": "servicio reducido",
    "SIGNIFICANT_DELAYS": "retrasos importantes", "DETOUR": "desvío",
    "ADDITIONAL_SERVICE": "servicio adicional",
    "MODIFIED_SERVICE": "servicio modificado", "OTHER_EFFECT": "otro efecto",
    "UNKNOWN_EFFECT": "efecto desconocido", "STOP_MOVED": "parada trasladada",
    "NO_EFFECT": "sin efecto", "ACCESSIBILITY_ISSUE": "accesibilidad",
}

CATEGORIES = {
    "accesibilidad": "Accesibilidad",
    "obras": "Obras programadas",
    "interrumpido": "Servicio interrumpido",
    "alternativo": "Servicio alternativo",
    "otras": "Otras afectaciones",
}

# reglas de texto (sobre texto normalizado: minúsculas, sin acentos)
TEXT_RULES = {
    "accesibilidad": [
        "ascensor", "escalera mecanica", "escaleras mecanicas", "accesib",
        "movilidad reducida", "pmr", "salvaescaleras", "plataforma elevadora",
        "rampa"],
    "obras": [
        "obras", "trabajos de", "mantenimiento", "renovacion de via",
        "mejora en la infraestructura", "mejora de la infraestructura"],
    "interrumpido": [
        "no prestan servicio", "no presta servicio", "suspendido",
        "suspension del servicio", "se suspende", "interrump",
        "sin servicio", "no circulan", "no circula", "cortada", "cortado",
        "servicio suprimido", "se suprime"],
    "alternativo": [
        "autobus", "autobuses", "servicio alternativo", "plan alternativo",
        "transporte alternativo", "por carretera"],
}
EFFECT_CATEGORY = {
    "ACCESSIBILITY_ISSUE": "accesibilidad",
    "NO_SERVICE": "interrumpido",
}
CAUSE_CATEGORY = {"MAINTENANCE": "obras", "CONSTRUCTION": "obras"}

_TAG_RE = re.compile(r"#[\wÀ-ÿ]+")


def _plain(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def translations(ts) -> list[dict]:
    """GTFS-RT TranslatedString -> [{language, text}] (sin vacíos)."""
    out = []
    for t in (ts or {}).get("translation") or []:
        txt = (t.get("text") or "").strip()
        if txt:
            out.append({"language": (t.get("language") or "").strip() or None,
                        "text": txt})
    return out


def pick_lang(trs: list[dict], prefer=("es", "ca", "eu", "gl", None)) -> dict | None:
    for lang in prefer:
        for t in trs:
            if t["language"] == lang:
                return t
    return trs[0] if trs else None


def classify(effect: str | None, cause: str | None, body: str) -> list[dict]:
    """[{category, reason}] — multietiqueta, cada una con su justificación."""
    found: dict[str, str] = {}
    if effect in EFFECT_CATEGORY:
        found[EFFECT_CATEGORY[effect]] = f"effect={effect}"
    if cause in CAUSE_CATEGORY:
        found.setdefault(CAUSE_CATEGORY[cause], f"cause={cause}")
    p = _plain(body)
    for cat, needles in TEXT_RULES.items():
        if cat in found:
            continue
        for n in needles:
            if re.search(r"(?<![a-z])" + re.escape(n), p):
                found[cat] = f"texto:«{n}»"
                break
    if not found:
        found["otras"] = "ninguna regla"
    order = list(CATEGORIES)
    return [{"category": c, "label": CATEGORIES[c], "reason": found[c]}
            for c in sorted(found, key=order.index)]


def period_status(periods: list[dict], now: int) -> str:
    """active | upcoming | expired. Sin activePeriod = vigente mientras
    esté publicado (especificación GTFS-RT)."""
    if not periods:
        return "active"
    future = False
    for p in periods:
        s = p.get("start") or 0
        e = p.get("end") or 2**62
        if s <= now <= e:
            return "active"
        if s > now:
            future = True
    return "upcoming" if future else "expired"


# ---------- resolución de entidades ----------

_stop_cache: dict = {"ts": 0.0, "names": {}}


def stop_names() -> dict:
    if time.time() - _stop_cache["ts"] > 300:
        with engine.connect() as c:
            _stop_cache["names"] = {(r[0], r[1]): r[2] for r in c.execute(text(
                "SELECT feed, stop_id, name FROM stops"))}
        _stop_cache["ts"] = time.time()
    return _stop_cache["names"]


_STOPWORDS = {"estacion", "apeadero", "cercanias", "rodalies", "renfe",
              "aeropuerto", "centro", "norte", "sur", "puerto", "universidad",
              "hospital", "plaza", "san", "santa", "la", "el", "los", "las"}
_route_stops_cache: dict = {}


def _route_stop_ids(feed: str, rids: frozenset) -> set:
    hit = _route_stops_cache.get((feed, rids))
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    with engine.connect() as c:
        sids = {r[0] for r in c.execute(text("""
            SELECT DISTINCT st.stop_id FROM trips t
            JOIN stop_times st ON st.feed=t.feed AND st.trip_id=t.trip_id
            WHERE t.feed=:f AND t.route_id = ANY(:r)"""),
            {"f": feed, "r": sorted(rids)})}
    if len(_route_stops_cache) > 500:
        _route_stops_cache.clear()
    _route_stops_cache[(feed, rids)] = (time.time(), sids)
    return sids


def mentioned_stations(feed: str, rids: set, body: str) -> list[dict]:
    """Estaciones de las rutas indicadas cuyo nombre aparece literalmente
    en el texto (sin acentos, por palabra completa). Variantes de nombre
    ('Bilbao-Abando' -> 'abando') solo si son únicas en esas rutas."""
    if not rids or not body:
        return []
    names = stop_names()
    sids = _route_stop_ids(feed, frozenset(rids))
    cands: dict = {}
    for sid in sids:
        nm = names.get((feed, sid))
        if not nm:
            continue
        full = _plain(nm).strip()
        parts = {full} | {x.strip() for x in re.split(r"[-/(),]", full)}
        for part in parts:
            if len(part) >= 4 and part not in _STOPWORDS:
                cands.setdefault(part, set()).add(sid)
    p = _plain(body)
    found = {}
    for token, ss in cands.items():
        if len(ss) != 1:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", p):
            sid = next(iter(ss))
            found[sid] = {"feed": feed, "stop_id": sid,
                          "name": names[(feed, sid)], "key": f"{feed}:{sid}",
                          "inferred_from": "texto"}
    # si casi todas las estaciones aparecen, no es un aviso de estación
    if len(found) > 3:
        return []
    return sorted(found.values(), key=lambda x: x["name"])


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize(feed: str, alert_id: str, p: dict, now: int,
              first_seen=None, last_seen=None, feed_ts=None,
              in_feed=True) -> dict:
    names = stop_names()
    periods = [{"start": _int(ap.get("start")), "end": _int(ap.get("end"))}
               for ap in p.get("activePeriod") or []]
    header = translations(p.get("headerText"))
    desc = translations(p.get("descriptionText"))
    urls = translations(p.get("url"))
    h, d = pick_lang(header), pick_lang(desc)
    body = " ".join(x["text"] for x in (h, d) if x)
    tags = sorted(set(_TAG_RE.findall(body)))
    clean = lambda s: _TAG_RE.sub("", s).strip() if s else s  # noqa: E731

    ents, unmatched_routes, unmatched_stops = [], [], []
    lines: dict = {}
    stations: dict = {}
    scopes = set()
    for ie in p.get("informedEntity") or []:
        trip = ie.get("trip") or {}
        rid = (ie.get("routeId") or trip.get("routeId") or "").strip() or None
        sid = (ie.get("stopId") or "").strip() or None
        tid = (trip.get("tripId") or "").strip() or None
        ent = {"agency_id": ie.get("agencyId"), "route_id": rid,
               "route_type": ie.get("routeType"), "stop_id": sid,
               "direction_id": ie.get("directionId"),
               "trip": {"trip_id": tid, "start_date": trip.get("startDate"),
                        "route_id": trip.get("routeId")} if trip else None}
        li = None
        if rid:
            if (feed, rid) in line_routes():
                li = line_info(feed, rid)
                k = (li["nucleo"]["code"] if li["nucleo"] else None, li["code"])
                lines.setdefault(k, li)
            else:
                unmatched_routes.append(rid)
                li = line_from_route_id_format(feed, rid)
                if li:
                    k = (li["nucleo"]["code"], li["code"])
                    lines.setdefault(k, li)
        if sid:
            nm = names.get((feed, sid))
            if nm:
                stations.setdefault(sid, {"feed": feed, "stop_id": sid,
                                          "name": nm,
                                          "key": f"{feed}:{sid}"})
            else:
                unmatched_stops.append(sid)
        ent["line"] = li
        ent["station"] = stations.get(sid) if sid else None
        if tid:
            scopes.add("trip")
        elif sid and rid:
            scopes.add("route_at_stop")
        elif sid:
            scopes.add("station")
        elif rid:
            scopes.add("line")
        elif ie.get("agencyId"):
            scopes.add("network")
        ents.append(ent)
    scope = scopes.pop() if len(scopes) == 1 else ("mixed" if scopes else "unknown")
    effect, cause = p.get("effect"), p.get("cause")
    line_list = sorted(lines.values(), key=lambda li: (
        (li["nucleo"] or {}).get("name") or "", li["code"]))
    nucleos = {}
    for li in line_list:
        if li["nucleo"]:
            nucleos[li["nucleo"]["code"]] = li["nucleo"]
    # Avisos de línea cuyo texto nombra una estación concreta (ascensores,
    # escaleras…): el alcance oficial sigue siendo la línea, pero se anota
    # qué estaciones menciona el texto para no presentarlo como incidencia
    # en todas las estaciones. Es una inferencia y se etiqueta como tal.
    mentioned = []
    if scope == "line":
        rids = {e["route_id"] for e in ents if e["route_id"]}
        mentioned = mentioned_stations(feed, rids, body)
    return {
        "id": alert_id, "feed": feed,
        "status": period_status(periods, now) if in_feed else "withdrawn",
        "in_feed": in_feed,
        "periods": periods,
        "header": clean(h["text"]) if h else None,
        "description": clean(d["text"]) if d else None,
        "language": (d or h or {}).get("language"),
        "translations": {"header": header, "description": desc},
        "url": (pick_lang(urls) or {}).get("text"),
        "tags": tags,
        "cause": cause, "cause_label": CAUSE_ES.get(cause) if cause else None,
        "effect": effect,
        "effect_label": EFFECT_ES.get(effect) if effect else None,
        "severity": p.get("severityLevel"),     # solo si el feed la publica
        "categories": classify(effect, cause, body),
        "scope": scope,
        "lines": line_list,
        "nucleos": list(nucleos.values()),
        "stations": list(stations.values()),
        "mentioned_stations": mentioned,
        "entities": ents,
        "unmatched": {"routes": sorted(set(unmatched_routes)),
                      "stops": sorted(set(unmatched_stops))},
        "provenance": {
            "source": "Renfe Operadora · GTFS-RT Service Alerts",
            "feed_url": FEED_URLS.get(feed), "license": "CC-BY 4.0",
            "feed_ts": feed_ts, "first_seen": first_seen,
            "last_seen": last_seen},
    }


# ---------- salud de la fuente ----------

def source_health(feed: str, now: int | None = None) -> dict:
    now = int(now or time.time())
    if feed not in FEED_URLS:
        return {"feed": feed, "status": "not_available",
                "message": "Renfe no publica avisos GTFS-RT para esta red: "
                           "cobertura de incidencias desconocida."}
    keys = [f"alerts_fetch_ok_{feed}", f"alerts_fetch_err_{feed}",
            f"rt_alerts_{feed}", f"alerts_count_{feed}",
            f"alerts_content_changed_{feed}", f"alerts_content_hash_{feed}"]
    with engine.connect() as c:
        m = dict(c.execute(text("SELECT key, value FROM meta WHERE key = ANY(:k)"),
                           {"k": keys}).all())
    ok = _int(m.get(keys[0]))
    err = _int(m.get(keys[1]))
    feed_ts = _int(m.get(keys[2]))          # timestamp del header (no sirve de frescura)
    count = _int(m.get(keys[3]))
    changed = _int(m.get(keys[4]))          # último cambio REAL de contenido (hash)
    chash = m.get(keys[5]) or None
    chash = chash[:12] if chash else None
    content_ts = changed if changed is not None else feed_ts
    if ok is None:
        status = "unknown"
        msg = "Sin registro de descargas de la fuente: cobertura desconocida."
    elif now - ok <= FETCH_OK_MAX_AGE:
        status = "ok"
        msg = ("Fuente operativa sin avisos publicados." if count == 0
               else "Fuente operativa.")
    elif err and err > ok:
        status = "down"
        msg = ("La fuente oficial de avisos no responde desde "
               "la última descarga correcta: puede haber incidencias que "
               "no estamos viendo.")
    else:
        status = "stale"
        msg = "Sin descargas recientes de la fuente: datos posiblemente antiguos."
    # frescura del CONTENIDO (header del feed), distinta de la última descarga:
    # Renfe puede dejar de actualizar su feed aunque la descarga responda
    content_age = now - content_ts if content_ts is not None else None
    content_stale = (status == "ok" and content_age is not None
                     and content_age > CONTENT_STALE_SEC)
    if content_stale:
        when = datetime.fromtimestamp(content_ts, TZ).strftime("%d/%m %H:%M")
        msg = (f"Renfe no actualiza su feed oficial de avisos desde hace "
               f"{_human_age(content_age)} ({when}). Las incidencias de última "
               "hora que Renfe publica en sus canales de WhatsApp/X no tienen "
               "datos abiertos y pueden no aparecer aquí.")
    return {"feed": feed, "status": status, "message": msg,
            "last_ok": ok, "last_error": err, "feed_ts": feed_ts,
            "last_fetch_ok": ok, "last_fetch_error": err,
            "content_changed_at": content_ts, "header_ts": feed_ts,
            "content_hash": chash,
            # alias de compatibilidad: ahora = content_changed_at
            "content_ts": content_ts, "content_age_sec": content_age,
            "content_stale": content_stale,
            "official_channels": OFFICIAL_CHANNELS,
            "published": count, "feed_url": FEED_URLS[feed]}


def _human_age(sec: int) -> str:
    """Edad en horas (<48 h) o días, en español."""
    h = sec // 3600
    if h < 48:
        return "1 hora" if h == 1 else f"{h} horas"
    d = h // 24
    return "1 día" if d == 1 else f"{d} días"


# ---------- consulta ----------

def load_alerts(feed: str, now: int, include_recent: bool) -> list[dict]:
    with engine.connect() as c:
        cur = c.execute(text(
            "SELECT alert_id, payload, updated_at FROM alerts WHERE feed=:f"),
            {"f": feed}).all()
        seen = {}
        try:
            seen = {r[0]: (r[1], r[2], r[3]) for r in c.execute(text(
                "SELECT alert_id, first_seen, last_seen, payload FROM alerts_seen"
                " WHERE feed=:f AND last_seen > :cut"),
                {"f": feed, "cut": now - RECENT_DAYS * 86400})}
        except Exception:
            seen = {}
    out = []
    ids = set()
    for aid, payload, ts in cur:
        fs, ls, _ = seen.get(aid, (None, None, None))
        out.append(normalize(feed, aid, payload or {}, now, fs, ls, ts, True))
        ids.add(aid)
    if include_recent:
        for aid, (fs, ls, payload) in seen.items():
            if aid not in ids:
                out.append(normalize(feed, aid, payload or {}, now, fs, ls,
                                     None, False))
    return out


_station_routes_cache: dict = {}


def station_route_ids(feed: str, stop_id: str) -> set:
    k = (feed, stop_id)
    hit = _station_routes_cache.get(k)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    with engine.connect() as c:
        rids = {r[0] for r in c.execute(text("""
            SELECT DISTINCT t.route_id FROM stop_times st
            JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
            WHERE st.feed=:f AND st.stop_id=:s"""), {"f": feed, "s": stop_id})}
    if len(_station_routes_cache) > 3000:
        _station_routes_cache.clear()
    _station_routes_cache[k] = (time.time(), rids)
    return rids


def _route_ids_of(a: dict) -> set:
    return {e["route_id"] for e in a["entities"] if e["route_id"]}


def _line_scope_routes(a: dict) -> set:
    """route_ids mencionados SIN stop (aviso de línea completa)."""
    return {e["route_id"] for e in a["entities"]
            if e["route_id"] and not e["stop_id"] and not (e["trip"] or {}).get("trip_id")}


def _stop_ids_of(a: dict) -> set:
    return {e["stop_id"] for e in a["entities"] if e["stop_id"]}


def relevance_for_station(a: dict, feed: str, stop_id: str) -> str | None:
    """'station' si el aviso nombra la estación; 'line' si es un aviso de
    línea completa de una línea que para aquí; None si no aplica."""
    if a["feed"] != feed:
        return None
    if stop_id in _stop_ids_of(a):
        return "station"
    if _line_scope_routes(a) & station_route_ids(feed, stop_id):
        ment = {m["stop_id"] for m in a.get("mentioned_stations") or []}
        if not ment:
            return "line"
        # aviso de línea cuyo texto nombra otra(s) estación(es)
        return "line_mentions_station" if stop_id in ment else "line_other_station"
    return None


def relevance_for_routes(a: dict, rids: set) -> str | None:
    """Para una línea: 'line' si el aviso es de la línea completa;
    'station_on_line' si solo afecta a estaciones de la línea (route+stop
    o stop suelto en una de sus paradas)."""
    if _line_scope_routes(a) & rids:
        return "line"
    if any(e["route_id"] in rids and e["stop_id"] for e in a["entities"]):
        return "station_on_line"
    return None


def _station_ids_of_routes(rids: set) -> set:
    if not rids:
        return set()
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("""
            SELECT DISTINCT st.stop_id FROM trips t
            JOIN stop_times st ON st.feed=t.feed AND st.trip_id=t.trip_id
            WHERE t.feed='cer' AND t.route_id = ANY(:r)"""),
            {"r": sorted(rids)})}


def query(feed="cer", nucleo=None, linea=None, estacion=None, categoria=None,
          periodo="actuales", origen=None, destino=None, now=None) -> dict:
    now = int(now or time.time())
    include_recent = periodo in ("recientes", "todas")
    items = load_alerts(feed, now, include_recent) if feed in FEED_URLS else []
    if periodo == "vigentes":
        items = [a for a in items if a["status"] == "active"]
    elif periodo == "proximas":
        items = [a for a in items if a["status"] == "upcoming"]
    elif periodo == "recientes":
        items = [a for a in items if a["status"] in ("withdrawn", "expired")]
    elif periodo == "actuales":
        items = [a for a in items if a["status"] in ("active", "upcoming")]
    if categoria:
        items = [a for a in items
                 if categoria in {c["category"] for c in a["categories"]}]
    if nucleo:
        code = NUCLEO_BY_SLUG.get(nucleo)
        if not code:
            raise HTTPException(404, "núcleo desconocido")
        if linea:
            rids = {rid for (f, rid), r in line_routes().items()
                    if f == "cer" and r["nucleo_code"] == code
                    and linea in (r["line_slug"], r["family_slug"])}
            if not rids:
                raise HTTPException(404, "línea desconocida en ese núcleo")
        else:
            rids = {rid for (f, rid), r in line_routes().items()
                    if f == "cer" and r["nucleo_code"] == code}
        stations_on = _station_ids_of_routes(rids)
        keep = []
        for a in items:
            rel = relevance_for_routes(a, rids)
            if rel is None and (_stop_ids_of(a) & stations_on):
                rel = "station_on_line" if linea else "station_in_nucleo"
            if rel:
                keep.append({**a, "relevance": rel})
        items = keep
    if estacion:
        pairs = [p.split(":", 1) for p in estacion.split(",") if ":" in p]
        keep = []
        for a in items:
            rel = None
            for f, s in pairs:
                rel = rel or relevance_for_station(a, f, s)
            if rel:
                keep.append({**a, "relevance": rel})
        items = keep
    if origen and destino:
        o = [p.split(":", 1) for p in origen.split(",") if ":" in p]
        d = [p.split(":", 1) for p in destino.split(",") if ":" in p]
        common = set()
        for f1, s1 in o:
            for f2, s2 in d:
                if f1 == f2:
                    common |= station_route_ids(f1, s1) & station_route_ids(f2, s2)
        keep = []
        for a in items:
            rel = None
            for f, s in o:
                if relevance_for_station(a, f, s) == "station":
                    rel = "origin"
            for f, s in d:
                if not rel and relevance_for_station(a, f, s) == "station":
                    rel = "destination"
            if not rel and _line_scope_routes(a) & common:
                ment = {m["stop_id"] for m in a.get("mentioned_stations") or []}
                ends = {s for _, s in o} | {s for _, s in d}
                rel = ("line" if not ment else
                       "line_mentions_station" if ment & ends else
                       "line_other_station")
            if rel:
                keep.append({**a, "relevance": rel})
        items = keep
    items.sort(key=lambda a: ({"active": 0, "upcoming": 1}.get(a["status"], 2),
                              -max([p["start"] or 0 for p in a["periods"]] or [0])))
    counts: dict = {"by_category": {}, "by_status": {}, "by_nucleo": {}}
    for a in items:
        for c in a["categories"]:
            counts["by_category"][c["category"]] = \
                counts["by_category"].get(c["category"], 0) + 1
        counts["by_status"][a["status"]] = counts["by_status"].get(a["status"], 0) + 1
        for n in a["nucleos"]:
            counts["by_nucleo"][n["slug"]] = counts["by_nucleo"].get(n["slug"], 0) + 1
    return {"source": source_health(feed, now),
            "filters": {"feed": feed, "nucleo": nucleo, "linea": linea,
                        "estacion": estacion, "categoria": categoria,
                        "periodo": periodo},
            "categories": CATEGORIES,
            "total": len(items), "counts": counts, "items": items}


@router.get("/incidencias")
def incidencias(feed: str = Query("cer", pattern="^(cer|ld)$"),
                nucleo: str | None = None, linea: str | None = None,
                estacion: str | None = Query(None, description="feed:stop_id[,…]"),
                categoria: str | None = Query(
                    None, pattern="^(accesibilidad|obras|interrumpido|alternativo|otras)$"),
                periodo: str = Query(
                    "actuales", pattern="^(actuales|vigentes|proximas|recientes|todas)$"),
                aviso_estado: str = Query(
                    "abiertos", pattern="^(abiertos|todos)$"),
                aviso_horas: int = Query(24, ge=1, le=720),
                origen: str | None = None, destino: str | None = None):
    """Avisos oficiales normalizados. `periodo`: actuales (publicados,
    vigentes o futuros), vigentes, proximas, recientes (retirados o
    caducados en los últimos 7 días) o todas. `aviso_estado`/`aviso_horas`
    filtran los avisos de canales oficiales (WhatsApp/manual)."""
    out = query(feed, nucleo, linea, estacion, categoria, periodo,
                origen, destino)
    # avisos oficiales (WhatsApp/manual): solo Cercanías
    out["official_notices"] = (threads_for_filters(
        nucleo, linea, estacion, horas=aviso_horas, estado=aviso_estado)
        if feed == "cer" else [])
    out["sources"] = ({"whatsapp": whatsapp_health()} if feed == "cer" else {})
    return out


@router.get("/incidencias/audit")
def incidencias_audit(feed: str = "cer"):
    """Auditoría: volumen, frescura, entidades y fallos de correspondencia."""
    now = int(time.time())
    items = load_alerts(feed, now, include_recent=True) if feed in FEED_URLS else []
    cur = [a for a in items if a["in_feed"]]
    ent = {"route": 0, "stop": 0, "trip": 0, "agency": 0, "route_type": 0}
    unm_r, unm_s = set(), set()
    for a in cur:
        for e in a["entities"]:
            ent["route"] += bool(e["route_id"])
            ent["stop"] += bool(e["stop_id"])
            ent["trip"] += bool((e["trip"] or {}).get("trip_id"))
            ent["agency"] += bool(e["agency_id"])
            ent["route_type"] += e["route_type"] is not None
        unm_r |= set(a["unmatched"]["routes"])
        unm_s |= set(a["unmatched"]["stops"])
    by_scope: dict = {}
    for a in cur:
        by_scope[a["scope"]] = by_scope.get(a["scope"], 0) + 1
    fields = {k: sum(1 for a in cur if a[k]) for k in
              ("header", "description", "url", "cause", "effect", "severity")}
    return {
        "source": source_health(feed, now),
        "published": len(cur),
        "history_7d": len(items),
        "status": {s: sum(1 for a in cur if a["status"] == s)
                   for s in ("active", "upcoming", "expired")},
        "without_period": sum(1 for a in cur if not a["periods"]),
        "fields_present": fields,
        "entities": ent,
        "by_scope": by_scope,
        "without_nucleo": sum(1 for a in cur if not a["nucleos"]
                              and a["scope"] in ("line", "route_at_stop")),
        "unmatched_route_ids": sorted(unm_r),
        "unmatched_stop_ids": sorted(unm_s),
    }


@router.get("/incidencias/{feed}/{alert_id}")
def incidencia(feed: str, alert_id: str):
    now = int(time.time())
    for a in load_alerts(feed, now, include_recent=True) if feed in FEED_URLS else []:
        if a["id"] == alert_id:
            return {"source": source_health(feed, now), "item": a}
    raise HTTPException(404, "aviso no encontrado (o retirado hace más de 7 días)")


def nucleo_alert_counts(now=None) -> dict:
    """{nucleo_slug: nº avisos actuales que lo afectan} para portadas."""
    out: dict = {}
    for a in load_alerts("cer", int(now or time.time()), False):
        if a["status"] not in ("active", "upcoming"):
            continue
        for n in a["nucleos"]:
            out[n["slug"]] = out.get(n["slug"], 0) + 1
    return out


__all__ = [
    "nucleo_alert_counts",
    "nucleo_public",
    "query",
    "router",
    "source_health",
]
