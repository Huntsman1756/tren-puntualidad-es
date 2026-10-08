"""API pública de puntualidad ferroviaria Renfe (v1)."""
import os
import re
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.db import engine

TZ = ZoneInfo("Europe/Madrid")
FEED_LABELS = {"cer": "Cercanías/Rodalies", "ld": "AV · Larga y Media Distancia"}
ALLOWED_ORIGINS = [o.strip() for o in
                   os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(title="Puntualidad Renfe API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)

_stations_cache = {"ts": 0, "rows": []}
_rl: dict[str, list[float]] = {}
RL_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else "?"
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


def _freshness():
    keys = ["rt_trip_updates_cer", "rt_trip_updates_ld", "rt_vehicles_cer",
            "rt_vehicles_ld", "rt_fleet_cer", "rt_alerts_cer",
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


@v1.get("/stations/search")
def stations_search(q: str = Query(min_length=2), limit: int = 15):
    """Búsqueda agrupada: estaciones físicas con mismo nombre en ambas
    redes se devuelven como un único resultado con varias paradas."""
    nq = _norm(q.strip())
    starts, contains = [], []
    for s in _stations():
        nn = _norm(s["name"])
        if nn.startswith(nq):
            starts.append(s)
        elif nq in nn:
            contains.append(s)
    ordered = starts + contains
    groups: dict[str, dict] = {}
    out = []
    for s in ordered:
        key = _norm(s["name"])
        if key in groups:
            groups[key]["stops"].append({"feed": s["feed"], "stop_id": s["stop_id"]})
            continue
        g = {"name": s["name"], "lat": s["lat"], "lon": s["lon"],
             "stops": [{"feed": s["feed"], "stop_id": s["stop_id"]}]}
        groups[key] = g
        out.append(g)
    for g in out[:limit]:
        g["networks"] = [FEED_LABELS.get(st["feed"], st["feed"]) for st in g["stops"]]
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


def _board(feed: str, stop_id: str, kind: str, minutes: int, limit: int):
    _, secs_now, day = _now()
    col = "dep" if kind == "departures" else "arr"
    lo, hi = secs_now - 300, secs_now + minutes * 60
    sql = text(f"""
        SELECT st.{col} AS sched, st.arr, st.dep, st.seq,
               t.trip_id, t.train_number, t.headsign,
               r.short_name AS line, r.long_name AS route_name, r.color,
               rt.delay AS trip_delay, su.delay AS stop_delay, su.time AS stop_time,
               rt.sched_rel,
               v.platform AS vp_platform, v.status AS vp_status,
               fl.delay_min AS fleet_delay, fl.platform AS fleet_platform,
               fl.next_platform AS fleet_next_platform, fl.next_eta AS fleet_eta,
               fl.cur_stop_id AS fleet_cur_stop,
               (SELECT s2.name FROM stop_times x JOIN stops s2
                  ON s2.feed=x.feed AND s2.stop_id=x.stop_id
                 WHERE x.feed=st.feed AND x.trip_id=st.trip_id
                 ORDER BY x.seq DESC LIMIT 1) AS destination,
               (SELECT s3.name FROM stop_times y JOIN stops s3
                  ON s3.feed=y.feed AND s3.stop_id=y.stop_id
                 WHERE y.feed=st.feed AND y.trip_id=st.trip_id
                 ORDER BY y.seq ASC LIMIT 1) AS origin
        FROM stop_times st
        JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
        JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id AND sd.day=:day
        LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
        LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
        LEFT JOIN rt_stop_update su ON su.feed=t.feed AND su.trip_id=t.trip_id
                                    AND su.stop_id=st.stop_id
        LEFT JOIN rt_vehicle v ON v.feed=t.feed AND v.trip_id=t.trip_id
        LEFT JOIN rt_fleet fl ON fl.feed=t.feed AND fl.trip_id=t.trip_id
        WHERE st.feed=:feed AND st.stop_id=:stop
          AND st.{col} BETWEEN :lo AND :hi
        ORDER BY st.{col}
        LIMIT :lim
    """)
    with engine.connect() as c:
        rows = c.execute(sql, {"feed": feed, "stop": stop_id, "day": day,
                               "lo": lo, "hi": hi, "lim": limit}).mappings().all()
    midnight = _local_midnight(day)
    out = []
    for r in rows:
        # precedencia: observado (flota) > predicción parada > predicción viaje
        if r["fleet_delay"] is not None:
            delay, source = r["fleet_delay"] * 60, "observed"
        elif r["stop_delay"] is not None:
            delay, source = r["stop_delay"], "stop"
        else:
            delay, source = r["trip_delay"], ("trip" if r["trip_delay"] is not None else None)
        sched_epoch = midnight + (r["sched"] or 0)
        platform = r["fleet_next_platform"] or r["fleet_platform"] or r["vp_platform"]
        out.append({
            "trip_id": r["trip_id"], "feed": feed,
            "line": (r["line"] or "").strip() or None,
            "train_number": r["train_number"],
            "destination": r["destination"], "origin": r["origin"],
            "scheduled": sched_epoch,
            "estimated": sched_epoch + (delay or 0),
            "delay_sec": delay,
            "realtime": source is not None,
            "delay_source": source,
            "cancelled": r["sched_rel"] == "CANCELED",
            "platform": platform,
            "vehicle_status": r["vp_status"],
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
    return {"feed": feed, "stop_id": stop_id,
            "network": FEED_LABELS.get(feed, feed), **dict(s)}


@v1.get("/stations/board")
def station_board_multi(
        stops: str = Query(description="lista 'feed:stop_id' separada por comas"),
        kind: str = Query("departures", pattern="^(departures|arrivals)$"),
        minutes: int = Query(180, ge=15, le=720),
        limit: int = Query(60, ge=1, le=200)):
    """Tablero combinado: admite varias paradas (estación física en ambas redes)."""
    items = []
    for feed, sid in _parse_stops_param(stops):
        items += _board(feed, sid, kind, minutes, limit)
    items.sort(key=lambda i: i["estimated"])
    return {"kind": kind, "items": items[:limit]}


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
                  minutes: int = Query(120, ge=15, le=720),
                  limit: int = Query(60, ge=1, le=200)):
    return {"kind": kind, "items": _board(feed, stop_id, kind, minutes, limit)}


@v1.get("/journeys")
def journeys(frm: str = Query(alias="from"), to: str = Query(), limit: int = 30):
    """from/to como 'feed:stop_id' o stop_id suelto (se prueban ambas redes
    si comparten feed)."""
    f_pairs = _parse_stops_param(frm)
    t_pairs = _parse_stops_param(to)
    combos = [(f, t) for f in f_pairs for t in t_pairs if f[0] == t[0]]
    if not combos:
        raise HTTPException(400, "origen y destino deben compartir red (cer|ld)")
    _, secs_now, day = _now()
    midnight = _local_midnight(day)
    items = []
    for (ffeed, fstop), (_, tstop) in combos:
        sql = text("""
            SELECT so.dep AS dep, sd2.arr AS arr, t.trip_id, t.train_number,
                   r.short_name AS line, rt.delay AS trip_delay,
                   s1.delay AS dep_delay, s2.delay AS arr_delay,
                   fl.delay_min AS fleet_delay
            FROM stop_times so
            JOIN stop_times sd2 ON sd2.feed=so.feed AND sd2.trip_id=so.trip_id AND sd2.seq>so.seq
            JOIN trips t ON t.feed=so.feed AND t.trip_id=so.trip_id
            JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id AND sd.day=:day
            LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
            LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
            LEFT JOIN rt_stop_update s1 ON s1.feed=t.feed AND s1.trip_id=t.trip_id AND s1.stop_id=so.stop_id
            LEFT JOIN rt_stop_update s2 ON s2.feed=t.feed AND s2.trip_id=t.trip_id AND s2.stop_id=sd2.stop_id
            LEFT JOIN rt_fleet fl ON fl.feed=t.feed AND fl.trip_id=t.trip_id
            WHERE so.feed=:feed AND so.stop_id=:fstop AND sd2.stop_id=:tstop
              AND so.dep BETWEEN :lo AND :hi
            ORDER BY so.dep LIMIT :lim
        """)
        with engine.connect() as c:
            rows = c.execute(sql, {"feed": ffeed, "fstop": fstop, "tstop": tstop,
                                   "day": day, "lo": secs_now - 300,
                                   "hi": secs_now + 8 * 3600, "lim": limit}).mappings().all()
        for r in rows:
            if r["fleet_delay"] is not None:
                delay = r["fleet_delay"] * 60
            else:
                delay = r["arr_delay"] if r["arr_delay"] is not None else r["trip_delay"]
            dep_d = r["dep_delay"] if r["dep_delay"] is not None else delay
            arr_d = r["arr_delay"] if r["arr_delay"] is not None else delay
            items.append({
                "trip_id": r["trip_id"], "feed": ffeed,
                "line": (r["line"] or "").strip() or None,
                "train_number": r["train_number"],
                "dep_scheduled": midnight + (r["dep"] or 0),
                "arr_scheduled": midnight + (r["arr"] or 0),
                "dep_estimated": midnight + (r["dep"] or 0) + (dep_d or 0),
                "arr_estimated": midnight + (r["arr"] or 0) + (arr_d or 0),
                "delay_sec": delay,
                "realtime": delay is not None,
            })
    items.sort(key=lambda i: i["dep_estimated"])
    return items[:limit]


@v1.get("/trains/by-number/{number}")
def trains_by_number(number: str):
    """Instancias de hoy para un número comercial de tren (p.ej. 06180)."""
    num = re.sub(r"\D", "", number)
    if not num:
        raise HTTPException(400, "número inválido")
    _, _, day = _now()
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT t.feed, t.trip_id, t.train_number, r.short_name AS line,
                   rt.delay, fl.delay_min AS fleet_delay
            FROM trips t
            JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id AND sd.day=:day
            LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
            LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
            LEFT JOIN rt_fleet fl ON fl.feed=t.feed AND fl.trip_id=t.trip_id
            WHERE t.train_number = :n
            LIMIT 10"""), {"day": day, "n": num}).mappings().all()
    return [dict(r) for r in rows]


@v1.get("/trains/{feed}/{trip_id}")
def train(feed: str, trip_id: str):
    with engine.connect() as c:
        t = c.execute(text("""
            SELECT t.trip_id, t.train_number, t.headsign, t.service_id,
                   r.short_name AS line, r.long_name AS route_name
            FROM trips t LEFT JOIN routes r
              ON r.feed=t.feed AND r.route_id=t.route_id
            WHERE t.feed=:f AND t.trip_id=:t"""),
            {"f": feed, "t": trip_id}).mappings().first()
        if not t:
            raise HTTPException(404, "train not found")
        stops = c.execute(text("""
            SELECT st.seq, st.stop_id, s.name, st.arr, st.dep,
                   su.delay AS stop_delay, su.time AS stop_time,
                   rt.delay AS trip_delay, fl.delay_min AS fleet_delay
            FROM stop_times st
            JOIN stops s ON s.feed=st.feed AND s.stop_id=st.stop_id
            LEFT JOIN rt_stop_update su ON su.feed=st.feed AND su.trip_id=st.trip_id
                                        AND su.stop_id=st.stop_id
            LEFT JOIN rt_trip rt ON rt.feed=st.feed AND rt.trip_id=st.trip_id
            LEFT JOIN rt_fleet fl ON fl.feed=st.feed AND fl.trip_id=st.trip_id
            WHERE st.feed=:f AND st.trip_id=:t ORDER BY st.seq"""),
            {"f": feed, "t": trip_id}).mappings().all()
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
    _, _, today = _now()
    midnight = _local_midnight(today)
    out_stops = []
    for r in stops:
        if r["fleet_delay"] is not None:
            delay = r["fleet_delay"] * 60
        elif r["stop_delay"] is not None:
            delay = r["stop_delay"]
        else:
            delay = r["trip_delay"]
        base = r["arr"] if r["arr"] is not None else r["dep"]
        out_stops.append({
            "seq": r["seq"], "stop_id": r["stop_id"], "name": r["name"],
            "arr_scheduled": midnight + r["arr"] if r["arr"] is not None else None,
            "dep_scheduled": midnight + r["dep"] if r["dep"] is not None else None,
            "est_time": r["stop_time"]
                or (midnight + base + (delay or 0) if base is not None else None),
            "delay_sec": delay, "realtime": r["stop_delay"] is not None,
        })
    return {"trip": dict(t), "feed": feed,
            "rt": dict(rt) if rt else None,
            "fleet": dict(fl) if fl else None,
            "vehicle": dict(veh) if veh else None,
            "stops": out_stops}


@v1.get("/delays/ranking")
def ranking(limit: int = Query(50, ge=1, le=200), min_delay: int = 60,
            feed: str | None = None):
    sql = text("""
        SELECT rt.feed, rt.trip_id,
               GREATEST(rt.delay, COALESCE(fl.delay_min,0)*60) AS delay,
               CASE WHEN fl.delay_min IS NOT NULL
                         AND fl.delay_min*60 >= rt.delay THEN 'observed'
                    ELSE 'predicted' END AS delay_source,
               rt.next_stop_id, rt.next_stop_time,
               t.train_number, r.short_name AS line, s.name AS next_stop_name,
               (SELECT s2.name FROM stop_times x JOIN stops s2
                  ON s2.feed=x.feed AND s2.stop_id=x.stop_id
                 WHERE x.feed=rt.feed AND x.trip_id=rt.trip_id
                 ORDER BY x.seq DESC LIMIT 1) AS destination
        FROM rt_trip rt
        LEFT JOIN trips t ON t.feed=rt.feed AND t.trip_id=rt.trip_id
        LEFT JOIN routes r ON r.feed=rt.feed AND r.route_id=t.route_id
        LEFT JOIN stops s ON s.feed=rt.feed AND s.stop_id=rt.next_stop_id
        LEFT JOIN rt_fleet fl ON fl.feed=rt.feed AND fl.trip_id=rt.trip_id
        WHERE GREATEST(rt.delay, COALESCE(fl.delay_min,0)*60) >= :mind
          AND rt.next_stop_time > :now
          AND (CAST(:feed AS text) IS NULL OR rt.feed = :feed)
        ORDER BY delay DESC LIMIT :lim
    """)
    with engine.connect() as c:
        rows = c.execute(sql, {"mind": min_delay, "now": int(time.time()),
                               "feed": feed, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


@v1.get("/alerts")
def alerts(feed: str = "cer", stop_id: str | None = None):
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT alert_id, payload, updated_at FROM alerts WHERE feed=:f"),
            {"f": feed}).all()
        station_routes = set()
        if stop_id:
            station_routes = {r[0] for r in c.execute(text("""
                SELECT DISTINCT t.route_id FROM trips t
                JOIN stop_times st ON st.feed=t.feed AND st.trip_id=t.trip_id
                WHERE st.feed=:f AND st.stop_id=:s"""),
                {"f": feed, "s": stop_id})}
    out = []
    for aid, p, ts in rows:
        informed = p.get("informedEntity", [])
        ents = [(ie.get("stopId") or "") for ie in informed]
        rts = {(ie.get("routeId") or "") for ie in informed}
        if stop_id and stop_id not in ents and not (rts & station_routes):
            continue
        texts = p.get("descriptionText") or p.get("headerText") or {}
        trans = texts.get("translation") or [{}]
        out.append({"id": aid, "stop_ids": ents, "updated_at": ts,
                    "text": trans[0].get("text"), "lang": trans[0].get("language"),
                    "effect": p.get("effect"), "cause": p.get("cause")})
    return out


@v1.get("/stations/{feed}/{stop_id}/punctuality")
def station_punctuality(feed: str, stop_id: str, days: int = Query(7, ge=1, le=90)):
    """Histórico desde observations (retrasos observados/notificados).
    coverage = viajes con observación / viajes programados que paran aquí."""
    now = int(time.time())
    since = now - days * 86400
    with engine.connect() as c:
        obs = c.execute(text("""
            SELECT o.trip_id, o.delay, o.observed_at
            FROM observations o
            WHERE o.feed=:f AND o.stop_id=:s AND o.observed_at > :since
        """), {"f": feed, "s": stop_id, "since": since}).mappings().all()
        scheduled = c.execute(text("""
            SELECT count(DISTINCT st.trip_id) FROM stop_times st
            JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
            JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
            WHERE st.feed=:f AND st.stop_id=:s
              AND sd.day >= CURRENT_DATE - CAST(:days AS int) * INTERVAL '1 day'
              AND sd.day <= CURRENT_DATE"""),
            {"f": feed, "s": stop_id, "days": days}).scalar()
    if not obs:
        return {"feed": feed, "stop_id": stop_id, "days": days,
                "coverage_observed": 0, "coverage_scheduled": scheduled,
                "note": "sin observaciones todavía — el histórico se acumula desde el primer despliegue"}
    delays = [o["delay"] for o in obs if o["delay"] is not None]
    uniq_trips = {o["trip_id"] for o in obs}
    delays.sort()
    n = len(delays)
    return {
        "feed": feed, "stop_id": stop_id, "days": days,
        "observations": n, "trips_observed": len(uniq_trips),
        "coverage_scheduled": scheduled,
        "coverage_observed_pct": round(100 * len(uniq_trips) / scheduled, 1) if scheduled else None,
        "delay_avg_sec": round(sum(delays) / n),
        "delay_median_sec": delays[n // 2],
        "delay_p90_sec": delays[int(n * 0.9)],
        "on_time_2min_pct": round(100 * sum(1 for d in delays if d <= 120) / n, 1),
        "delayed_over_5min_pct": round(100 * sum(1 for d in delays if d > 300) / n, 1),
        "note": "retrasos observados por el visor oficial; población = observaciones, no servicios completos",
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


app.include_router(v1)
