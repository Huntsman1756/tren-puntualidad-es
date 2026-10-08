"""API de puntualidad ferroviaria Renfe."""
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .db import engine

TZ = ZoneInfo("Europe/Madrid")
FEED_LABELS = {"cer": "Cercanías/Rodalies", "ld": "AV · Larga y Media Distancia"}

app = FastAPI(title="Puntualidad Renfe API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

_stations_cache = {"ts": 0, "rows": []}


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


@app.get("/api/health")
def health():
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/meta/status")
def meta_status():
    keys = ["rt_trip_updates_cer", "rt_trip_updates_ld", "rt_vehicles_cer",
            "rt_vehicles_ld", "rt_alerts_cer", "static_loaded_cer", "static_loaded_ld"]
    with engine.connect() as c:
        rows = c.execute(text("SELECT key,value FROM meta WHERE key = ANY(:k)"),
                         {"k": keys}).all()
    now = int(time.time())
    out = {k: {"ts": int(v), "age_sec": now - int(v)} for k, v in rows}
    return {"now": now, "feeds": out}


@app.get("/api/stations/search")
def stations_search(q: str = Query(min_length=2), limit: int = 15):
    nq = _norm(q.strip())
    starts, contains = [], []
    for s in _stations():
        nn = _norm(s["name"])
        if nn.startswith(nq):
            starts.append(s)
        elif nq in nn:
            contains.append(s)
    return [
        {"feed": s["feed"], "stop_id": s["stop_id"], "name": s["name"],
         "network": FEED_LABELS.get(s["feed"], s["feed"]),
         "lat": s["lat"], "lon": s["lon"]}
        for s in (starts + contains)[:limit]
    ]


def _board(feed: str, stop_id: str, kind: str, minutes: int, limit: int):
    epoch_now, secs_now, day = _now()
    col = "dep" if kind == "departures" else "arr"
    lo, hi = secs_now - 300, secs_now + minutes * 60
    sql = text(f"""
        SELECT st.{col} AS sched, st.arr, st.dep, st.seq,
               t.trip_id, t.train_number, t.headsign,
               r.short_name AS line, r.long_name AS route_name, r.color,
               rt.delay AS trip_delay, su.delay AS stop_delay, su.time AS stop_time,
               rt.sched_rel,
               v.platform, v.status AS vp_status, v.ts AS vp_ts,
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
        delay = r["stop_delay"] if r["stop_delay"] is not None else r["trip_delay"]
        sched_epoch = midnight + (r["sched"] or 0)
        out.append({
            "trip_id": r["trip_id"], "feed": feed,
            "line": (r["line"] or "").strip() or None,
            "train_number": r["train_number"],
            "destination": r["destination"], "origin": r["origin"],
            "scheduled": sched_epoch,
            "estimated": r["stop_time"] or sched_epoch + (delay or 0),
            "delay_sec": delay,
            "realtime": r["trip_delay"] is not None or r["stop_delay"] is not None,
            "delay_source": ("stop" if r["stop_delay"] is not None
                             else "trip" if r["trip_delay"] is not None else None),
            "cancelled": r["sched_rel"] == "CANCELED",
            "platform": r["platform"],
            "vehicle_status": r["vp_status"],
        })
    return out


@app.get("/api/stations/{feed}/{stop_id}")
def station(feed: str, stop_id: str):
    with engine.connect() as c:
        s = c.execute(text(
            "SELECT name, lat, lon FROM stops WHERE feed=:f AND stop_id=:s"),
            {"f": feed, "s": stop_id}).mappings().first()
    if not s:
        raise HTTPException(404, "station not found")
    return {"feed": feed, "stop_id": stop_id,
            "network": FEED_LABELS.get(feed, feed), **dict(s)}


@app.get("/api/stations/{feed}/{stop_id}/board")
def station_board(feed: str, stop_id: str,
                  kind: str = Query("departures", pattern="^(departures|arrivals)$"),
                  minutes: int = Query(120, ge=15, le=720),
                  limit: int = Query(60, ge=1, le=200)):
    return {"kind": kind, "items": _board(feed, stop_id, kind, minutes, limit)}


@app.get("/api/journeys")
def journeys(frm: str = Query(alias="from"), to: str = Query(), limit: int = 30):
    """from/to como 'feed:stop_id'."""
    try:
        ffeed, fstop = frm.split(":", 1)
        tfeed, tstop = to.split(":", 1)
    except ValueError:
        raise HTTPException(400, "use 'feed:stop_id' en from y to")
    if ffeed != tfeed:
        raise HTTPException(400, "origen y destino deben pertenecer a la misma red (cer|ld)")
    epoch_now, secs_now, day = _now()
    sql = text("""
        SELECT so.dep AS dep, sd2.arr AS arr, t.trip_id, t.train_number,
               r.short_name AS line, rt.delay AS trip_delay,
               s1.delay AS dep_delay, s2.delay AS arr_delay
        FROM stop_times so
        JOIN stop_times sd2 ON sd2.feed=so.feed AND sd2.trip_id=so.trip_id AND sd2.seq>so.seq
        JOIN trips t ON t.feed=so.feed AND t.trip_id=so.trip_id
        JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id AND sd.day=:day
        LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
        LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
        LEFT JOIN rt_stop_update s1 ON s1.feed=t.feed AND s1.trip_id=t.trip_id AND s1.stop_id=so.stop_id
        LEFT JOIN rt_stop_update s2 ON s2.feed=t.feed AND s2.trip_id=t.trip_id AND s2.stop_id=sd2.stop_id
        WHERE so.feed=:feed AND so.stop_id=:fstop AND sd2.stop_id=:tstop
          AND so.dep BETWEEN :lo AND :hi
        ORDER BY so.dep LIMIT :lim
    """)
    with engine.connect() as c:
        rows = c.execute(sql, {"feed": ffeed, "fstop": fstop, "tstop": tstop,
                               "day": day, "lo": secs_now - 300,
                               "hi": secs_now + 8 * 3600, "lim": limit}).mappings().all()
    midnight = _local_midnight(day)
    return [
        {"trip_id": r["trip_id"], "feed": ffeed,
         "line": (r["line"] or "").strip() or None, "train_number": r["train_number"],
         "dep_scheduled": midnight + (r["dep"] or 0),
         "arr_scheduled": midnight + (r["arr"] or 0),
         "dep_estimated": midnight + (r["dep"] or 0)
            + (r["dep_delay"] if r["dep_delay"] is not None else (r["trip_delay"] or 0)),
         "arr_estimated": midnight + (r["arr"] or 0)
            + (r["arr_delay"] if r["arr_delay"] is not None else (r["trip_delay"] or 0)),
         "delay_sec": r["arr_delay"] if r["arr_delay"] is not None else r["trip_delay"],
         "realtime": r["trip_delay"] is not None or r["dep_delay"] is not None}
        for r in rows
    ]


@app.get("/api/trains/{feed}/{trip_id}")
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
                   rt.delay AS trip_delay
            FROM stop_times st
            JOIN stops s ON s.feed=st.feed AND s.stop_id=st.stop_id
            LEFT JOIN rt_stop_update su ON su.feed=st.feed AND su.trip_id=st.trip_id
                                        AND su.stop_id=st.stop_id
            LEFT JOIN rt_trip rt ON rt.feed=st.feed AND rt.trip_id=st.trip_id
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
    _, _, today = _now()
    midnight = _local_midnight(today)
    out_stops = []
    for r in stops:
        delay = r["stop_delay"] if r["stop_delay"] is not None else r["trip_delay"]
        out_stops.append({
            "seq": r["seq"], "stop_id": r["stop_id"], "name": r["name"],
            "arr_scheduled": midnight + r["arr"] if r["arr"] is not None else None,
            "dep_scheduled": midnight + r["dep"] if r["dep"] is not None else None,
            "est_time": r["stop_time"]
                or (midnight + r["arr"] + (delay or 0) if r["arr"] is not None else None),
            "delay_sec": delay, "realtime": r["stop_delay"] is not None,
        })
    return {"trip": dict(t), "feed": feed, "rt": dict(rt) if rt else None,
            "vehicle": dict(veh) if veh else None, "stops": out_stops}


@app.get("/api/delays/ranking")
def ranking(limit: int = Query(50, ge=1, le=200), min_delay: int = 60):
    sql = text("""
        SELECT rt.feed, rt.trip_id, rt.delay, rt.next_stop_id, rt.next_stop_time,
               t.train_number, r.short_name AS line, s.name AS next_stop_name,
               (SELECT s2.name FROM stop_times x JOIN stops s2
                  ON s2.feed=x.feed AND s2.stop_id=x.stop_id
                 WHERE x.feed=rt.feed AND x.trip_id=rt.trip_id
                 ORDER BY x.seq DESC LIMIT 1) AS destination
        FROM rt_trip rt
        LEFT JOIN trips t ON t.feed=rt.feed AND t.trip_id=rt.trip_id
        LEFT JOIN routes r ON r.feed=rt.feed AND r.route_id=t.route_id
        LEFT JOIN stops s ON s.feed=rt.feed AND s.stop_id=rt.next_stop_id
        WHERE rt.delay >= :mind AND rt.next_stop_time > :now
        ORDER BY rt.delay DESC LIMIT :lim
    """)
    with engine.connect() as c:
        rows = c.execute(sql, {"mind": min_delay, "now": int(time.time()),
                               "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/alerts")
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
