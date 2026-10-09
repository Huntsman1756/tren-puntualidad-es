"""Posibles incidencias INFERIDAS de retrasos (episodios persistidos).

No son avisos oficiales de Renfe. El collector evalúa las líneas con datos
RT y guarda episodios con ciclo de vida (observacion -> confirmada ->
resuelta). Este endpoint solo lee esos episodios; no recalcula nada.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from api.common import NUCLEO_BY_SLUG, NUCLEOS, line_routes, now_parts, nucleo_public, routes_for
from api.db import engine
from api.incidents import query as incidents_query
from api.notices import family_slug_of, load_threads

router = APIRouter(prefix="/api/v1")

RT_META_MAX_AGE = 600        # meta rt_trip_updates_cer más antigua = feed caído
RESOLVED_WINDOW_SEC = 6 * 3600    # episodios resueltos mostrados hasta 6 h
EVOLUTION_WINDOW_SEC = 3 * 3600   # evolución: últimas 3 h
EVOLUTION_MAX_POINTS = 60
NOTICE_WINDOW_HOURS = 24          # avisos oficiales abiertos para el cruce
RULE = {
    "min_monitored": 3,
    "min_delayed": 3,
    "min_share": 0.20,
    "min_delayed_pair": 2,
    "pair_min_share": 0.5,
    "confirm_sec": 300,
    "clear_sec": 600,
    "delay_min_sec": 900,
    "delay_severe_sec": 1800,
    "rt_meta_max_age_sec": RT_META_MAX_AGE,
    "resolved_window_sec": RESOLVED_WINDOW_SEC,
    "evolution_window_sec": EVOLUTION_WINDOW_SEC,
    "signal": "monitored >= min_monitored y (delayed_15 >= min_delayed, o "
              "delayed_15 >= min_delayed_pair con share >= pair_min_share)",
}
SEMANTICS = ("Posible incidencia inferida de retrasos informados en tiempo "
             "real; no es un aviso oficial de Renfe.")
STATUS_RANK = {"confirmada": 0, "observacion": 1, "resuelta": 2}


def _rt_age(now: int) -> int | None:
    """Antigüedad del último ciclo RT de Cercanías (None si no hay registro)."""
    with engine.connect() as c:
        v = c.execute(text(
            "SELECT value FROM meta WHERE key='rt_trip_updates_cer'")).scalar()
    try:
        return now - int(v)
    except (TypeError, ValueError):
        return None


def _pct(share) -> float | None:
    return None if share is None else round(float(share) * 100, 1)


def _family_color(routes: list[dict]) -> str | None:
    """Color de la familia: primero el de rutas de tren, si no el de cualquiera."""
    return (next((r["color"] for r in routes if r["mode"] == "tren" and r["color"]), None)
            or next((r["color"] for r in routes if r["color"]), None))


def _line(nuc_code: str, fam_slug: str, routes: list[dict]) -> dict:
    nuc = nucleo_public(nuc_code)
    fam_code = routes[0]["family_code"] if routes else fam_slug.upper()
    name = nuc["name"] if nuc else NUCLEOS.get(nuc_code, {}).get("name", "")
    return {"code": fam_code, "slug": fam_slug,
            "url": f"/lineas/{(nuc or {}).get('slug', '')}/{fam_slug}",
            "label": f"{fam_code} · {name}".rstrip(" ·"),
            "color": _family_color(routes)}


def _thin(points: list[dict], max_points: int) -> list[dict]:
    """Reduce a ≤ max_points conservando primer y último punto."""
    n = len(points)
    if n <= max_points:
        return points
    idx = sorted({round(i * (n - 1) / (max_points - 1)) for i in range(max_points)})
    return [points[i] for i in idx]


def _evolution(samples: list[dict]) -> list[dict]:
    pts = [{"ts": s["ts"], "delayed_15": s["delayed_15"], "monitored": s["monitored"],
            "share_pct": _pct(s["share"]), "max_delay_sec": s["max_delay_sec"],
            "signal": bool(s["signal"])} for s in samples]
    return _thin(pts, EVOLUTION_MAX_POINTS)


def _notice_count(threads: list[dict], nuc_code: str, fam_slug: str) -> int:
    """Hilos oficiales abiertos del núcleo que nombran la familia (o una variante)."""
    return sum(1 for t in threads
               if t["nucleo"] and t["nucleo"]["code"] == nuc_code
               and any(family_slug_of(ln) == fam_slug for ln in t["lines"]))


def _official_alerts(nuc_code: str, fam_slug: str) -> int | None:
    """Avisos oficiales GTFS-RT vigentes de la línea completa (None si falla)."""
    nuc = nucleo_public(nuc_code)
    try:
        return sum(1 for a in incidents_query(
            feed="cer", nucleo=nuc["slug"], linea=fam_slug,
            periodo="actuales")["items"] if a.get("relevance") == "line")
    except Exception:
        return None


def _item(e: dict, routes: list[dict], samples: list[dict], threads: list[dict],
          now: int) -> dict:
    cur = dict(e["last_metrics"] or {})
    cur["share_pct"] = _pct(cur.get("share"))
    end = e["resolved_at"] or now
    fam = e["family_slug"]
    return {
        "id": e["id"],
        "status": e["status"],
        "nucleo": nucleo_public(e["nucleo_code"]),
        "line": _line(e["nucleo_code"], fam, routes),
        "opened_at": e["opened_at"],
        "confirmed_at": e["confirmed_at"],
        "last_signal_at": e["last_signal_at"],
        "resolved_at": e["resolved_at"],
        "rt_gap_since": e["rt_gap_since"],
        "duration_sec": end - e["opened_at"],
        "current": cur,
        "peak": {"delayed_15": e["peak_delayed_15"],
                 "share_pct": _pct(e["peak_share"]),
                 "max_delay_sec": e["peak_max_delay_sec"]},
        "evolution": _evolution(samples),
        "official_alerts": _official_alerts(e["nucleo_code"], fam),
        "official_notices": _notice_count(threads, e["nucleo_code"], fam),
    }


@router.get("/anomalias")
def anomalias(nucleo: str | None = None, linea: str | None = None,
              incluir_resueltas: bool = True):
    """Episodios de posible incidencia inferida (no oficial) por línea.

    - confirmada / observacion: episodios abiertos.
    - resuelta: solo si llegó a confirmarse y se resolvió en las últimas 6 h.
    Si el feed RT no es fresco, rt_fresh=false y rt_gap_since marca el hueco;
    los episodios no se dan por resueltos por falta de datos."""
    code = NUCLEO_BY_SLUG.get(nucleo or "") if nucleo else None
    if nucleo and not code:
        raise HTTPException(404, "núcleo desconocido")
    rids = routes_for(nucleo, linea)
    if linea and not rids:
        raise HTTPException(404, "línea desconocida en ese núcleo" if nucleo
                            else "línea desconocida")
    now, _, _ = now_parts()
    age = _rt_age(now)
    rt_fresh = age is not None and age <= RT_META_MAX_AGE
    base = {"generated_at": now, "rt_fresh": rt_fresh, "rt_age_sec": age,
            "rule": RULE, "semantics": SEMANTICS}

    lr = {rid: r for (f, rid), r in line_routes().items()
          if f == "cer" and r["nucleo_code"] and r["family_slug"]}
    fam_routes: dict = {}
    for r in lr.values():
        fam_routes.setdefault((r["nucleo_code"], r["family_slug"]), []).append(r)
    wanted = None
    if linea:   # familias de la línea pedida (dentro del núcleo si lo hay)
        wanted = {(lr[rid]["nucleo_code"], lr[rid]["family_slug"])
                  for rid in rids if rid in lr}

    where = ["(status <> 'resuelta' OR (confirmed_at IS NOT NULL"
             " AND COALESCE(resolved_at, 0) >= :cut))"]
    params: dict = {"cut": now - RESOLVED_WINDOW_SEC, "since": now - EVOLUTION_WINDOW_SEC}
    if not incluir_resueltas:
        where = ["status <> 'resuelta'"]
    if code:
        where.append("nucleo_code = :n")
        params["n"] = code
    with engine.connect() as c:
        eps = [dict(r) for r in c.execute(text(
            "SELECT * FROM anomaly_episode WHERE " + " AND ".join(where)
            + " ORDER BY id"), params).mappings().all()]
        eps = [e for e in eps if wanted is None
               or (e["nucleo_code"], e["family_slug"]) in wanted]
        samples: dict = {}
        if eps:
            rows = c.execute(text(
                "SELECT episode_id, ts, monitored, delayed_15, share, max_delay_sec,"
                " signal FROM anomaly_sample WHERE episode_id = ANY(:ids)"
                " AND ts >= :since ORDER BY episode_id, ts"),
                {"ids": [e["id"] for e in eps], "since": params["since"]}).mappings()
            for r in rows:
                samples.setdefault(r["episode_id"], []).append(dict(r))
    threads = load_threads("abiertos", NOTICE_WINDOW_HOURS, now=now) if eps else []

    items = [_item(e, fam_routes.get((e["nucleo_code"], e["family_slug"]), []),
                   samples.get(e["id"], []), threads, now) for e in eps]
    items.sort(key=lambda i: (STATUS_RANK.get(i["status"], 9),
                              -(i["current"].get("delayed_15") or 0)))
    return {**base, "items": items}
