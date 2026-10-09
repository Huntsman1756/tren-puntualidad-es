"""Posibles incidencias INFERIDAS de retrasos informados en tiempo real.

No son avisos oficiales de Renfe. Una línea (núcleo + familia) aparece solo
cuando varios trenes con dato RT fresco llevan retraso a la vez, con la
misma regla de retraso efectivo que /delays/lines (api.lines_api).
"""
import statistics

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from api.common import NUCLEO_BY_SLUG, NUCLEOS, line_routes, now_parts, nucleo_public, routes_for
from api.db import engine
from api.incidents import query as incidents_query
from api.lines_api import RT_FRESH_SEC, live_trains, scheduled_now_by_route

router = APIRouter(prefix="/api/v1")

RT_META_MAX_AGE = 600        # meta rt_trip_updates_cer más antigua = feed caído
DELAY_MIN_SEC = 900          # 15 min
DELAY_SEVERE_SEC = 1800      # 30 min
TOP_TRAINS = 5
RULE = {
    "delay_min_sec": DELAY_MIN_SEC,
    "delay_severe_sec": DELAY_SEVERE_SEC,
    "min_delayed": 3,
    "min_delayed_pair": 2,
    "min_delayed_share": 0.5,
    "rt_meta_max_age_sec": RT_META_MAX_AGE,
    "row_max_age_sec": RT_FRESH_SEC,
    "top_trains": TOP_TRAINS,
    "signal": "delayed_15 >= min_delayed, o delayed_15 >= min_delayed_pair "
              "y delayed_15 >= min_delayed_share * monitored",
}
SEMANTICS = ("Posible incidencia inferida de retrasos informados en tiempo "
             "real; no es un aviso oficial de Renfe.")


def _is_signal(delayed: int, monitored: int) -> bool:
    return (delayed >= RULE["min_delayed"]
            or (delayed >= RULE["min_delayed_pair"]
                and delayed >= RULE["min_delayed_share"] * monitored))


def _rt_age(now: int) -> int | None:
    """Antigüedad del último ciclo RT de Cercanías (None si no hay registro)."""
    with engine.connect() as c:
        v = c.execute(text(
            "SELECT value FROM meta WHERE key='rt_trip_updates_cer'")).scalar()
    try:
        return now - int(v)
    except (TypeError, ValueError):
        return None


def _family_color(routes: list[dict]) -> str | None:
    """Color de la familia: primero el de rutas de tren, si no el de cualquiera."""
    return (next((r["color"] for r in routes if r["mode"] == "tren" and r["color"]), None)
            or next((r["color"] for r in routes if r["color"]), None))


def _item(key: tuple, trains: list[dict], routes: list[dict], scheduled: int) -> dict:
    nucleo_code, fam_slug = key
    nuc = nucleo_public(nucleo_code)
    fam_code = routes[0]["family_code"]
    delays = [t["delay"] for t in trains if t["delay"] is not None]
    ranked = sorted(trains, key=lambda t: (t["delay"] is None, -(t["delay"] or 0)))
    try:
        # avisos oficiales vigentes de la línea completa (no bloquea si no hay tablas)
        off = sum(1 for a in incidents_query(
            feed="cer", nucleo=nuc["slug"], linea=fam_slug,
            periodo="actuales")["items"] if a.get("relevance") == "line")
    except Exception:
        off = None
    return {
        "nucleo": nuc,
        "line": {"code": fam_code, "slug": fam_slug,
                 "url": f"/lineas/{nuc['slug']}/{fam_slug}",
                 "label": f"{fam_code} · {NUCLEOS[nucleo_code]['name']}",
                 "color": _family_color(routes)},
        "monitored": len(trains),
        "scheduled_now": scheduled,
        "delayed_15": sum(1 for d in delays if d >= DELAY_MIN_SEC),
        "delayed_30": sum(1 for d in delays if d >= DELAY_SEVERE_SEC),
        "max_delay_sec": max(delays) if delays else None,
        "median_delay_sec": round(statistics.median(delays)) if delays else None,
        "official_alerts": off,
        "trains": [{"trip_id": t["trip_id"], "train_number": t["train_number"],
                    "delay_sec": t["delay"], "delay_source": t["delay_source"],
                    "next_stop_name": t["next_stop_name"],
                    "destination": t["destination"]}
                   for t in ranked[:TOP_TRAINS]],
    }


@router.get("/anomalias")
def anomalias(nucleo: str | None = None, linea: str | None = None):
    """Líneas con posible incidencia inferida de retrasos RT (no oficial).

    Solo Cercanías/Rodalies (feed cer) con núcleo verificado en line_route.
    Si el feed RT está desactualizado, devuelve items vacío y rt_fresh=false."""
    code = NUCLEO_BY_SLUG.get(nucleo or "") if nucleo else None
    if nucleo and not code:
        raise HTTPException(404, "núcleo desconocido")
    rids = routes_for(nucleo, linea)
    if linea and not rids:
        raise HTTPException(404, "línea desconocida en ese núcleo" if nucleo
                            else "línea desconocida")
    now, secs, today = now_parts()
    age = _rt_age(now)
    rt_fresh = age is not None and age <= RT_META_MAX_AGE
    base = {"generated_at": now, "rt_fresh": rt_fresh, "rt_age_sec": age,
            "rule": RULE, "semantics": SEMANTICS}
    if not rt_fresh:
        return {**base, "items": []}

    lr = {rid: {**r, "route_id": rid} for (f, rid), r in line_routes().items()
          if f == "cer" and r["nucleo_code"] and r["family_slug"]
          and (rids is None or rid in rids)}
    fam_routes: dict = {}
    for r in lr.values():
        fam_routes.setdefault((r["nucleo_code"], r["family_slug"]), []).append(r)
    with engine.connect() as c:
        sched = scheduled_now_by_route(c, sorted(lr), today, secs)
    groups: dict = {}
    for t in live_trains(feed="cer", min_delay=None, limit=100000):
        r = lr.get(t["route_id"])
        if r:
            groups.setdefault((r["nucleo_code"], r["family_slug"]), []).append(t)

    items = []
    for key, trains in groups.items():
        delayed = sum(1 for t in trains
                      if t["delay"] is not None and t["delay"] >= DELAY_MIN_SEC)
        if not _is_signal(delayed, len(trains)):
            continue
        routes = fam_routes[key]
        scheduled = sum(sched.get(r["route_id"], 0) for r in routes)
        items.append(_item(key, trains, routes, scheduled))
    items.sort(key=lambda i: (-i["delayed_15"], -(i["max_delay_sec"] or 0)))
    return {**base, "items": items}
