"""Sondeo de feeds GTFS-RT (JSON) y persistencia de estado + observaciones.

El parsing está separado de la escritura para poder testearlo offline.
"""
import contextlib
import json
import logging
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import text

from collector.config import FLOTA_URL, RT_ALERTS, RT_TRIP_UPDATES, RT_VEHICLE_POSITIONS
from collector.db import engine, set_meta
from collector.gtfsutil import TZINFO, extract_platform

log = logging.getLogger("collector.rt")

CLIENT = httpx.Client(timeout=20)


def _fetch(url: str):
    r = CLIENT.get(url, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def _ev(s: dict) -> dict:
    return s.get("arrival") or s.get("departure") or {}


def parse_trip_updates(data: dict, feed: str, now: int):
    """Devuelve (trips, stus) deduplicados a partir de un feed GTFS-RT JSON."""
    feed_ts = int(data.get("header", {}).get("timestamp") or now)
    trips, stus = {}, {}
    for e in data.get("entity", []):
        tu = e.get("tripUpdate") or {}
        trip = tu.get("trip") or {}
        tid = (trip.get("tripId") or "").strip()
        if not tid:
            continue
        stu_list = tu.get("stopTimeUpdate") or []
        next_stop = min(
            (s for s in stu_list if _ev(s).get("time")),
            key=lambda s: int(_ev(s)["time"]),
            default=None,
        )
        nev = _ev(next_stop) if next_stop else {}
        trips[tid] = {
            "f": feed, "t": tid,
            "delay": tu.get("delay"),
            "rel": trip.get("scheduleRelationship"),
            "ns": (next_stop or {}).get("stopId"),
            "nst": int(nev.get("time") or 0) or None,
            "nsd": nev.get("delay"),
            "ts": feed_ts,
        }
        for s in stu_list:
            ev = _ev(s)
            sid = (s.get("stopId") or "").strip()
            if not sid:
                continue
            stus[(tid, sid)] = {
                "f": feed, "t": tid, "s": sid,
                "delay": ev.get("delay"),
                "time": int(ev.get("time") or 0) or None,
                "ts": feed_ts,
            }
    return feed_ts, list(trips.values()), list(stus.values())


def parse_vehicle_positions(data: dict, feed: str):
    feed_ts = int(data.get("header", {}).get("timestamp") or time.time())
    rows = {}
    for e in data.get("entity", []):
        v = e.get("vehicle") or {}
        vid = ((v.get("vehicle") or {}).get("id") or e.get("id") or "").strip()
        if not vid:
            continue
        pos = v.get("position") or {}
        label = (v.get("vehicle") or {}).get("label")
        rows[vid] = {
            "f": feed, "vid": vid,
            "t": ((v.get("trip") or {}).get("tripId") or "").strip() or None,
            "label": label, "plat": extract_platform(label),
            "lat": pos.get("latitude"), "lon": pos.get("longitude"),
            "status": v.get("currentStatus"),
            "sid": (v.get("stopId") or "").strip() or None,
            "ts": int(v.get("timestamp") or feed_ts),
        }
    return feed_ts, list(rows.values())


def parse_alerts(data: dict, feed: str):
    feed_ts = int(data.get("header", {}).get("timestamp") or time.time())
    rows = list({
        e.get("id") or str(i): {
            "f": feed, "a": e.get("id") or str(i),
            "p": json.dumps(e.get("alert") or {}, ensure_ascii=False), "ts": feed_ts,
        }
        for i, e in enumerate(data.get("entity", []))
    }.values())
    return feed_ts, rows


def parse_fleet(data: dict):
    try:
        feed_ts = int(datetime.strptime(data.get("fechaActualizacion") or "",
                                        "%Y-%m-%dT%H:%M:%S")
                      .replace(tzinfo=TZINFO).timestamp())
    except ValueError:
        feed_ts = int(time.time())
    rows = {}
    for t in data.get("trenes") or []:
        tid = (t.get("tripId") or "").strip()
        if not tid:
            continue
        raw_delay = (t.get("retrasoMin") or "").strip()
        try:
            delay_min = int(raw_delay) if raw_delay != "" else None
        except ValueError:
            delay_min = None
        eta = None
        if t.get("horaLlegadaSigEst"):
            with contextlib.suppress(ValueError):
                eta = int(datetime.strptime(t["horaLlegadaSigEst"], "%Y-%m-%dT%H:%M:%S")
                          .replace(tzinfo=TZINFO).timestamp())
        rows[tid] = {
            "f": "cer", "t": tid,
            "tn": (t.get("codTren") or "").strip() or None,
            "line": (t.get("codLinea") or "").strip() or None,
            "dm": delay_min,
            "cur": (t.get("codEstAct") or "").strip() or None,
            "ns": (t.get("codEstSig") or "").strip() or None,
            "eta": eta,
            "org": (t.get("codEstOrig") or "").strip() or None,
            "dst": (t.get("codEstDest") or "").strip() or None,
            "lat": t.get("latitud"), "lon": t.get("longitud"),
            "plat": (t.get("via") or "").strip() or None,
            "nplat": (t.get("nextVia") or "").strip() or None,
            "ts": feed_ts,
        }
    return feed_ts, list(rows.values())


def _sched_deps(conn, feed: str, pairs: set) -> dict:
    """dep_secs GTFS por (trip_id, stop_id) para las parejas pedidas."""
    if not pairs:
        return {}
    tids = sorted({t for t, _ in pairs})
    sids = {s for _, s in pairs}
    return {(r[0], r[1]): r[2] for r in conn.execute(text("""
        SELECT trip_id, stop_id, dep FROM stop_times
        WHERE feed=:f AND trip_id = ANY(:t)"""),
        {"f": feed, "t": tids}).all()
        if r[1] in sids and r[2] is not None}


def _service_date(sched_epoch: int | None, dep_secs: int | None) -> str | None:
    """Día de servicio GTFS de una observación en una parada.

    sched_epoch - dep_secs ≈ medianoche del service-day (maneja paradas
    con dep >= 86400 en servicios que cruzan medianoche). None si falta
    el dato programado: se persiste NULL (honesto, no se inventa).
    """
    if sched_epoch is None or dep_secs is None:
        return None
    return (datetime.fromtimestamp(sched_epoch, TZINFO)
            .date() - timedelta(days=dep_secs // 86400)).isoformat()


def poll_trip_updates(feed: str):
    data = _fetch(RT_TRIP_UPDATES[feed])
    now = int(time.time())
    feed_ts, trips, stu_list_all = parse_trip_updates(data, feed, now)

    with engine.begin() as conn:
        if trips:
            conn.execute(text("""
                INSERT INTO rt_trip(feed,trip_id,delay,sched_rel,next_stop_id,next_stop_time,next_stop_delay,updated_at,first_seen)
                VALUES(:f,:t,:delay,:rel,:ns,:nst,:nsd,:ts,:ts)
                ON CONFLICT(feed,trip_id) DO UPDATE SET
                  delay=EXCLUDED.delay, sched_rel=EXCLUDED.sched_rel,
                  next_stop_id=EXCLUDED.next_stop_id, next_stop_time=EXCLUDED.next_stop_time,
                  next_stop_delay=EXCLUDED.next_stop_delay, updated_at=EXCLUDED.updated_at
            """), trips)
        conn.execute(text("DELETE FROM rt_stop_update WHERE feed=:f"), {"f": feed})
        if stu_list_all:
            conn.execute(text("""
                INSERT INTO rt_stop_update(feed,trip_id,stop_id,delay,time,updated_at)
                VALUES(:f,:t,:s,:delay,:time,:ts)
            """), stu_list_all)

        # Observaciones: solo si cambia delay o time respecto a la última
        # registrada para esta instancia (feed,trip_id,service_date,stop_id).
        if stu_list_all:
            deps = _sched_deps(conn, feed,
                               {(s["t"], s["s"]) for s in stu_list_all})
            # epoch programado = time - delay (time es predicho en GTFS-RT)
            for s in stu_list_all:
                d = s["delay"] or 0
                sched = s["time"] - d if s["time"] else None
                s["svc"] = _service_date(sched, deps.get((s["t"], s["s"])))
                s["src"] = "trip_update"
                s["kind"] = "prediction"
            last = {
                (r.trip_id, r.service_date, r.stop_id): (r.delay, r.time)
                for r in conn.execute(text("""
                    SELECT DISTINCT ON (trip_id, service_date, stop_id)
                           trip_id, service_date, stop_id, delay, time
                    FROM observations
                    WHERE feed=:f AND observed_at > :cut
                    ORDER BY trip_id, service_date, stop_id, observed_at DESC
                """), {"f": feed, "cut": now - 86400})
            }
            obs = [{**s, "o": now, "pts": feed_ts} for s in stu_list_all
                   if last.get((s["t"], s["svc"], s["s"])) != (s["delay"], s["time"])]
            if obs:
                conn.execute(text("""
                    INSERT INTO observations(feed,trip_id,service_date,stop_id,
                        delay,time,source,kind,provider_ts,observed_at)
                    VALUES(:f,:t,CAST(:svc AS date),:s,:delay,:time,:src,:kind,:pts,:o)
                """), obs)
        set_meta(conn, f"rt_trip_updates_{feed}", feed_ts)
    return len(trips)


def poll_vehicle_positions(feed: str):
    data = _fetch(RT_VEHICLE_POSITIONS[feed])
    feed_ts, rows = parse_vehicle_positions(data, feed)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM rt_vehicle WHERE feed=:f"), {"f": feed})
        if rows:
            conn.execute(text("""
                INSERT INTO rt_vehicle(feed,vehicle_id,trip_id,label,platform,lat,lon,status,stop_id,ts)
                VALUES(:f,:vid,:t,:label,:plat,:lat,:lon,:status,:sid,:ts)
            """), rows)
        set_meta(conn, f"rt_vehicles_{feed}", feed_ts)
    return len(rows)


def poll_alerts(feed: str, data: dict | None = None, now: int | None = None):
    """Avisos oficiales GTFS-RT.

    Salud de la fuente separada del contenido: `rt_alerts_{feed}` es el
    timestamp del feed (solo cambia cuando Renfe regenera los avisos) y
    `alerts_fetch_ok_{feed}` la última descarga correcta nuestra. Así una
    fuente sana sin avisos (fetch reciente, 0 entidades) no se confunde con
    una fuente caída (fetch fallando). `alerts_seen` guarda el histórico
    (primera/última vez vistos) para poder consultar periodos pasados.
    """
    url = RT_ALERTS.get(feed)
    if not url:
        return 0
    now = int(now if now is not None else time.time())
    if data is None:
        try:
            data = _fetch(url)
        except Exception as e:
            with engine.begin() as conn:
                set_meta(conn, f"alerts_fetch_err_{feed}", now)
                set_meta(conn, f"alerts_fetch_errmsg_{feed}", str(e)[:200])
            raise
    feed_ts, rows = parse_alerts(data, feed)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alerts WHERE feed=:f"), {"f": feed})
        if rows:
            conn.execute(text("""
                INSERT INTO alerts(feed,alert_id,payload,updated_at)
                VALUES(:f,:a,CAST(:p AS jsonb),:ts)
            """), rows)
            conn.execute(text("""
                INSERT INTO alerts_seen(feed,alert_id,payload,first_seen,last_seen)
                VALUES(:f,:a,CAST(:p AS jsonb),:now,:now)
                ON CONFLICT(feed,alert_id) DO UPDATE SET
                  payload=EXCLUDED.payload, last_seen=EXCLUDED.last_seen
            """), [{**r, "now": now} for r in rows])
        set_meta(conn, f"rt_alerts_{feed}", feed_ts)
        set_meta(conn, f"alerts_fetch_ok_{feed}", now)
        set_meta(conn, f"alerts_count_{feed}", len(rows))
    return len(rows)


def poll_fleet():
    """flota.json del visor oficial: retraso informado, posición y vía (CER).

    kind='reported': es el retraso notificado en la parada actual, NO una
    llegada efectiva. Se deduplica por instancia (trip_id, service_date,
    stop_id): solo se guarda si el retraso cambia."""
    data = _fetch(FLOTA_URL)
    feed_ts, rows = parse_fleet(data)
    now = int(time.time())
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM rt_fleet"))
        if rows:
            conn.execute(text("""
                INSERT INTO rt_fleet(feed,trip_id,train_number,line,delay_min,cur_stop_id,
                    next_stop_id,next_eta,origin_stop_id,dest_stop_id,lat,lon,platform,next_platform,ts)
                VALUES(:f,:t,:tn,:line,:dm,:cur,:ns,:eta,:org,:dst,:lat,:lon,:plat,:nplat,:ts)
            """), rows)
        cands = [r for r in rows if r["dm"] is not None and r["cur"]]
        if cands:
            deps = _sched_deps(conn, "cer",
                               {(r["t"], r["cur"]) for r in cands})
            for r in cands:
                # epoch aprox. de la salida programada en la parada actual
                sched = now - r["dm"] * 60
                r["svc"] = _service_date(sched, deps.get((r["t"], r["cur"])))
            last = {
                (r.trip_id, r.service_date, r.stop_id): r.delay
                for r in conn.execute(text("""
                    SELECT DISTINCT ON (trip_id, service_date, stop_id)
                           trip_id, service_date, stop_id, delay
                    FROM observations
                    WHERE feed='cer' AND kind='reported' AND observed_at > :cut
                    ORDER BY trip_id, service_date, stop_id, observed_at DESC
                """), {"cut": now - 86400})
            }
            obs = [{"f": "cer", "t": r["t"], "svc": r["svc"], "s": r["cur"],
                    "delay": r["dm"] * 60, "time": None,
                    "src": "fleet", "kind": "reported",
                    "pts": feed_ts, "o": now}
                   for r in cands
                   if last.get((r["t"], r["svc"], r["cur"])) != r["dm"] * 60]
            if obs:
                conn.execute(text("""
                    INSERT INTO observations(feed,trip_id,service_date,stop_id,
                        delay,time,source,kind,provider_ts,observed_at)
                    VALUES(:f,:t,CAST(:svc AS date),:s,:delay,:time,:src,:kind,:pts,:o)
                """), obs)
        set_meta(conn, "rt_fleet_cer", feed_ts)
    return len(rows)


def prune_history(days=90):
    cut = int(time.time()) - days * 86400
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM observations WHERE observed_at < :c"), {"c": cut})
        conn.execute(text("DELETE FROM alerts_seen WHERE last_seen < :c"), {"c": cut})
        # Viajes en rt_trip que llevan >12h sin actualizarse: limpieza
        conn.execute(text("DELETE FROM rt_trip WHERE updated_at < :c"),
                     {"c": int(time.time()) - 43200})
