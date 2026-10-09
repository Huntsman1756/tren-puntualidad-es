"""Núcleos, líneas y ranking en vivo con identidad de línea inequívoca.

Ranking en vivo — dos vistas que no se mezclan:
- trenes individuales (`/delays/ranking`): retraso informado por tren.
- agregado por línea (`/delays/lines`): trenes con dato RT, trenes con
  retraso informado ≥ umbral, retraso máximo y cobertura = trenes con dato
  RT / trenes que según el horario deberían estar circulando ahora.
Ninguno es puntualidad histórica: es una foto del momento.
"""
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from api.common import (
    NUCLEO_BY_SLUG,
    NUCLEOS,
    line_info,
    line_routes,
    now_parts,
    nucleo_public,
    routes_for,
)
from api.db import engine

router = APIRouter(prefix="/api/v1")

RT_FRESH_SEC = 900
# flota.json publica a veces retrasoMin absurdos (p. ej. -1438 en trenes que
# cruzan medianoche): fuera de este rango se trata como "sin dato".
FLEET_MIN, FLEET_MAX = -60, 600

# retraso efectivo: observado (flota, si plausible) > predicción del viaje
EFF_DELAY_SQL = f"""
    CASE WHEN fl.delay_min BETWEEN {FLEET_MIN} AND {FLEET_MAX}
         THEN fl.delay_min * 60 ELSE rt.delay END"""
EFF_SRC_SQL = f"""
    CASE WHEN fl.delay_min BETWEEN {FLEET_MIN} AND {FLEET_MAX}
         THEN 'observed' WHEN rt.delay IS NOT NULL THEN 'predicted' END"""


def _families(code: str) -> dict:
    """{family_slug: {code, variants{slug: {...}}, routes, color}} para un
    núcleo, solo rutas con viajes en el GTFS vigente."""
    fam: dict = {}
    for (feed, rid), r in line_routes().items():
        if feed != "cer" or r["nucleo_code"] != code or not r["line_slug"]:
            continue
        if not r["n_trips"]:
            continue
        f = fam.setdefault(r["family_slug"], {
            "code": r["family_code"], "slug": r["family_slug"],
            "color": None, "text_color": None, "variants": {},
            "routes": 0, "trips": 0, "modes": set()})
        v = f["variants"].setdefault(r["line_slug"], {
            "code": r["line_code"], "slug": r["line_slug"], "routes": [],
            "color": r["color"], "trips": 0, "modes": set()})
        v["routes"].append({"route_id": rid, "long_name": r["long_name"],
                            "mode": r["mode"], "status": r["status"],
                            "trips": r["n_trips"]})
        v["trips"] += r["n_trips"]
        v["modes"].add(r["mode"])
        f["routes"] += 1
        f["trips"] += r["n_trips"]
        f["modes"].add(r["mode"])
        if r["mode"] == "tren" and r["color"] and not f["color"]:
            f["color"], f["text_color"] = r["color"], r["text_color"]
    return fam


def _natural(code: str):
    import re
    m = re.match(r"([A-Za-z]+)(\d+)(.*)", code or "")
    return (m.group(1), int(m.group(2)), m.group(3)) if m else (code, 0, "")


def _fam_public(f: dict, nslug: str) -> dict:
    variants = sorted(f["variants"].values(), key=lambda v: _natural(v["code"]))
    return {
        "code": f["code"], "slug": f["slug"], "color": f["color"],
        "text_color": f["text_color"],
        "url": f"/lineas/{nslug}/{f['slug']}",
        "routes": f["routes"], "trips": f["trips"],
        "bus_only": f["modes"] == {"bus"},
        "variants": [{"code": v["code"], "slug": v["slug"],
                      "url": f"/lineas/{nslug}/{v['slug']}",
                      "trips": v["trips"], "routes": len(v["routes"]),
                      "bus_only": v["modes"] == {"bus"}}
                     for v in variants],
    }


@router.get("/nucleos")
def nucleos():
    """Núcleos de Cercanías/Rodalies con sus líneas (familias y variantes)."""
    from api.incidents import nucleo_alert_counts
    try:
        alerts = nucleo_alert_counts()
    except Exception:
        alerts = {}
    out = []
    for code, n in NUCLEOS.items():
        fams = _families(code)
        if not fams:
            continue
        pub = nucleo_public(code)
        out.append({**pub, "url": f"/nucleos/{n['slug']}",
                    "lines": [_fam_public(f, n["slug"]) for f in
                              sorted(fams.values(), key=lambda f: _natural(f["code"]))],
                    "alerts": alerts.get(n["slug"], 0)})
    return sorted(out, key=lambda x: x["name"])


@router.get("/nucleos/{slug}")
def nucleo(slug: str):
    code = NUCLEO_BY_SLUG.get(slug)
    if not code:
        raise HTTPException(404, "núcleo desconocido")
    fams = _families(code)
    rids = routes_for(slug, None) or set()
    with engine.connect() as c:
        n_st = c.execute(text("""
            SELECT count(DISTINCT st.stop_id) FROM trips t
            JOIN stop_times st ON st.feed=t.feed AND st.trip_id=t.trip_id
            WHERE t.feed='cer' AND t.route_id = ANY(:r)"""),
            {"r": sorted(rids)}).scalar() if rids else 0
    return {**nucleo_public(code), "url": f"/nucleos/{slug}",
            "lines": [_fam_public(f, slug) for f in
                      sorted(fams.values(), key=lambda f: _natural(f["code"]))],
            "stations": n_st,
            "routes": len(rids)}


def _pattern(c, rids: list[str]) -> list[dict]:
    """Secuencia de paradas del viaje con más paradas entre las rutas dadas
    (patrón representativo de la variante, un sentido)."""
    row = c.execute(text("""
        SELECT ts.trip_id FROM trip_span ts
        JOIN trips t ON t.feed=ts.feed AND t.trip_id=ts.trip_id
        WHERE ts.feed='cer' AND t.route_id = ANY(:r)
        ORDER BY ts.n_stops DESC, ts.trip_id LIMIT 1"""), {"r": rids}).first()
    if not row:
        return []
    return [dict(r) for r in c.execute(text("""
        SELECT st.stop_id, s.name, s.lat, s.lon FROM stop_times st
        JOIN stops s ON s.feed=st.feed AND s.stop_id=st.stop_id
        WHERE st.feed='cer' AND st.trip_id=:t ORDER BY st.seq"""),
        {"t": row[0]}).mappings()]


@router.get("/lineas/{nslug}/{lslug}")
def linea(nslug: str, lslug: str):
    """Ficha de línea: identidad, variantes, route_ids (correspondencia
    verificable), estaciones por variante y trenes en circulación."""
    code = NUCLEO_BY_SLUG.get(nslug)
    if not code:
        raise HTTPException(404, "núcleo desconocido")
    fams = _families(code)
    fam = fams.get(lslug)
    variant = None
    if not fam:
        for f in fams.values():
            if lslug in f["variants"]:
                fam, variant = f, f["variants"][lslug]
                break
    if not fam:
        raise HTTPException(404, "línea desconocida en este núcleo")
    members = [variant] if variant else list(fam["variants"].values())
    rids = sorted(r["route_id"] for v in members for r in v["routes"])
    with engine.connect() as c:
        patterns = []
        for v in sorted(members, key=lambda v: _natural(v["code"])):
            rail = [r["route_id"] for r in v["routes"] if r["mode"] == "tren"] \
                or [r["route_id"] for r in v["routes"]]
            patterns.append({"code": v["code"], "slug": v["slug"],
                             "stops": _pattern(c, rail)})
        stations = [dict(r) for r in c.execute(text("""
            SELECT st.stop_id, s.name, count(DISTINCT t.trip_id) AS trips
            FROM trips t
            JOIN stop_times st ON st.feed=t.feed AND st.trip_id=t.trip_id
            JOIN stops s ON s.feed=st.feed AND s.stop_id=st.stop_id
            WHERE t.feed='cer' AND t.route_id = ANY(:r)
            GROUP BY st.stop_id, s.name ORDER BY s.name"""),
            {"r": rids}).mappings()]
    first = next(iter(members))
    color = first.get("color") or fam["color"]
    return {
        "nucleo": nucleo_public(code),
        "family": _fam_public(fam, nslug),
        "line": {"code": variant["code"] if variant else fam["code"],
                 "slug": lslug, "is_family": variant is None,
                 "label": f"{variant['code'] if variant else fam['code']} · "
                          f"{NUCLEOS[code]['name']}",
                 "color": color},
        "routes": sorted([{**r, "line_code": v["code"]} for v in members
                          for r in v["routes"]], key=lambda r: r["route_id"]),
        "patterns": patterns,
        "stations": [{**s, "key": f"cer:{s['stop_id']}"} for s in stations],
        "live": live_trains(nucleo=nslug, linea=lslug, limit=200, min_delay=None),
    }


@router.get("/lineas")
def lineas_index():
    return nucleos()


@router.get("/lineas-audit")
def lineas_audit(status: str | None = None):
    """Correspondencia route_id → núcleo → línea comercial, con la
    evidencia (prefijo y votos de paradas del visor oficial)."""
    rows = []
    for (feed, rid), r in sorted(line_routes().items()):
        if status and r["status"] != status:
            continue
        rows.append({"feed": feed, "route_id": rid,
                     "nucleo": nucleo_public(r["nucleo_code"]),
                     "line_code": r["line_code"], "family": r["family_code"],
                     "mode": r["mode"], "status": r["status"],
                     "trips": r["n_trips"], "evidence": r["evidence"],
                     "long_name": r["long_name"]})
    summary: dict = defaultdict(int)
    for r in rows:
        summary[r["status"]] += 1
    return {"summary": dict(summary), "total": len(rows), "items": rows}


def live_trains(nucleo=None, linea=None, feed=None, min_delay=60,
                limit=50, ccaa_cond="", params=None) -> list[dict]:
    """Trenes con dato RT fresco (próxima parada en el futuro), con su
    identidad de línea. min_delay None = todos los monitorizados."""
    rids = routes_for(nucleo, linea)
    if rids is not None and not rids:
        return []
    now = int(time.time())
    cond = ""
    p = {"now": now, "fresh": now - RT_FRESH_SEC, "lim": limit,
         "feed": feed, **(params or {})}
    if rids is not None:
        cond += " AND rt.feed='cer' AND t.route_id = ANY(:rids)"
        p["rids"] = sorted(rids)
    if min_delay is not None:
        cond += f" AND ({EFF_DELAY_SQL}) >= :mind"
        p["mind"] = min_delay
    sql = text(f"""
        SELECT rt.feed, rt.trip_id, t.route_id, t.train_number,
               r.short_name AS line,
               ({EFF_DELAY_SQL}) AS delay, {EFF_SRC_SQL} AS delay_source,
               rt.next_stop_id, rt.next_stop_time, rt.sched_rel,
               s.name AS next_stop_name, rt.updated_at,
               (SELECT s2.name FROM stop_times x JOIN stops s2
                  ON s2.feed=x.feed AND s2.stop_id=x.stop_id
                 WHERE x.feed=rt.feed AND x.trip_id=rt.trip_id
                 ORDER BY x.seq DESC LIMIT 1) AS destination
        FROM rt_trip rt
        LEFT JOIN trips t ON t.feed=rt.feed AND t.trip_id=rt.trip_id
        LEFT JOIN routes r ON r.feed=rt.feed AND r.route_id=t.route_id
        LEFT JOIN stops s ON s.feed=rt.feed AND s.stop_id=rt.next_stop_id
        LEFT JOIN rt_fleet fl ON fl.feed=rt.feed AND fl.trip_id=rt.trip_id
        WHERE rt.updated_at > :fresh AND rt.next_stop_time > :now
          AND (CAST(:feed AS text) IS NULL OR rt.feed = :feed)
          {cond} {ccaa_cond}
        ORDER BY delay DESC NULLS LAST LIMIT :lim""")
    with engine.connect() as c:
        rows = c.execute(sql, p).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["line_info"] = line_info(d["feed"], d["route_id"], d["line"])
        d["cancelled"] = d.pop("sched_rel") == "CANCELED"
        out.append(d)
    return out


def scheduled_now_by_route(c, rids, day, secs) -> dict:
    """Circulaciones que, según el horario de `day`, deberían estar en marcha
    a `secs` (primera salida ≤ ahora ≤ última llegada), por route_id."""
    if not rids:
        return {}
    rows = c.execute(text("""
        SELECT t.route_id, count(*) FROM trip_span ts
        JOIN trips t ON t.feed=ts.feed AND t.trip_id=ts.trip_id
        JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
             AND sd.day BETWEEN CAST(:day AS date) - 1 AND CAST(:day AS date)
        WHERE ts.feed='cer' AND t.route_id = ANY(:r)
          AND ts.first_dep + (sd.day - CAST(:day AS date)) * 86400 <= :s
          AND ts.last_arr + (sd.day - CAST(:day AS date)) * 86400 >= :s
        GROUP BY t.route_id"""),
        {"r": sorted(rids), "day": day, "s": secs}).all()
    return {rid: n for rid, n in rows}


@router.get("/delays/lines")
def delays_by_line(nucleo: str | None = None,
                   min_delay: int = Query(180, ge=60, le=3600)):
    """Agregado EN VIVO por línea (familia) de Cercanías/Rodalies.

    - monitored: trenes de la línea con dato RT fresco ahora.
    - delayed: de ellos, con retraso informado ≥ min_delay.
    - max_delay_sec: mayor retraso informado.
    - scheduled_now: circulaciones que según el horario de hoy deberían
      estar en marcha (primera salida ≤ ahora ≤ última llegada).
    - coverage_pct = monitored / scheduled_now (acotado a 100).
    No es puntualidad histórica."""
    code = NUCLEO_BY_SLUG.get(nucleo or "") if nucleo else None
    if nucleo and not code:
        raise HTTPException(404, "núcleo desconocido")
    now, secs, today = now_parts()
    lr = {rid: r for (f, rid), r in line_routes().items()
          if f == "cer" and r["nucleo_code"] and (not code or r["nucleo_code"] == code)}
    if not lr:
        return {"generated_at": now, "min_delay": min_delay, "items": [],
                "semantics": "snapshot_reported_delay"}
    rids = sorted(lr)
    with engine.connect() as c:
        sched = scheduled_now_by_route(c, rids, today, secs)
        live = c.execute(text(f"""
            SELECT t.route_id, ({EFF_DELAY_SQL}) AS delay
            FROM rt_trip rt
            JOIN trips t ON t.feed=rt.feed AND t.trip_id=rt.trip_id
            LEFT JOIN rt_fleet fl ON fl.feed=rt.feed AND fl.trip_id=rt.trip_id
            WHERE rt.feed='cer' AND rt.updated_at > :fresh
              AND rt.next_stop_time > :now AND t.route_id = ANY(:r)"""),
            {"r": rids, "fresh": now - RT_FRESH_SEC, "now": now}).all()
    agg: dict = {}

    def bucket(rid):
        r = lr[rid]
        k = (r["nucleo_code"], r["family_slug"])
        return agg.setdefault(k, {
            "nucleo": nucleo_public(r["nucleo_code"]),
            "line": {"code": r["family_code"], "slug": r["family_slug"],
                     "url": f"/lineas/{NUCLEOS[r['nucleo_code']]['slug']}/{r['family_slug']}",
                     "label": f"{r['family_code']} · {NUCLEOS[r['nucleo_code']]['name']}",
                     "color": None},
            "scheduled_now": 0, "monitored": 0, "delayed": 0,
            "max_delay_sec": None})

    for rid, n in sched.items():
        bucket(rid)["scheduled_now"] += n
        if lr[rid]["mode"] == "tren" and lr[rid]["color"]:
            bucket(rid)["line"]["color"] = bucket(rid)["line"]["color"] or lr[rid]["color"]
    for rid, delay in live:
        b = bucket(rid)
        b["monitored"] += 1
        if delay is not None:
            if delay >= min_delay:
                b["delayed"] += 1
            if b["max_delay_sec"] is None or delay > b["max_delay_sec"]:
                b["max_delay_sec"] = delay
    items = []
    for b in agg.values():
        b["coverage_pct"] = (min(100, round(100 * b["monitored"] / b["scheduled_now"]))
                             if b["scheduled_now"] else None)
        items.append(b)
    items.sort(key=lambda b: (-b["delayed"], -(b["max_delay_sec"] or 0),
                              b["nucleo"]["name"], _natural(b["line"]["code"])))
    return {"generated_at": now, "min_delay": min_delay,
            "semantics": "snapshot_reported_delay",
            "note": "Foto en vivo del retraso informado por Renfe; no es "
                    "puntualidad histórica. Cobertura = trenes con dato en "
                    "tiempo real / trenes que según horario circulan ahora.",
            "items": items}


@router.get("/stations/{feed}/{stop_id}/lines")
def station_lines(feed: str, stop_id: str):
    """Líneas que sirven la estación (según los viajes del GTFS vigente)."""
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT t.route_id, r.short_name, count(*) AS trips
            FROM stop_times st
            JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
            LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
            WHERE st.feed=:f AND st.stop_id=:s
            GROUP BY t.route_id, r.short_name"""),
            {"f": feed, "s": stop_id}).all()
    out: dict = {}
    for rid, short, n in rows:
        li = line_info(feed, rid, short)
        if not li:
            continue
        k = ((li["nucleo"] or {}).get("code"), li["code"])
        e = out.setdefault(k, {**li, "trips": 0})
        e["trips"] += n
    return sorted(out.values(), key=lambda x: _natural(x["code"]))

