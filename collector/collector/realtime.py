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


# Ventana de retraso plausible (segundos): -60 … +600 min, la misma que la
# API usa para retrasos de flota. Fuera de ella el dato se guarda crudo pero
# no ancla la fecha de servicio ni genera observaciones.
DELAY_MIN_SEC = -3600
DELAY_MAX_SEC = 36000


def _retraso_plausible(sec) -> bool:
    """None (sin dato) cuenta como plausible: no hay nada que descartar."""
    return sec is None or DELAY_MIN_SEC <= sec <= DELAY_MAX_SEC


def _svc_futura(svc, now: int) -> bool:
    """True si la fecha de servicio (ISO) es posterior al día local de `now`.
    Un tren observado ahora no puede pertenecer a un día de servicio futuro;
    los servicios de madrugada pertenecen al día ANTERIOR, nunca al siguiente."""
    return svc is not None and svc > datetime.fromtimestamp(now, TZINFO).date().isoformat()


def _descartar_futuras(obs: list[dict], now: int, feed: str) -> list[dict]:
    """Quita observaciones con service_date futura (defensa en profundidad)."""
    keep = [o for o in obs if not _svc_futura(o["svc"], now)]
    if len(keep) != len(obs):
        log.info("%s: %d observaciones con fecha de servicio futura descartadas",
                 feed, len(obs) - len(keep))
    return keep


def _midnight(day) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=TZINFO).timestamp())


# Resolución de instancia: el día candidato debe quedar a menos de
# SVC_TOL_SEC de la hora programada, y ganar al segundo candidato por
# al menos SVC_MARGIN_SEC (los trips diarios se repiten cada 86400 s:
# cerca del punto medio NO se infiere una fecha — se persiste NULL).
SVC_TOL_SEC = 4 * 3600
SVC_MARGIN_SEC = 90 * 60


def _pick_day(scored: list, tol: int, margin: int):
    """scored: [(error_secs, date)] por día candidato. Devuelve el día
    ganador en ISO, o None si no hay candidato <= tol o el segundo
    candidato queda a menos de `margin` del primero (ambiguo)."""
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    if scored[0][0] > tol:
        return None
    if len(scored) > 1 and scored[1][0] - scored[0][0] < margin:
        return None
    return scored[0][1].isoformat()


def resolve_service_dates(conn, feed: str, items: list[dict],
                          tol: int = SVC_TOL_SEC,
                          margin: int = SVC_MARGIN_SEC):
    """Rellena item["svc"] = 'YYYY-MM-DD' (o None) para cada item.

    item = {"t": trip_id, "a": [(stop_id, approx_epoch, pref), ...]}
    donde approx_epoch es la mejor estimación de la hora PROGRAMADA del
    evento en esa parada (p.ej. time - delay para trip_updates, o
    provider_ts - delay para flota) y pref ∈ {'dep', 'arr', 'any'} elige
    la columna GTFS a comparar.

    Un día de servicio d es candidato si TODAS las anclas tienen horario
    en el viaje y el peor error |medianoche(d)+secs - approx| <= tol.
    Si hay >=2 candidatos y el segundo no queda al menos `margin` peor
    que el primero, el día es ambiguo -> svc=None (no se infiere).
    """
    for i in items:
        i["svc"] = None
    items = [i for i in items if i.get("a")]
    if not items:
        return
    tids = sorted({i["t"] for i in items})
    pairs = {(i["t"], s) for i in items for s, _, _ in i["a"]}
    svc_of = dict(conn.execute(text(
        "SELECT trip_id, service_id FROM trips"
        " WHERE feed=:f AND trip_id = ANY(:t)"),
        {"f": feed, "t": tids}).all())
    if not svc_of:
        return
    approx = [a for i in items for _, a, _ in i["a"]]
    lo = (datetime.fromtimestamp(min(approx), TZINFO).date()
          - timedelta(days=2))
    hi = (datetime.fromtimestamp(max(approx), TZINFO).date()
          + timedelta(days=2))
    days_of: dict[str, list] = {}
    for sid, day in conn.execute(text(
            "SELECT service_id, day FROM service_days WHERE feed=:f"
            " AND service_id = ANY(:s) AND day BETWEEN :lo AND :hi"),
            {"f": feed, "s": sorted(set(svc_of.values())),
             "lo": lo, "hi": hi}):
        days_of.setdefault(sid, []).append(day)
    secs: dict = {}
    stop_ids = sorted({s for _, s in pairs})
    for t, s, dep, arr in conn.execute(text(
            "SELECT trip_id, stop_id, dep, arr FROM stop_times"
            " WHERE feed=:f AND trip_id = ANY(:t) AND stop_id = ANY(:s)"),
            {"f": feed, "t": tids, "s": stop_ids}):
        if (t, s) in pairs:
            e = secs.setdefault((t, s), {"dep": [], "arr": []})
            if dep is not None:
                e["dep"].append(dep)
            if arr is not None:
                e["arr"].append(arr)
    midnights: dict = {}
    for i in items:
        days = days_of.get(svc_of.get(i["t"])) or []
        scored = []
        for d in days:
            base = midnights.get(d)
            if base is None:
                base = midnights[d] = _midnight(d)
            worst, ok = 0, True
            for s, apx, pref in i["a"]:
                ent = secs.get((i["t"], s))
                if not ent:
                    ok = False
                    break
                vals = (ent["dep"] if pref == "dep"
                        else ent["arr"] if pref == "arr" else []) \
                    or ent["dep"] or ent["arr"]
                if not vals:
                    ok = False
                    break
                worst = max(worst, min(abs(base + v - apx) for v in vals))
            if ok:
                scored.append((worst, d))
        i["svc"] = _pick_day(scored, tol, margin)


def _mark_capture(conn, feed: str, source: str, ts: int):
    """Fija el inicio efectivo de la captura tipificada por (feed, fuente).
    Solo el primer valor escrito cuenta: es la cota inferior honesta de
    cualquier ventana de cobertura."""
    conn.execute(text(
        "INSERT INTO meta(key,value) VALUES(:k,:v) ON CONFLICT DO NOTHING"),
        {"k": f"capture_start_{feed}_{source}", "v": str(ts)})


def record_capture(conn, feed: str, source: str, ts: int):
    """Latido de captura por (feed, fuente, día local): nº de sondeos, primer
    y último sondeo y mayor hueco entre sondeos consecutivos del mismo día.
    Un solo upsert; los huecos entre días no cuentan (cada día es su fila)."""
    day = datetime.fromtimestamp(ts, TZINFO).date()
    conn.execute(text("""
        INSERT INTO capture_health(feed,source,day,polls,first_ts,last_ts,max_gap_sec)
        VALUES(:f,:s,:d,1,:ts,:ts,0)
        ON CONFLICT(feed,source,day) DO UPDATE SET
          polls=capture_health.polls+1,
          max_gap_sec=GREATEST(capture_health.max_gap_sec,
                               :ts-capture_health.last_ts),
          first_ts=LEAST(capture_health.first_ts,EXCLUDED.first_ts),
          last_ts=EXCLUDED.last_ts
    """), {"f": feed, "s": source, "d": day, "ts": ts})


def poll_trip_updates(feed: str, data: dict | None = None,
                      now: int | None = None):
    if data is None:
        data = _fetch(RT_TRIP_UPDATES[feed])
    now = int(now if now is not None else time.time())
    feed_ts, trips, stu_list_all = parse_trip_updates(data, feed, now)

    with engine.begin() as conn:
        _mark_capture(conn, feed, "trip_update", now)
        record_capture(conn, feed, "trip_update", now)
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
            # Retrasos absurdos (fuera de ventana): ni ancla ni observación.
            ok_stu = [s for s in stu_list_all if _retraso_plausible(s["delay"])]
            n_bad = len(stu_list_all) - len(ok_stu)
            if n_bad:
                log.info("trip_updates %s: %d retrasos implausibles ignorados",
                         feed, n_bad)
            # La instancia se resuelve contra GTFS+calendario: el evento
            # programado aproximado es time - delay (time es la predicción
            # del feed). Sin candidato inequívoco -> svc queda NULL.
            items = []
            for s in ok_stu:
                s["src"], s["kind"], s["svc"] = "trip_update", "prediction", None
                if s["time"]:
                    # arr/dep indistinguibles a nivel de día de servicio
                    items.append({"t": s["t"], "_s": s,
                                  "a": [(s["s"], s["time"] - (s["delay"] or 0),
                                         "any")]})
            resolve_service_dates(conn, feed, items)
            for it in items:
                it["_s"]["svc"] = it["svc"]
            # clave de dedup: svc se resuelve como str ISO; la BD devuelve
            # date -> normalizar a str para que la comparación muerda
            last = {
                (r.trip_id,
                 str(r.service_date) if r.service_date else None,
                 r.stop_id): (r.delay, r.time)
                for r in conn.execute(text("""
                    SELECT DISTINCT ON (trip_id, service_date, stop_id)
                           trip_id, service_date, stop_id, delay, time
                    FROM observations
                    WHERE feed=:f AND observed_at > :cut
                    ORDER BY trip_id, service_date, stop_id, observed_at DESC
                """), {"f": feed, "cut": now - 86400})
            }
            obs = [{**s, "o": now, "pts": feed_ts} for s in ok_stu
                   if last.get((s["t"], s["svc"], s["s"])) != (s["delay"], s["time"])]
            obs = _descartar_futuras(obs, now, feed)
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


def poll_fleet(data: dict | None = None, now: int | None = None):
    """flota.json del visor oficial: retraso informado, posición y vía (CER).

    kind='reported': es el retraso notificado en la parada actual, NO una
    llegada efectiva. Se deduplica por instancia (trip_id, service_date,
    stop_id): solo se guarda si el retraso cambia.

    El día de servicio se resuelve contra GTFS+calendario con dos anclas:
    la salida programada en la parada actual (feed_ts - retraso) y la ETA
    a la siguiente parada. Si no hay un único día inequívoco, svc=None:
    no se infiere una fecha de servicio ambigua."""
    if data is None:
        data = _fetch(FLOTA_URL)
    feed_ts, rows = parse_fleet(data)
    now = int(now if now is not None else time.time())
    with engine.begin() as conn:
        _mark_capture(conn, "cer", "fleet", now)
        record_capture(conn, "cer", "fleet", now)
        conn.execute(text("DELETE FROM rt_fleet"))
        if rows:
            conn.execute(text("""
                INSERT INTO rt_fleet(feed,trip_id,train_number,line,delay_min,cur_stop_id,
                    next_stop_id,next_eta,origin_stop_id,dest_stop_id,lat,lon,platform,next_platform,ts)
                VALUES(:f,:t,:tn,:line,:dm,:cur,:ns,:eta,:org,:dst,:lat,:lon,:plat,:nplat,:ts)
            """), rows)
        # Retrasos absurdos (p. ej. -1438 min por cruce de medianoche): se
        # guardan en rt_fleet pero no anclan fecha ni generan observaciones.
        n_bad = sum(1 for r in rows
                    if r["dm"] is not None and not _retraso_plausible(r["dm"] * 60))
        if n_bad:
            log.info("flota: %d retrasos implausibles ignorados", n_bad)
        cands = [r for r in rows if r["dm"] is not None and r["cur"]
                 and _retraso_plausible(r["dm"] * 60)]
        if cands:
            items = []
            for r in cands:
                anchors = [(r["cur"], feed_ts - r["dm"] * 60, "dep")]
                if r["ns"] and r["eta"]:
                    anchors.append((r["ns"], r["eta"] - r["dm"] * 60, "arr"))
                r["_it"] = {"t": r["t"], "a": anchors}
                items.append(r["_it"])
            resolve_service_dates(conn, "cer", items)
            for r in cands:
                r["svc"] = r.pop("_it")["svc"]
            last = {
                (r.trip_id,
                 str(r.service_date) if r.service_date else None,
                 r.stop_id): r.delay
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
            obs = _descartar_futuras(obs, now, "flota")
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
    cut_day = datetime.fromtimestamp(cut, TZINFO).date()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM observations WHERE observed_at < :c"), {"c": cut})
        conn.execute(text("DELETE FROM alerts_seen WHERE last_seen < :c"), {"c": cut})
        # versiones mínimas de programación histórica, mismo horizonte
        conn.execute(text("DELETE FROM circulation_stop WHERE day < :d"),
                     {"d": cut_day})
        conn.execute(text("DELETE FROM circulation WHERE day < :d"),
                     {"d": cut_day})
        conn.execute(text("DELETE FROM sched_capture WHERE day < :d"),
                     {"d": cut_day})
        conn.execute(text("DELETE FROM capture_health WHERE day < :d"),
                     {"d": cut_day})
        # Viajes en rt_trip que llevan >12h sin actualizarse: limpieza
        conn.execute(text("DELETE FROM rt_trip WHERE updated_at < :c"),
                     {"c": int(time.time()) - 43200})
