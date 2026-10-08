"""Identidad canónica de líneas: operador · núcleo · feed · route_id · código.

Problema: `route_short_name` (C1, C2…) se repite entre núcleos (C1 existe en
Madrid, Asturias, Sevilla, Valencia…). Ni el short_name ni la provincia de la
próxima parada bastan para saber a qué red pertenece una ruta.

Fuentes y reglas (todas verificables, nada se deduce del nombre):

1. `route_id` del GTFS CER (`10T0011C4`): los dos primeros dígitos son el
   código de núcleo con el que Renfe construye el identificador. La tabla
   GTFS_PREFIX traduce prefijo → código NUCLEO oficial del visor Renfe.
   Única excepción observada: Rodalies usa prefijo 51 en GTFS y NUCLEO 50
   en el visor (comprobado 2026-10-08: 1010/1010 paradas clasificadas de
   rutas 51* están en el núcleo 50).
2. Contraste independiente: `tiempo-real.renfe.com/data/estaciones.geojson`
   publica NUCLEO por CODIGO_ESTACION. Para cada ruta se cuentan los votos
   de sus paradas. Estado de la correspondencia:
     verified     el núcleo del prefijo es el mayoritario entre las paradas
                  clasificadas de la ruta
     prefix_only  sin paradas clasificadas en el visor: solo prefijo
     conflict     las paradas apuntan mayoritariamente a otro núcleo; la
                  ruta NO se asigna (nucleo_code NULL) y queda en auditoría
     unmapped     prefijo desconocido
     not_applicable  feed LD (AV/LD/MD no se organiza por núcleos)
3. Código comercial: short_name normalizado sin distinguir mayúsculas
   (C4A ≡ C4a), con la grafía oficial del visor (LINEAS) si existe.
   Familia = letras+dígitos (C4a, C4b → C4; R2N, R2S → R2): permite
   agrupar variantes y ramales que comparten código comercial.
"""
import collections
import json
import logging
import re
import time

import httpx
from sqlalchemy import text

from collector.config import STATIONS_GEOJSON

log = logging.getLogger("collector.lines")

# código NUCLEO oficial (visor Renfe) -> identidad pública
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

# prefijo de route_id GTFS CER -> código NUCLEO oficial
GTFS_PREFIX = {p: p for p in NUCLEOS if p != "50"}
GTFS_PREFIX["51"] = "50"

_CODE_RE = re.compile(r"^([A-Za-z]+)(\d+)([A-Za-z]*)$")


def family_of(code: str | None) -> str | None:
    """'C4a' -> 'C4', 'R2N' -> 'R2', 'RG1' -> 'RG1', 'BUS' -> 'BUS'."""
    if not code:
        return None
    m = _CODE_RE.match(code)
    return f"{m.group(1).upper()}{m.group(2)}" if m else code


def slug_of(code: str | None) -> str | None:
    return re.sub(r"[^a-z0-9]+", "-", code.lower()).strip("-") if code else None


def nucleo_from_route_id(route_id: str) -> str | None:
    """Código NUCLEO candidato según el prefijo del route_id (o None)."""
    m = re.match(r"^(\d{2})T", route_id or "")
    return GTFS_PREFIX.get(m.group(1)) if m else None


def classify_route(route_id: str, votes: dict) -> tuple[str | None, str]:
    """(nucleo_code asignado, status) a partir del prefijo y de los votos
    {nucleo_code: n_paradas} del visor oficial."""
    cand = nucleo_from_route_id(route_id)
    if cand is None:
        return None, "unmapped"
    known = {k: v for k, v in votes.items() if k}
    if not known:
        return cand, "prefix_only"
    top = max(known.values())
    if known.get(cand, 0) == top:
        return cand, "verified"
    return None, "conflict"


def fetch_station_nucleos() -> dict:
    """{codigo5: {'nucleo': '10', 'lineas': 'C1,C10'}} del visor oficial."""
    r = httpx.get(STATIONS_GEOJSON, timeout=60, follow_redirects=True)
    r.raise_for_status()
    out = {}
    for f in r.json().get("features", []):
        p = f.get("properties") or {}
        code = str(p.get("CODIGO_ESTACION") or "").strip()
        nuc = str(p.get("NUCLEO") or "").strip()
        if not code or not nuc:
            continue
        out[code.zfill(5)] = {"nucleo": nuc,
                              "lineas": (p.get("LINEAS") or "").strip() or None}
    return out


def store_station_nucleos(conn, data: dict):
    """Persistencia del contraste oficial (sustituye solo si hay datos)."""
    if not data:
        return 0
    conn.execute(text("DELETE FROM station_nucleo"))
    conn.execute(text("""
        INSERT INTO station_nucleo (code, nucleo_code, lineas, fetched_at)
        VALUES (:c, :n, :l, :ts)"""),
        [{"c": c, "n": v["nucleo"], "l": v["lineas"], "ts": int(time.time())}
         for c, v in data.items()])
    return len(data)


def _canonical_codes(rows, official: dict) -> dict:
    """{(nucleo, lower_code): grafía} — oficial (LINEAS del visor) si
    existe, si no la más frecuente en el GTFS."""
    seen: dict = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["nucleo"] and r["short"]:
            seen[(r["nucleo"], r["short"].lower())][r["short"]] += 1
    out = {}
    for (nuc, low), cnt in seen.items():
        off = official.get((nuc, low))
        out[(nuc, low)] = off or cnt.most_common(1)[0][0]
    return out


def compute_line_routes(conn) -> dict:
    """Recalcula `line_route` para ambos feeds. Idempotente."""
    st_nuc = {r[0]: r[1] for r in conn.execute(text(
        "SELECT code, nucleo_code FROM station_nucleo"))}
    official = {}
    for nuc, lineas in conn.execute(text(
            "SELECT nucleo_code, lineas FROM station_nucleo"
            " WHERE lineas IS NOT NULL")):
        for code in re.split(r"[,;\s]+", lineas):
            if code:
                official[(nuc, code.lower())] = code
    routes = [dict(r) for r in conn.execute(text("""
        SELECT feed, route_id, short_name AS short, long_name, route_type,
               color, text_color FROM routes""")).mappings()]
    stops_of: dict = collections.defaultdict(set)
    for rid, sid in conn.execute(text("""
            SELECT DISTINCT t.route_id, st.stop_id
            FROM trips t JOIN stop_times st
              ON st.feed=t.feed AND st.trip_id=t.trip_id
            WHERE t.feed='cer'""")):
        stops_of[rid].add(sid)
    n_trips = dict(((f, r), n) for f, r, n in conn.execute(text(
        "SELECT feed, route_id, count(*) FROM trips GROUP BY feed, route_id")))
    now = int(time.time())
    rows = []
    for r in routes:
        short = (r["short"] or "").strip() or None
        if r["feed"] != "cer":
            r.update(nucleo=None, status="not_applicable", short=short,
                     evidence={"product": short})
            rows.append(r)
            continue
        votes = collections.Counter(st_nuc.get(s) for s in stops_of[r["route_id"]])
        nuc, status = classify_route(r["route_id"], votes)
        m = re.match(r"^(\d{2})T", r["route_id"])
        r.update(nucleo=nuc, status=status, short=short, evidence={
            "prefix": m.group(1) if m else None,
            "prefix_nucleo": nucleo_from_route_id(r["route_id"]),
            "stop_votes": {k or "sin_dato": v for k, v in votes.items()},
            "stops": len(stops_of[r["route_id"]]),
        })
        rows.append(r)
    canon = _canonical_codes(rows, official)
    out = []
    for r in rows:
        code = r["short"]
        if r["nucleo"] and code:
            code = canon.get((r["nucleo"], code.lower()), code)
        out.append({
            "f": r["feed"], "r": r["route_id"], "n": r["nucleo"],
            "code": code, "slug": slug_of(code),
            "fam": family_of(code), "fslug": slug_of(family_of(code)),
            "mode": "bus" if r["route_type"] == 3 else "tren",
            "st": r["status"], "ev": json.dumps(r["evidence"]),
            "ln": (r["long_name"] or "").strip() or None,
            "color": (r["color"] or "").strip() or None,
            "tcolor": (r["text_color"] or "").strip() or None,
            "nt": n_trips.get((r["feed"], r["route_id"]), 0),
            "ts": now,
        })
    conn.execute(text("DELETE FROM line_route"))
    if out:
        conn.execute(text("""
            INSERT INTO line_route (feed, route_id, nucleo_code, line_code,
                line_slug, family_code, family_slug, mode, status, evidence,
                long_name, color, text_color, n_trips, updated_at)
            VALUES (:f, :r, :n, :code, :slug, :fam, :fslug, :mode, :st,
                    CAST(:ev AS jsonb), :ln, :color, :tcolor, :nt, :ts)"""), out)
    stats = collections.Counter(o["st"] for o in out)
    return dict(stats)


def compute_trip_spans(conn, feed: str) -> int:
    """Primera salida y última llegada programadas por viaje (para saber
    qué circulaciones deberían estar en marcha sin recorrer stop_times
    en cada petición)."""
    conn.execute(text("DELETE FROM trip_span WHERE feed=:f"), {"f": feed})
    conn.execute(text("""
        INSERT INTO trip_span (feed, trip_id, first_dep, last_arr, n_stops)
        SELECT feed, trip_id, min(COALESCE(dep, arr)), max(COALESCE(arr, dep)),
               count(*)
        FROM stop_times WHERE feed=:f GROUP BY feed, trip_id"""), {"f": feed})
    return conn.execute(text(
        "SELECT count(*) FROM trip_span WHERE feed=:f"), {"f": feed}).scalar()


def refresh_lines(engine, fetch: bool = True) -> dict:
    """Descarga el contraste oficial (si se pide) y recalcula line_route.
    Si el visor no responde se conserva el último contraste guardado."""
    data = {}
    if fetch:
        try:
            data = fetch_station_nucleos()
        except Exception:
            log.exception("estaciones.geojson no disponible; se usa el último guardado")
    with engine.begin() as conn:
        n = store_station_nucleos(conn, data)
        stats = compute_line_routes(conn)
        conn.execute(text(
            "INSERT INTO meta(key,value) VALUES('line_routes_stats',:v) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value"),
            {"v": json.dumps({"stations_official": n, **stats,
                              "ts": int(time.time())})})
    return {"stations_official": n, **stats}
