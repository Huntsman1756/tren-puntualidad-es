"""API pública de puntualidad ferroviaria Renfe (v1)."""
import ipaddress
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.anomalies import router as anomalies_router
from api.common import line_info, reset_caches
from api.db import engine
from api.incidents import query as incidents_query
from api.incidents import router as incidents_router
from api.lines_api import live_trains
from api.lines_api import router as lines_router
from api.lines_api import station_lines as _station_lines
from api.map_api import router as map_router
from api.push_api import router as push_router

TZ = ZoneInfo("Europe/Madrid")
FEED_LABELS = {"cer": "Cercanías/Rodalies", "ld": "AV · Larga y Media Distancia"}
ALLOWED_ORIGINS = [o.strip() for o in
                   os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(title="Puntualidad Renfe API", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

_stations_cache = {"ts": 0, "rows": []}
_rl: dict[str, list[float]] = {}
RL_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))


def _parse_cidrs(s: str) -> list:
    """'10.0.1.0/24,127.0.0.1' -> redes. Se parsea una vez al importar."""
    return [ipaddress.ip_network(p.strip(), strict=False)
            for p in s.split(",") if p.strip()]


# Proxies cuyo X-Forwarded-For aceptamos / redes internas sin XFF (SSR web)
_TRUSTED = _parse_cidrs(os.environ.get("TRUSTED_PROXIES", "127.0.0.1/32,::1/128"))
_INTERNAL = _parse_cidrs(os.environ.get("INTERNAL_NETWORKS", "127.0.0.1/32,::1/128"))


def _in_nets(ip_s: str, nets: list) -> bool:
    try:
        ip = ipaddress.ip_address(ip_s)
    except ValueError:
        return False
    return any(ip in n for n in nets)


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _rl_key(request: Request) -> str | None:
    """Clave del limitador.

    - XFF solo se respeta si el peer es un proxy de confianza (Traefik):
      se recorre de derecha a izquierda saltando proxies de confianza; la
      primera IP que no lo es, es el cliente real.
    - XFF de un peer no confiable se ignora (no se puede falsificar la clave).
    - Sin XFF y desde red interna: es el SSR de la web (una sola IP para
      todos los usuarios) -> sin límite (None).
    """
    peer = request.client.host if request.client else "?"
    xff = request.headers.get("x-forwarded-for")
    if xff and _in_nets(peer, _TRUSTED):
        for entry in reversed(xff.split(",")):
            cand = entry.strip()
            if _is_ip(cand) and not _in_nets(cand, _TRUSTED):
                return cand
        return peer
    if not xff and _in_nets(peer, _INTERNAL):
        return None
    return peer


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = _rl_key(request)
    if ip is None:
        return await call_next(request)
    now = time.time()
    hits = [t for t in _rl.get(ip, []) if now - t < 60]
    if len(hits) >= RL_LIMIT:
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
    hits.append(now)
    _rl[ip] = hits
    if len(_rl) > 5000:  # límite de memoria del bucket
        _rl.clear()
    return await call_next(request)


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def _stations():
    if time.time() - _stations_cache["ts"] > 300:
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT feed, stop_id, name, lat, lon FROM stops"
            )).mappings().all()
        _stations_cache["rows"] = [dict(r) for r in rows]
        _stations_cache["ts"] = time.time()
    return _stations_cache["rows"]


def _local_midnight(day=None) -> int:
    d = day or datetime.now(TZ).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp())


def _now():
    n = datetime.now(TZ)
    return int(n.timestamp()), n.hour * 3600 + n.minute * 60 + n.second, n.date()


def _parse_date(s: str | None):
    """YYYY-MM-DD -> date. None -> hoy."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date debe ser YYYY-MM-DD") from None


def _parse_time(s: str | None):
    """HH:MM -> segundos desde medianoche local."""
    if not s:
        return None
    try:
        t = datetime.strptime(s, "%H:%M")
        return t.hour * 3600 + t.minute * 60
    except ValueError:
        raise HTTPException(400, "time debe ser HH:MM") from None


def _window(date_s: str | None, time_s: str | None, minutes: int):
    """Resuelve (día_servicio_objetivo, lo_wall_secs, hi_wall_secs, es_hoy).

    lo/hi se expresan en segundos de reloj del día objetivo; el SQL desplaza
    `dep + (sd.day - :day)*86400` para capturar servicios del día anterior
    que cruzan medianoche.
    """
    _, secs_now, today = _now()
    day = _parse_date(date_s) or today
    t0 = _parse_time(time_s)
    if t0 is None:
        t0 = secs_now - 300 if day == today else 0
    return day, t0, t0 + minutes * 60, day == today


def _haversine_m(lat1, lon1, lat2, lon2) -> float | None:
    """Distancia en metros. None si falta alguna coordenada — jamás 0:
    sin coords no se puede justificar una fusión de estaciones."""
    import math
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _freshness():
    # alerts_fetch_*: nuestra descarga del feed (rt_alerts_cer solo cambia
    # cuando Renfe publica avisos nuevos)
    keys = ["rt_trip_updates_cer", "rt_trip_updates_ld", "rt_vehicles_cer",
            "rt_vehicles_ld", "rt_fleet_cer", "rt_alerts_cer",
            "alerts_fetch_ok_cer", "alerts_fetch_err_cer",
            "static_loaded_cer", "static_loaded_ld"]
    with engine.connect() as c:
        rows = c.execute(text("SELECT key,value FROM meta WHERE key = ANY(:k)"),
                         {"k": keys}).all()
    now = int(time.time())
    return {k: {"ts": int(v), "age_sec": now - int(v)} for k, v in rows}


# ---------- health ----------

@app.get("/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    try:
        with engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM stops")).scalar()
        return {"status": "ok" if n else "empty", "stations": n}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)


@app.get("/api/health")
def health():
    return health_ready()


# ---------- v1 ----------

v1 = APIRouter(prefix="/api/v1")


@v1.get("/meta/status")
def meta_status():
    return {"now": int(time.time()), "feeds": _freshness()}


@v1.get("/meta/coverage")
def meta_coverage():
    """Cobertura temporal VÁLIDA por feed: primer y último día consultable
    (racha continua desde hoy con servicio completo) y lista de días
    válidos. Ver _coverage_days."""
    _, _, today = _now()
    return {"today": str(today), "feeds": _coverage_days(),
            "min_share": COVERAGE_MIN_SHARE}


@v1.get("/stations/search")
def stations_search(q: str = Query(min_length=2), limit: int = 15,
                    ccaa: str | None = None, provincia: str | None = None):
    """Búsqueda agrupada: estaciones físicas con mismo nombre en ambas
    redes se devuelven como un único resultado con varias paradas.
    `ccaa`/`provincia` (slug) restringen territorialmente."""
    nq = _norm(q.strip())
    geo = _geo_rows()
    pool = _stations()
    if ccaa or provincia:
        pool = [s for s in pool if _in_territory(geo, s, ccaa, provincia)]
    starts, contains = [], []
    for s in pool:
        nn = _norm(s["name"])
        if nn.startswith(nq):
            starts.append(s)
        elif nq in nn:
            contains.append(s)
    ordered = starts + contains
    # Agrupar por nombre normalizado SOLO si las paradas están a <1,5 km:
    # evita fusionar estaciones homónimas en lugares distintos.
    groups: dict[str, list[dict]] = {}
    out = []
    for s in ordered:
        key = _norm(s["name"])
        merged = False
        for g in groups.get(key, []):
            d = _haversine_m(g["lat"], g["lon"], s["lat"], s["lon"])
            if d is not None and d < 1500:
                g["stops"].append({"feed": s["feed"], "stop_id": s["stop_id"]})
                merged = True
                break
        if merged:
            continue
        g = {"name": s["name"], "lat": s["lat"], "lon": s["lon"],
             "stops": [{"feed": s["feed"], "stop_id": s["stop_id"]}]}
        groups.setdefault(key, []).append(g)
        out.append(g)
    for g in out[:limit]:
        g["networks"] = [FEED_LABELS.get(st["feed"], st["feed"]) for st in g["stops"]]
        gr = geo.get((g["stops"][0]["feed"], g["stops"][0]["stop_id"]))
        if gr:
            g["provincia"] = gr["provincia"]
            g["poblacion"] = gr["poblacion"]
    return out[:limit]


def _parse_stops_param(stops: str):
    pairs = []
    for part in stops.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            f, sid = part.split(":", 1)
        else:
            # sin feed: probar ambos
            pairs += [("cer", part), ("ld", part)]
            continue
        if f not in ("cer", "ld"):
            raise HTTPException(400, f"feed desconocido: {f}")
        pairs.append((f, sid))
    if not pairs:
        raise HTTPException(400, "stops vacío")
    return pairs


def _board(feed: str, stop_id: str, kind: str, lo: int, hi: int,
           day, with_rt: bool, limit: int, only_semidirect: bool = False):
    col = "dep" if kind == "departures" else "arr"
    semi_sql = "AND tf.semidirect=1" if only_semidirect else ""
    # with_rt=False en fechas ≠ hoy: los trip_id de CER se repiten a diario y
    # filtrarían retrasos de hoy a viajes de otra fecha. LD lleva fecha en el id.
    rt_join = "" if not with_rt else """
        LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
        LEFT JOIN rt_stop_update su ON su.feed=t.feed AND su.trip_id=t.trip_id
                                    AND su.stop_id=st.stop_id
        LEFT JOIN rt_vehicle v ON v.feed=t.feed AND v.trip_id=t.trip_id
        LEFT JOIN rt_fleet fl ON fl.feed=t.feed AND fl.trip_id=t.trip_id
    """
    rt_cols = "" if not with_rt else """,
               rt.delay AS trip_delay, su.delay AS stop_delay, su.time AS stop_time,
               rt.sched_rel,
               v.platform AS vp_platform, v.status AS vp_status,
               fl.delay_min AS fleet_delay, fl.platform AS fleet_platform,
               fl.next_platform AS fleet_next_platform, fl.next_eta AS fleet_eta,
               fl.cur_stop_id AS fleet_cur_stop"""
    sql = text(f"""
        SELECT st.{col} AS sched, st.arr, st.dep, st.seq,
               st.{col} + (sd.day - :day) * 86400 AS eff_secs,
               t.trip_id, t.train_number, t.headsign, t.route_id,
               r.short_name AS line, r.long_name AS route_name, r.color,
               tf.semidirect, tf.skipped,
               (SELECT s2.name FROM stop_times x JOIN stops s2
                  ON s2.feed=x.feed AND s2.stop_id=x.stop_id
                 WHERE x.feed=st.feed AND x.trip_id=st.trip_id
                 ORDER BY x.seq DESC LIMIT 1) AS destination,
               (SELECT s3.name FROM stop_times y JOIN stops s3
                  ON s3.feed=y.feed AND s3.stop_id=y.stop_id
                 WHERE y.feed=st.feed AND y.trip_id=st.trip_id
                 ORDER BY y.seq ASC LIMIT 1) AS origin
               {rt_cols}
        FROM stop_times st
        JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
        JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
                          AND sd.day BETWEEN :day - 1 AND :day
        LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
        LEFT JOIN trip_flags tf ON tf.feed=t.feed AND tf.trip_id=t.trip_id
        {rt_join}
        WHERE st.feed=:feed AND st.stop_id=:stop
          AND st.{col} + (sd.day - :day) * 86400 BETWEEN :lo AND :hi
          {semi_sql}
        ORDER BY eff_secs
        LIMIT :lim
    """)
    with engine.connect() as c:
        rows = c.execute(sql, {"feed": feed, "stop": stop_id, "day": day,
                               "lo": lo, "hi": hi, "lim": limit}).mappings().all()
    midnight = _local_midnight(day)
    out = []
    for r in rows:
        # precedencia: observado (flota) > predicción parada > predicción viaje
        delay, source = None, None
        if with_rt:
            if r["fleet_delay"] is not None:
                delay, source = r["fleet_delay"] * 60, "observed"
            elif r["stop_delay"] is not None:
                delay, source = r["stop_delay"], "stop"
            elif r["trip_delay"] is not None:
                delay, source = r["trip_delay"], "trip"
        sched_epoch = midnight + int(r["eff_secs"])
        platform = ((r.get("fleet_next_platform") or r.get("fleet_platform"))
                    or r.get("vp_platform")) if with_rt else None
        out.append({
            "trip_id": r["trip_id"], "feed": feed,
            "line": (r["line"] or "").strip() or None,
            "line_info": line_info(feed, r["route_id"], r["line"]),
            "train_number": r["train_number"],
            "service_date": (day if r["eff_secs"] == r["sched"] else
                             day - timedelta(days=1)).isoformat(),
            "destination": r["destination"], "origin": r["origin"],
            "scheduled": sched_epoch,
            "estimated": sched_epoch + (delay or 0),
            "delay_sec": delay,
            "realtime": source is not None,
            "delay_source": source,
            "cancelled": with_rt and r["sched_rel"] == "CANCELED",
            "platform": platform,
            "vehicle_status": r.get("vp_status") if with_rt else None,
            "semidirect": bool(r["semidirect"]) if r["semidirect"] is not None else False,
            "skipped_stops": r["skipped"] or 0,
        })
    return out


@v1.get("/stations/{feed}/{stop_id}")
def station(feed: str, stop_id: str):
    with engine.connect() as c:
        s = c.execute(text(
            "SELECT name, lat, lon FROM stops WHERE feed=:f AND stop_id=:s"),
            {"f": feed, "s": stop_id}).mappings().first()
    if not s:
        raise HTTPException(404, "station not found")
    g = _geo_rows().get((feed, stop_id))
    return {"feed": feed, "stop_id": stop_id,
            "network": FEED_LABELS.get(feed, feed), **dict(s),
            "lines": _station_lines(feed, stop_id),
            "geo": {
                "poblacion": g["poblacion"],
                "provincia": g["provincia"],
                "provincia_slug": _slug_prov(g["provincia"]),
                "ccaa": g["ccaa"],
                "ccaa_slug": _slug_ccaa(g["ccaa_code"]),
                "source": g["source"],
            } if g and g["cpro"] else None}


def _feed_ts(feeds: set[str]):
    """Timestamps del feed RT por red para frescura real (no hora del fetch)."""
    keys = [f"rt_trip_updates_{f}" for f in feeds] + \
           (["rt_fleet_cer"] if "cer" in feeds else [])
    with engine.connect() as c:
        rows = c.execute(text("SELECT key,value FROM meta WHERE key = ANY(:k)"),
                         {"k": keys}).all()
    return {k: int(v) for k, v in rows}


@v1.get("/stations/board")
def station_board_multi(
        stops: str = Query(description="lista 'feed:stop_id' separada por comas"),
        kind: str = Query("departures", pattern="^(departures|arrivals)$"),
        minutes: int = Query(180, ge=15, le=1440),
        limit: int = Query(60, ge=1, le=200),
        date: str | None = Query(None, description="YYYY-MM-DD (por defecto hoy)"),
        time_s: str | None = Query(None, alias="time", description="HH:MM local"),
        semidirect: bool = Query(False, description="solo servicios que omiten >=2 paradas")):
    """Tablero combinado: admite varias paradas (estación física en ambas redes).
    En fechas ≠ hoy devuelve solo horario programado (nunca retrasos de hoy)."""
    pairs = _parse_stops_param(stops)
    day, lo, hi, is_today = _window(date, time_s, minutes)
    items = []
    for feed, sid in pairs:
        items += _board(feed, sid, kind, lo, hi, day, is_today, limit,
                        only_semidirect=semidirect)
    items.sort(key=lambda i: i["estimated"])
    return {"kind": kind, "items": items[:limit], "date": day.isoformat(),
            "scheduled_only": not is_today,
            "feed_ts": _feed_ts({f for f, _ in pairs})}


@v1.get("/stations/sitemap")
def stations_sitemap(limit: int = Query(600, ge=1, le=2000)):
    """Estaciones con servicio hoy (ordenadas por nº de servicios) para sitemap."""
    _, _, day = _now()
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT st.feed, st.stop_id, count(*) AS n
            FROM stop_times st
            JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
            JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id AND sd.day=:day
            GROUP BY st.feed, st.stop_id
            ORDER BY n DESC LIMIT :lim"""),
            {"day": day, "lim": limit}).all()
    return [{"key": f"{r.feed}:{r.stop_id}", "services": r.n} for r in rows]


@v1.get("/stations/{feed}/{stop_id}/board")
def station_board(feed: str, stop_id: str,
                  kind: str = Query("departures", pattern="^(departures|arrivals)$"),
                  minutes: int = Query(120, ge=15, le=1440),
                  limit: int = Query(60, ge=1, le=200),
                  date: str | None = None,
                  time_s: str | None = Query(None, alias="time"),
                  semidirect: bool = False):
    day, lo, hi, is_today = _window(date, time_s, minutes)
    items = _board(feed, stop_id, kind, lo, hi, day, is_today, limit,
                   only_semidirect=semidirect)
    return {"kind": kind, "items": items, "date": day.isoformat(),
            "scheduled_only": not is_today,
            "feed_ts": _feed_ts({feed})}


COVERAGE_MIN_SHARE = 0.3
FLEET_MIN, FLEET_MAX = -60, 600


def _coverage_days() -> dict:
    """Días con servicio válido por feed (cache 10 min).

    Un día es válido si tiene al menos COVERAGE_MIN_SHARE de los viajes de
    la mediana de los días con servicio: evita ofrecer fechas en las que el
    GTFS solo conserva restos (unos pocos servicios sueltos) y presentarlas
    como "sin trenes"."""
    c0 = getattr(_coverage_days, "c", None)
    if c0 and time.time() - c0[0] < 600:
        return c0[1]
    _, _, today = _now()
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT sd.feed, sd.day, count(*) AS n
            FROM service_days sd
            JOIN trips t ON t.feed=sd.feed AND t.service_id=sd.service_id
            WHERE sd.day >= :t
            GROUP BY sd.feed, sd.day ORDER BY sd.feed, sd.day"""),
            {"t": today}).all()
    by: dict = {}
    for feed, day, n in rows:
        by.setdefault(feed, []).append((day, n))
    out = {}
    for feed, lst in by.items():
        counts = sorted(n for _, n in lst)
        med = counts[len(counts) // 2] if counts else 0
        valid = [d for d, n in lst if n >= COVERAGE_MIN_SHARE * med]
        vs = set(valid)
        last = None
        d = today
        while d in vs:          # racha continua desde hoy
            last = d
            d += timedelta(days=1)
        out[feed] = {"first_day": str(today) if today in vs else None,
                     "last_day": str(last) if last else None,
                     "valid_days": [str(x) for x in valid],
                     "median_trips": med}
    _coverage_days.c = (time.time(), out)
    return out


def _check_coverage(feeds: set[str], day) -> dict | None:
    """None si `day` está cubierto para todos los feeds; si no, detalle."""
    cov = _coverage_days()
    for f in feeds:
        fc = cov.get(f)
        if not fc or str(day) not in fc["valid_days"]:
            return {"feed": f, "first_day": (fc or {}).get("first_day"),
                    "last_day": (fc or {}).get("last_day")}
    return None


def _journey_rows(combos, day, lo, hi, is_today, limit, semidirect):
    midnight = _local_midnight(day)
    rt_join = "" if not is_today else """
            LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
            LEFT JOIN rt_stop_update s1 ON s1.feed=t.feed AND s1.trip_id=t.trip_id AND s1.stop_id=so.stop_id
            LEFT JOIN rt_stop_update s2 ON s2.feed=t.feed AND s2.trip_id=t.trip_id AND s2.stop_id=sd2.stop_id
            LEFT JOIN rt_fleet fl ON fl.feed=t.feed AND fl.trip_id=t.trip_id
    """
    rt_cols = "" if not is_today else """,
                   rt.delay AS trip_delay, s1.delay AS dep_delay, s2.delay AS arr_delay,
                   rt.sched_rel, fl.delay_min AS fleet_delay"""
    items = []
    semi_sql = "AND tf.semidirect=1" if semidirect else ""
    for (ffeed, fstop), (_, tstop) in combos:
        sql = text(f"""
            SELECT so.dep + (sd.day - :day) * 86400 AS dep_eff,
                   sd2.arr + (sd.day - :day) * 86400 AS arr_eff,
                   t.trip_id, t.train_number, t.route_id, r.short_name AS line,
                   sd.day AS service_day,
                   tf.semidirect, tf.skipped{rt_cols}
            FROM stop_times so
            JOIN stop_times sd2 ON sd2.feed=so.feed AND sd2.trip_id=so.trip_id
                               AND sd2.seq>so.seq AND sd2.stop_id=:tstop
            JOIN trips t ON t.feed=so.feed AND t.trip_id=so.trip_id
            JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
                              AND sd.day BETWEEN :day - 1 AND :day
            LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
            LEFT JOIN trip_flags tf ON tf.feed=t.feed AND tf.trip_id=t.trip_id
            {rt_join}
            WHERE so.feed=:feed AND so.stop_id=:fstop
              AND so.dep + (sd.day - :day) * 86400 BETWEEN :lo AND :hi
              {semi_sql}
            ORDER BY dep_eff LIMIT :lim
        """)
        with engine.connect() as c:
            rows = c.execute(sql, {"feed": ffeed, "fstop": fstop, "tstop": tstop,
                                   "day": day, "lo": lo, "hi": hi,
                                   "lim": limit}).mappings().all()
        for r in rows:
            delay = dep_d = arr_d = None
            cancelled = False
            # RT solo hoy: en una ventana de <=24 h un trip_id diario aparece
            # una única vez, así que el RT corresponde a esa instancia
            if is_today:
                fl = r["fleet_delay"]
                if fl is not None and FLEET_MIN <= fl <= FLEET_MAX:
                    delay = fl * 60
                else:
                    delay = r["arr_delay"] if r["arr_delay"] is not None else r["trip_delay"]
                dep_d = r["dep_delay"] if r["dep_delay"] is not None else delay
                arr_d = r["arr_delay"] if r["arr_delay"] is not None else delay
                cancelled = r["sched_rel"] == "CANCELED"
            items.append({
                "trip_id": r["trip_id"], "feed": ffeed,
                "line": (r["line"] or "").strip() or None,
                "line_info": line_info(ffeed, r["route_id"], r["line"]),
                "train_number": r["train_number"],
                "service_date": r["service_day"].isoformat(),
                "dep_scheduled": midnight + int(r["dep_eff"]),
                "arr_scheduled": midnight + int(r["arr_eff"]),
                "dep_estimated": midnight + int(r["dep_eff"]) + (dep_d or 0),
                "arr_estimated": midnight + int(r["arr_eff"]) + (arr_d or 0),
                "delay_sec": delay,
                "realtime": delay is not None,
                "cancelled": cancelled,
                "semidirect": bool(r["semidirect"]) if r["semidirect"] is not None else False,
                "skipped_stops": r["skipped"] or 0,
            })
    items.sort(key=lambda i: i["dep_estimated"])
    return items[:limit]


def _ever_direct(combos) -> bool:
    """¿Existe en el GTFS vigente ALGÚN viaje que pase por origen y luego
    por destino (cualquier día)? Distingue 'sin servicios en la ventana'
    de 'no hay servicio directo: requiere transbordo'."""
    with engine.connect() as c:
        for (feed, fstop), (_, tstop) in combos:
            if c.execute(text("""
                SELECT 1 FROM stop_times so
                JOIN stop_times sd2 ON sd2.feed=so.feed AND sd2.trip_id=so.trip_id
                     AND sd2.seq>so.seq AND sd2.stop_id=:t
                WHERE so.feed=:f AND so.stop_id=:o LIMIT 1"""),
                    {"f": feed, "o": fstop, "t": tstop}).first():
                return True
    return False


@v1.get("/journeys")
def journeys(frm: str = Query(alias="from"), to: str = Query(),
             limit: int = Query(30, ge=1, le=100),
             date: str | None = Query(None, description="YYYY-MM-DD (hoy por defecto)"),
             time_s: str | None = Query(None, alias="time", description="HH:MM local"),
             hours: int = Query(8, ge=1, le=24),
             semidirect: bool = Query(False, description="solo semidirectos (omiten >=2 paradas)"),
             response: Response = None):
    """Trayectos directos origen→destino (lista, compatibilidad v0.2).
    Para estados diferenciados usar /journeys/plan."""
    f_pairs = _parse_stops_param(frm)
    t_pairs = _parse_stops_param(to)
    combos = [(f, t) for f in f_pairs for t in t_pairs if f[0] == t[0]]
    if not combos:
        raise HTTPException(400, "origen y destino deben compartir red (cer|ld)")
    day, lo, hi, is_today = _window(date, time_s, hours * 60)
    items = _journey_rows(combos, day, lo, hi, is_today, limit, semidirect)
    response.headers["X-Date"] = day.isoformat()
    response.headers["X-Scheduled-Only"] = "0" if is_today else "1"
    ts = _feed_ts({f for f, _ in combos})
    response.headers["X-Feed-Ts"] = ",".join(
        k.replace("rt_trip_updates_", "").replace("rt_fleet_", "") + f":{v}"
        for k, v in ts.items())
    return items


@v1.get("/journeys/plan")
def journeys_plan(frm: str = Query(alias="from"), to: str = Query(),
                  limit: int = Query(60, ge=1, le=200),
                  date: str | None = None,
                  time_s: str | None = Query(None, alias="time"),
                  hours: int | None = Query(None, ge=1, le=24),
                  semidirect: bool = False):
    """Planificador de servicios DIRECTOS con estado explícito:

    - ok                 hay trenes directos en la ventana
    - no_direct_window   existen servicios directos en el GTFS, pero no en
                         la fecha/franja consultada
    - out_of_coverage    la fecha no está cubierta por el calendario vigente
                         de la red (no se puede afirmar nada)
    - needs_transfer     ningún tren del GTFS vigente une ambas estaciones
                         sin transbordo; los transbordos NO se calculan
    - different_networks origen y destino no comparten red (CER vs LD):
                         haría falta transbordo entre redes (no soportado)
    Sin `time` en una fecha distinta de hoy se consulta el día completo.
    """
    f_pairs = _parse_stops_param(frm)
    t_pairs = _parse_stops_param(to)
    combos = [(f, t) for f in f_pairs for t in t_pairs if f[0] == t[0]]
    _, secs_now, today = _now()
    day = _parse_date(date) or today
    cov = {f: {k: v for k, v in fc.items() if k != "valid_days"}
           for f, fc in _coverage_days().items()}
    base = {"date": day.isoformat(), "today": today.isoformat(),
            "scheduled_only": day != today, "transfers_supported": False,
            "items": [], "coverage": cov, "time": time_s or None}
    if not combos:
        return {**base, "status": "different_networks",
                "message": "Origen y destino no comparten red (Cercanías vs "
                           "AV/LD): haría falta un transbordo entre redes, "
                           "que este planificador no calcula."}
    if day < today:
        return {**base, "status": "out_of_coverage", "coverage_gap": None,
                "message": "Fecha pasada: el planificador solo consulta hoy y "
                           "fechas futuras."}
    gap = _check_coverage({f for (f, _), _ in combos}, day)
    if gap:
        return {**base, "status": "out_of_coverage", "coverage_gap": gap,
                "message": "La fecha elegida no está cubierta por el horario "
                           "oficial vigente de esta red."}
    t0 = _parse_time(time_s)
    if t0 is None:
        t0 = max(0, secs_now - 300) if day == today else 0
    span = (hours or (24 if (day != today and not time_s) else 8)) * 3600
    items = _journey_rows(combos, day, t0, t0 + span, day == today, limit,
                          semidirect)
    window = {"from_secs": t0, "to_secs": t0 + span}
    if items:
        return {**base, "status": "ok", "items": items, "window": window,
                "feed_ts": _feed_ts({f for (f, _), _ in combos})}
    if _ever_direct(combos):
        return {**base, "status": "no_direct_window", "window": window,
                "message": "Hay trenes directos entre estas estaciones, pero "
                           "ninguno en la fecha y franja consultadas."
                           + (" Prueba sin el filtro de semidirectos."
                              if semidirect else "")}
    return {**base, "status": "needs_transfer", "window": window,
            "message": "Ningún tren del horario vigente une estas estaciones "
                       "sin transbordo. Este planificador solo calcula "
                       "servicios directos: no inventamos conexiones."}


@v1.get("/trains/by-number/{number}")
def trains_by_number(number: str, date: str | None = None):
    """Instancias de un número comercial de tren en un día (hoy por defecto)."""
    num = re.sub(r"\D", "", number)
    if not num:
        raise HTTPException(400, "número inválido")
    today = _now()[2]
    day = _parse_date(date) or today
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT DISTINCT t.feed, t.trip_id, t.train_number, t.route_id,
                   r.short_name AS line,
                   rt.delay, fl.delay_min AS fleet_delay
            FROM trips t
            JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id AND sd.day=:day
            LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
            LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
                AND :today = TRUE
            LEFT JOIN rt_fleet fl ON fl.feed=t.feed AND fl.trip_id=t.trip_id
                AND :today = TRUE
            WHERE t.train_number = :n
            LIMIT 10"""),
            {"day": day, "n": num, "today": day == today}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["line_info"] = line_info(d["feed"], d["route_id"], d["line"])
        d["service_date"] = day.isoformat()
        out.append(d)
    return out


@v1.get("/trains/{feed}/{trip_id}")
def train(feed: str, trip_id: str, date: str | None = Query(
        None, description="día de servicio YYYY-MM-DD (hoy por defecto)")):
    """Ficha de una INSTANCIA de circulación (feed, trip_id, día).

    En CER el trip_id se repite cada día: sin fecha se asume hoy. Con una
    fecha distinta de hoy solo se devuelve horario programado (nunca el
    RT de hoy) y se indica si el viaje circula realmente ese día."""
    _, _, today = _now()
    day = _parse_date(date) or today
    is_today = day == today
    with engine.connect() as c:
        t = c.execute(text("""
            SELECT t.trip_id, t.train_number, t.headsign, t.service_id, t.route_id,
                   r.short_name AS line, r.long_name AS route_name
            FROM trips t LEFT JOIN routes r
              ON r.feed=t.feed AND r.route_id=t.route_id
            WHERE t.feed=:f AND t.trip_id=:t"""),
            {"f": feed, "t": trip_id}).mappings().first()
        if not t:
            raise HTTPException(404, "train not found")
        runs = c.execute(text("""
            SELECT 1 FROM service_days WHERE feed=:f AND service_id=:s AND day=:d"""),
            {"f": feed, "s": t["service_id"], "d": day}).first() is not None
        rt_on = is_today and runs
        rtsel = ("su.delay AS stop_delay, su.time AS stop_time, "
                 "rt.delay AS trip_delay, fl.delay_min AS fleet_delay") if rt_on else (
                 "NULL AS stop_delay, NULL AS stop_time, "
                 "NULL AS trip_delay, NULL AS fleet_delay")
        stops = c.execute(text(f"""
            SELECT st.seq, st.stop_id, s.name, st.arr, st.dep, {rtsel},
                   tf.semidirect, tf.skipped
            FROM stop_times st
            JOIN stops s ON s.feed=st.feed AND s.stop_id=st.stop_id
            LEFT JOIN rt_stop_update su ON su.feed=st.feed AND su.trip_id=st.trip_id
                                        AND su.stop_id=st.stop_id
            LEFT JOIN rt_trip rt ON rt.feed=st.feed AND rt.trip_id=st.trip_id
            LEFT JOIN rt_fleet fl ON fl.feed=st.feed AND fl.trip_id=st.trip_id
            LEFT JOIN trip_flags tf ON tf.feed=st.feed AND tf.trip_id=st.trip_id
            WHERE st.feed=:f AND st.trip_id=:t ORDER BY st.seq"""),
            {"f": feed, "t": trip_id}).mappings().all()
        veh = rt = fl = None
        if rt_on:
            veh = c.execute(text("""
                SELECT lat, lon, status, platform, label, ts FROM rt_vehicle
                WHERE feed=:f AND trip_id=:t"""),
                {"f": feed, "t": trip_id}).mappings().first()
            rt = c.execute(text("""
                SELECT delay, sched_rel, next_stop_id, next_stop_time FROM rt_trip
                WHERE feed=:f AND trip_id=:t"""),
                {"f": feed, "t": trip_id}).mappings().first()
            fl = c.execute(text("""
                SELECT delay_min, cur_stop_id, next_stop_id, next_eta, platform,
                       next_platform, lat, lon, ts FROM rt_fleet
                WHERE feed=:f AND trip_id=:t"""),
                {"f": feed, "t": trip_id}).mappings().first()
            if fl and fl["delay_min"] is not None and not (
                    FLEET_MIN <= fl["delay_min"] <= FLEET_MAX):
                fl = {**fl, "delay_min": None}
    midnight = _local_midnight(day)
    out_stops = []
    for r in stops:
        fld = r["fleet_delay"]
        if fld is not None and not (FLEET_MIN <= fld <= FLEET_MAX):
            fld = None
        if r["stop_delay"] is not None:
            delay, src = r["stop_delay"], "stop"
        elif fld is not None:
            delay, src = fld * 60, "observed"
        elif r["trip_delay"] is not None:
            delay, src = r["trip_delay"], "trip"
        else:
            delay, src = None, None
        base = r["arr"] if r["arr"] is not None else r["dep"]
        out_stops.append({
            "seq": r["seq"], "stop_id": r["stop_id"], "name": r["name"],
            "arr_scheduled": midnight + r["arr"] if r["arr"] is not None else None,
            "dep_scheduled": midnight + r["dep"] if r["dep"] is not None else None,
            "est_time": r["stop_time"]
                or (midnight + base + (delay or 0) if base is not None else None),
            "delay_sec": delay,
            "realtime": r["stop_delay"] is not None,
            "delay_source": src,
        })
    flags = None
    if stops:
        flags = {"semidirect": bool(stops[0]["semidirect"]),
                 "skipped_stops": stops[0]["skipped"] or 0}
    return {"trip": dict(t), "feed": feed,
            "service_date": day.isoformat(), "runs_on_date": runs,
            "scheduled_only": not rt_on,
            "line_info": line_info(feed, t["route_id"], t["line"]),
            "rt": dict(rt) if rt else None,
            "fleet": dict(fl) if fl else None,
            "vehicle": dict(veh) if veh else None,
            "flags": flags,
            "stops": out_stops}


@v1.get("/delays/ranking")
def ranking(limit: int = Query(50, ge=1, le=200), min_delay: int = 60,
            feed: str | None = None,
            ccaa: str | None = None, provincia: str | None = None,
            nucleo: str | None = Query(None, description="slug de núcleo (madrid…)"),
            linea: str | None = Query(None, description="slug de línea o familia (c4, c4a…)")):
    """Ranking EN VIVO de trenes individuales por retraso informado.

    `nucleo`/`linea` usan la identidad canónica de línea (route_id →
    núcleo verificado), nunca la provincia ni el short_name.
    `ccaa`/`provincia` (slug) filtran por la PRÓXIMA PARADA informada por
    RT — semántica local, no de recorrido."""
    cond = ""
    params = {}
    if ccaa or provincia:
        geo = _geo_rows()
        ccaa_code = provincia_name = None
        for g in geo.values():
            if ccaa and _slug_ccaa(g["ccaa_code"]) == ccaa:
                ccaa_code = g["ccaa_code"]
            if provincia and _slug_prov(g["provincia"] or "") == provincia:
                provincia_name = g["provincia"]
            if (ccaa_code or not ccaa) and (provincia_name or not provincia):
                break
        if (ccaa and not ccaa_code) or (provincia and not provincia_name):
            return []  # territorio desconocido -> vacío honesto
        parts = []
        if ccaa_code:
            parts.append("g.ccaa_code = :ccaa_code")
            params["ccaa_code"] = ccaa_code
        if provincia_name:
            parts.append("g.provincia = :prov")
            params["prov"] = provincia_name
        cond = (" AND EXISTS (SELECT 1 FROM geo_station g"
                " WHERE g.feed=rt.feed AND g.stop_id=rt.next_stop_id"
                " AND g.active=1 AND " + " AND ".join(parts) + ")")
    return live_trains(nucleo=nucleo, linea=linea, feed=feed,
                       min_delay=min_delay, limit=limit, ccaa_cond=cond,
                       params=params)


@v1.get("/alerts", deprecated=True)
def alerts(feed: str = "cer", stop_id: str | None = None):
    """Compatibilidad: usa /incidencias. Con stop_id devuelve los avisos de
    la estación y los de línea completa de las líneas que paran en ella,
    marcando `relevance`."""
    res = incidents_query(feed=feed,
                          estacion=f"{feed}:{stop_id}" if stop_id else None,
                          periodo="actuales")
    return [{"id": a["id"], "stop_ids": [e["stop_id"] for e in a["entities"]],
             "updated_at": a["provenance"]["feed_ts"],
             "text": a["description"] or a["header"], "lang": a["language"],
             "effect": a["effect"], "cause": a["cause"],
             "relevance": a.get("relevance"), "scope": a["scope"],
             "lines": [li["label"] for li in a["lines"]],
             "categories": [c["category"] for c in a["categories"]]}
            for a in res["items"]]


@v1.get("/stations/{feed}/{stop_id}/punctuality")
def station_punctuality(feed: str, stop_id: str, days: int = Query(7, ge=1, le=90)):
    """Histórico metodológicamente honesto.

    NO es puntualidad real: es el retraso *informado* por los feeds RT.
    Las métricas se calculan por INSTANCIA DE CIRCULACIÓN
    (feed, trip_id, service_date), no por registro — un tren con muchas
    actualizaciones no pesa más que otro.

    Denominador = circulaciones programadas capturadas (snapshot
    `circulation_stop`, inmutable una vez cerrado el día), acotadas al
    inicio efectivo de la captura tipificada: los días previos a la
    monitorización no cuentan como programados-perdidos. Los registros
    legacy sin service_date se cuentan aparte y no entran en las
    métricas.
    """
    _, _, today = _now()
    with engine.connect() as c:
        # inicio efectivo de captura tipificada del feed (peor cota)
        caps = {r["key"]: int(r["value"]) for r in c.execute(text(
            "SELECT key, value FROM meta WHERE key LIKE :p"),
            {"p": f"capture_start_{feed}_%"}).mappings()}
        cap_start = min(caps.values()) if caps else None
        d1 = today
        d0 = today - timedelta(days=days)
        if cap_start:
            cap_day = datetime.fromtimestamp(cap_start, TZ).date()
            if cap_day > d0:
                d0 = cap_day
        obs = c.execute(text("""
            SELECT o.trip_id, o.service_date, o.delay, o.kind, o.source
            FROM (
                SELECT DISTINCT ON (trip_id, service_date)
                       trip_id, service_date, delay, kind, source, observed_at
                FROM observations
                WHERE feed=:f AND stop_id=:s AND delay IS NOT NULL
                  AND service_date >= :d0 AND service_date <= :d1
                ORDER BY trip_id, service_date, observed_at DESC
            ) o"""),
            {"f": feed, "s": stop_id, "d0": d0, "d1": d1}).mappings().all()
        legacy = c.execute(text("""
            SELECT count(*) FROM observations
            WHERE feed=:f AND stop_id=:s AND service_date IS NULL
              AND observed_at > :since"""),
            {"f": feed, "s": stop_id,
             "since": int(time.time()) - days * 86400}).scalar()
        # instancias programadas = circulaciones capturadas que paran aquí
        scheduled = c.execute(text("""
            SELECT count(*) FROM (
                SELECT DISTINCT trip_id, day
                FROM circulation_stop
                WHERE feed=:f AND stop_id=:s
                  AND day BETWEEN :d0 AND :d1) x"""),
            {"f": feed, "s": stop_id, "d0": d0, "d1": d1}).scalar()
        monitored = c.execute(text("""
            SELECT count(*) FROM sched_capture
            WHERE feed=:f AND day BETWEEN :d0 AND :d1"""),
            {"f": feed, "d0": d0, "d1": d1}).scalar()
    if not obs:
        return {"feed": feed, "stop_id": stop_id, "days": days,
                "window": {"from": str(d0), "to": str(d1),
                           "days_captured": monitored},
                "capture_start": caps or None,
                "circulations_with_rt": 0, "circulations_scheduled": scheduled,
                "legacy_records": legacy,
                "semantics": "reported_delay",
                "note": "sin observaciones tipificadas todavía — el histórico "
                        "se acumula desde el inicio efectivo de la captura"}
    delays = sorted(o["delay"] for o in obs)
    n = len(delays)
    reported = sum(1 for o in obs if o["kind"] == "reported")
    return {
        "feed": feed, "stop_id": stop_id, "days": days,
        "window": {"from": str(d0), "to": str(d1),
                   "days_captured": monitored},
        "capture_start": caps or None,
        "circulations_with_rt": n,
        "circulations_scheduled": scheduled,
        "coverage_pct": round(100 * n / scheduled, 1) if scheduled else None,
        "reported_sources": {"fleet_reported": reported,
                             "trip_update_prediction": n - reported},
        "legacy_records": legacy,
        "semantics": "reported_delay",
        "delay_median_sec": delays[n // 2],
        "delay_p90_sec": delays[min(int(n * 0.9), n - 1)],
        "on_time_2min_pct": round(100 * sum(1 for d in delays if d <= 120) / n, 1),
        "delayed_over_5min_pct": round(100 * sum(1 for d in delays if d > 300) / n, 1),
        "note": "retraso informado por el feed RT — NO llegada efectiva. "
                "Población = instancias de circulación con dato RT.",
    }


@v1.get("/data/status")
def data_status():
    """Estado de frescura y cobertura de las fuentes."""
    fresh = _freshness()
    with engine.connect() as c:
        cov = c.execute(text("""
            SELECT feed, count(*) FROM stops GROUP BY feed""")).all()
        rt = c.execute(text("""
            SELECT feed, count(*) FROM rt_trip
            WHERE updated_at > :cut GROUP BY feed"""),
            {"cut": int(time.time()) - 900}).all()
    return {
        "now": int(time.time()),
        "feeds": fresh,
        "stations_by_feed": {r[0]: r[1] for r in cov},
        "active_rt_trips": {r[0]: r[1] for r in rt},
    }


def _in_territory(geo, stop_row, ccaa_slug, prov_slug):
    g = geo.get((stop_row["feed"], stop_row["stop_id"]))
    if not g:
        return False
    if ccaa_slug and _slug_ccaa(g["ccaa_code"]) != ccaa_slug:
        return False
    return not (prov_slug and _slug_prov(g["provincia"] or "") != prov_slug)


# ---------- territorio (v0.3.1): CCAA/provincia/localidad ----------

def _tslug(s: str) -> str:
    n = _norm(s or "").replace("/", " ").replace(",", " ")
    return re.sub(r"[^a-z0-9]+", "-", n).strip("-")


def _geo_rows():
    """geo_station indexada por (feed, stop_id). Cache 300 s."""
    if not hasattr(_geo_rows, "c"):
        _geo_rows.c = {"ts": 0, "rows": {}}
    if time.time() - _geo_rows.c["ts"] > 300:
        try:
            with engine.connect() as c:
                rows = c.execute(text("""
                    SELECT feed, stop_id, cpro, provincia, ccaa_code, ccaa,
                           poblacion, source, matched_code, dist_m, active
                    FROM geo_station
                """)).mappings().all()
            _geo_rows.c["rows"] = {(r["feed"], r["stop_id"]): dict(r) for r in rows}
            _geo_rows.c["ts"] = time.time()
        except Exception:
            _geo_rows.c["rows"] = {}
    return _geo_rows.c["rows"]


_groups_cache: dict = {"key": None, "groups": []}


def _station_groups() -> list:
    """Agrupación física (mismo nombre normalizado y <1,5 km). Indexada
    por nombre (antes O(n²)) y cacheada mientras no cambie _stations()."""
    rows = _stations()
    if _groups_cache["key"] == _stations_cache["ts"]:
        return _groups_cache["groups"]
    by_name: dict = {}
    groups: list = []
    for s in rows:
        nn = _norm(s["name"])
        placed = False
        for g in by_name.get(nn, []):
            d = _haversine_m(g["lat"], g["lon"], s["lat"], s["lon"])
            if d is not None and d < 1500:
                g["stops"].append((s["feed"], s["stop_id"]))
                placed = True
                break
        if not placed:
            g = {"name": s["name"], "lat": s["lat"], "lon": s["lon"],
                 "stops": [(s["feed"], s["stop_id"])]}
            by_name.setdefault(nn, []).append(g)
            groups.append(g)
    _groups_cache.update(key=_stations_cache["ts"], groups=groups)
    return groups


def _geo_items(ccaa_slug=None, prov_slug=None, q=None):
    """Estaciones físicas con territorio.

    Identidad física = misma regla que la búsqueda: mismo nombre
    normalizado y <1,5 km entre paradas (cubre el caso Vallecas
    cer:70001 / ld:70005 a 139 m). Sin coordenadas nunca se fusiona.
    La adscripción territorial se toma del primer miembro con cpro;
    conflictos entre miembros se ignoran conservadoramente.
    """
    geo = _geo_rows()
    groups = _station_groups()
    items = []
    for g in groups:
        ent = {"name": g["name"], "lat": g["lat"], "lon": g["lon"],
               "feeds": [], "key_parts": g["stops"],
               "cpro": None, "provincia": None, "ccaa_code": None,
               "ccaa": None, "poblacion": None, "source": None}
        for k in g["stops"]:
            ent["feeds"].append(k[0])
            gr = geo.get(k)
            if gr and gr["active"] and gr["cpro"] and not ent["cpro"]:
                ent.update({x: gr[x] for x in
                            ("cpro", "provincia", "ccaa_code", "ccaa",
                             "poblacion", "source")})
        items.append(ent)
    if ccaa_slug:
        items = [i for i in items if _slug_ccaa(i["ccaa_code"]) == ccaa_slug]
    if prov_slug:
        items = [i for i in items
                 if _slug_prov(i["provincia"] or "") == prov_slug]
    if q:
        nq = _norm(q)
        items = [i for i in items
                 if nq in _norm(i["name"] or "")
                 or nq in _norm(i["poblacion"] or "")]
    return sorted(items, key=lambda i: (i["provincia"] or "", i["name"]))


# slugs canónicos por CAUTO INE (estables para URLs públicas)
_CCAA_SLUGS = {
    "01": "andalucia", "02": "aragon", "03": "asturias", "04": "illes-balears",
    "05": "canarias", "06": "cantabria", "07": "castilla-y-leon",
    "08": "castilla-la-mancha", "09": "cataluna", "10": "valencia",
    "11": "extremadura", "12": "galicia", "13": "madrid", "14": "murcia",
    "15": "navarra", "16": "pais-vasco", "17": "la-rioja",
    "18": "ceuta", "19": "melilla",
}


def _slug_ccaa(x):
    """x = ccaa_code ('13') o nombre; devuelve slug canónico."""
    if x in _CCAA_SLUGS:
        return _CCAA_SLUGS[x]
    return _tslug(x or "")


def _slug_prov(name):
    base = _tslug(name)
    return {"coruna-a": "a-coruna", "rioja-la": "la-rioja",
            "palmas-las": "las-palmas",
            "santa-cruz-de-tenerife": "santa-cruz-de-tenerife"}.get(base, base)


@v1.get("/geo/ccaa")
def geo_ccaa():
    """Comunidades autónomas con estaciones clasificadas (conteo por
    stop_id físico, sin duplicar feeds)."""
    items = _geo_items()
    agg: dict = {}
    for i in items:
        if not i["ccaa_code"]:
            continue
        k = i["ccaa_code"]
        a = agg.setdefault(k, {"code": k, "name": i["ccaa"],
                               "slug": _slug_ccaa(k), "stations": 0})
        a["stations"] += 1
    return sorted(agg.values(), key=lambda x: x["name"])


@v1.get("/geo/ccaa/{slug}")
def geo_ccaa_detail(slug: str):
    items = _geo_items(ccaa_slug=slug)
    if not items:
        raise HTTPException(404, "comunidad no encontrada o sin estaciones")
    provs: dict = {}
    for i in items:
        if not i["cpro"]:
            continue
        p = provs.setdefault(i["cpro"], {"cpro": i["cpro"], "name": i["provincia"],
                                         "slug": _slug_prov(i["provincia"] or ""),
                                         "stations": 0})
        p["stations"] += 1
    return {"name": items[0]["ccaa"], "code": items[0]["ccaa_code"],
            "slug": slug,
            "provinces": sorted(provs.values(), key=lambda x: x["name"]),
            "stations": len(items)}


@v1.get("/geo/provincia/{slug}")
def geo_provincia(slug: str, page: int = Query(1, ge=1),
                  size: int = Query(60, ge=1, le=300)):
    items = _geo_items(prov_slug=slug)
    if not items:
        raise HTTPException(404, "provincia no encontrada o sin estaciones")
    return {
        "provincia": items[0]["provincia"], "cpro": items[0]["cpro"],
        "ccaa": items[0]["ccaa"], "ccaa_slug": _slug_ccaa(items[0]["ccaa_code"]),
        "slug": slug,
        "total": len(items),
        "page": page, "size": size,
        "items": items[(page - 1) * size: page * size],
    }


@v1.get("/geo/estaciones")
def geo_estaciones(ccaa: str | None = None, provincia: str | None = None,
                   q: str | None = None,
                   page: int = Query(1, ge=1),
                   size: int = Query(60, ge=1, le=300)):
    items = _geo_items(ccaa_slug=ccaa, prov_slug=provincia, q=q)
    return {"total": len(items), "page": page, "size": size,
            "items": items[(page - 1) * size: page * size]}


@v1.get("/geo/coverage")
def geo_coverage():
    """Informe de cobertura territorial por feed."""
    geo = _geo_rows()
    feeds: dict = {}
    for (feed, _sid), g in geo.items():
        if not g["active"]:
            continue
        f = feeds.setdefault(feed, {"total": 0, "catalogo": 0,
                                    "cartociudad": 0, "geo_inferida": 0,
                                    "catalogo_nombre": 0,
                                    "sin_clasificar": 0, "exterior": 0})
        f["total"] += 1
        src = g["source"] or "sin_clasificar"
        if src == "catalogo" and not g["cpro"]:
            f["exterior"] += 1
        elif src in f:
            f[src] += 1
        else:
            f["sin_clasificar"] += 1
    return feeds


@v1.get("/geo/audit")
def geo_audit(source: str | None = None,
              page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=500)):
    """Detalle de conciliación por parada para auditoría.
    `source` filtra por procedencia (cartociudad, geo_inferida,
    catalogo_nombre, sin_datos, exterior)."""
    geo = _geo_rows()
    rows = [g for g in geo.values() if g["active"]]
    if source:
        rows = [g for g in rows if g["source"] == source or
                (source == "exterior" and g["source"] == "catalogo"
                 and not g["cpro"])]
    out = [{"feed": g["feed"], "stop_id": g["stop_id"],
            "provincia": g["provincia"], "ccaa": g["ccaa"],
            "poblacion": g["poblacion"], "source": g["source"],
            "matched_code": g["matched_code"], "dist_m": g["dist_m"]}
           for g in sorted(rows, key=lambda x: (x["source"], x["feed"],
                                                x["stop_id"]))]
    return {"total": len(out), "page": page, "size": size,
            "items": out[(page - 1) * size: page * size]}


# Estadísticas históricas (v0.4): NO se publican hasta superar su propio
# control de calidad y cobertura. Solo se montan con STATS_PUBLIC=1.
if os.environ.get("STATS_PUBLIC") == "1":
    from api.stats import stats_router
    v1.include_router(stats_router)

app.include_router(v1)
app.include_router(lines_router)
app.include_router(incidents_router)
app.include_router(anomalies_router)
app.include_router(push_router)
app.include_router(map_router)


@app.on_event("startup")
def _ensure_push_tables():
    """Las tablas las crea el collector (create_all); si la API arranca
    antes, se crean aquí con el mismo esquema para que /push nunca falle."""
    try:
        with engine.begin() as c:
            c.execute(text("""
                CREATE TABLE IF NOT EXISTS push_devices (
                    id VARCHAR(36) PRIMARY KEY,
                    endpoint TEXT UNIQUE NOT NULL,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    token_hash VARCHAR(64),
                    created_at TIMESTAMPTZ DEFAULT now(),
                    last_seen_at TIMESTAMPTZ)"""))
            c.execute(text("""
                CREATE TABLE IF NOT EXISTS push_rules (
                    id VARCHAR(36) PRIMARY KEY,
                    device_id VARCHAR(36) NOT NULL,
                    config JSONB NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    last_notify_key TEXT,
                    last_notify_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now())"""))
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_push_rules_device_id"
                           " ON push_rules (device_id)"))
    except Exception:
        pass
    reset_caches()
