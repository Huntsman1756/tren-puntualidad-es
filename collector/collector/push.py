"""Evaluación y envío de notificaciones Web Push.

Reglas:
- Solo se notifica con dato en tiempo real (observado o estimado); nunca por
  horario programado sin confirmar.
- Solo dentro de la ventana/días configurados por la suscripción.
- Dedup por (suscripción, tren, fecha) + re-notificación si el retraso sube
  un escalón de 5 min; límite de frecuencia por min_interval_min.
- Endpoint muerto (404/410) -> borrado inmediato.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from collector.config import (
    VAPID_PRIVATE_KEY,
    VAPID_PUBLIC_KEY,
    VAPID_SUBJECT,
)
from collector.db import engine
from collector.gtfsutil import TZINFO
from collector.lines import NUCLEOS

log = logging.getLogger("push")

STEP_MIN = 5  # escalón de retraso que re-notifica


def _hm(s: str | None, default: str) -> int:
    try:
        h, m = (s or default).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 0


def in_window(cfg: dict, now: datetime) -> bool:
    """cfg.days usa 0=domingo (convención JS Date.getDay())."""
    if cfg.get("days"):
        js_today = (now.weekday() + 1) % 7
        if js_today not in cfg["days"]:
            return False
    mins = now.hour * 60 + now.minute
    return _hm(cfg.get("from_time"), "00:00") <= mins <= _hm(
        cfg.get("to_time"), "23:59")


def line_label(r) -> str | None:
    """'C4 · Madrid' si el núcleo es verificable; si no, el short_name."""
    code = r.get("line_code") or (r.get("line") or "").strip() or None
    nuc = NUCLEOS.get(r.get("nucleo_code") or "")
    if code and nuc:
        return f"{code} · {nuc['name']}"
    return code


def next_trains_journey(conn, from_key: str, to_key: str, horizon_min: int) -> list:
    """Próximos trenes directos hoy con su retraso estimado (fuente real)."""
    f_pairs = [tuple(p.split(":")) for p in from_key.split(",")]
    t_pairs = [tuple(p.split(":")) for p in to_key.split(",")]
    now = datetime.now(TZINFO)
    today = now.date()
    now_secs = int(now.hour * 3600 + now.minute * 60 + now.second)
    items = []
    for (ff, fs), (tf, ts) in ((f, t) for f in f_pairs for t in t_pairs):
        if ff != tf:
            continue
        rows = conn.execute(text("""
            SELECT t.trip_id, t.train_number, r.short_name AS line,
                   lr.line_code, lr.nucleo_code,
                   so.dep AS dep_secs,
                   rt.delay AS trip_delay, s1.delay AS dep_delay,
                   fl.delay_min AS fleet_delay
            FROM stop_times so
            JOIN stop_times sd2 ON sd2.feed=so.feed AND sd2.trip_id=so.trip_id
                 AND sd2.seq>so.seq AND sd2.stop_id=:ts
            JOIN trips t ON t.feed=so.feed AND t.trip_id=so.trip_id
            JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
                 AND sd.day=:day
            LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
            LEFT JOIN line_route lr ON lr.feed=t.feed AND lr.route_id=t.route_id
            LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
            LEFT JOIN rt_stop_update s1 ON s1.feed=t.feed AND s1.trip_id=t.trip_id
                 AND s1.stop_id=so.stop_id
            LEFT JOIN rt_fleet fl ON fl.feed=t.feed AND fl.trip_id=t.trip_id
            WHERE so.feed=:ff AND so.stop_id=:fs
              AND so.dep BETWEEN :lo AND :hi
            ORDER BY so.dep LIMIT 12
        """), {"ff": ff, "fs": fs, "ts": ts, "day": today,
               "lo": now_secs, "hi": now_secs + horizon_min * 60}
        ).mappings().all()
        for r in rows:
            delay = r["fleet_delay"] * 60 if r["fleet_delay"] is not None else (
                r["dep_delay"] if r["dep_delay"] is not None else r["trip_delay"])
            items.append({
                "trip_id": r["trip_id"], "train": r["train_number"],
                "line": line_label(r), "dep_secs": r["dep_secs"],
                "delay_sec": delay, "realtime": delay is not None,
            })
    return sorted(items, key=lambda i: i["dep_secs"])


def next_trains_station(conn, station_key: str, horizon_min: int) -> list:
    now = datetime.now(TZINFO)
    today = now.date()
    now_secs = int(now.hour * 3600 + now.minute * 60 + now.second)
    items = []
    for p in station_key.split(","):
        feed, stop = p.split(":")
        rows = conn.execute(text("""
            SELECT t.trip_id, t.train_number, t.headsign AS dest,
                   r.short_name AS line, lr.line_code, lr.nucleo_code,
                   st.dep AS dep_secs,
                   s.delay AS st_delay, rt.delay AS trip_delay,
                   fl.delay_min AS fleet_delay
            FROM stop_times st
            JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
            JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
                 AND sd.day=:day
            LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
            LEFT JOIN line_route lr ON lr.feed=t.feed AND lr.route_id=t.route_id
            LEFT JOIN rt_stop_update s ON s.feed=t.feed AND s.trip_id=t.trip_id
                 AND s.stop_id=st.stop_id
            LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
            LEFT JOIN rt_fleet fl ON fl.feed=t.feed AND fl.trip_id=t.trip_id
            WHERE st.feed=:feed AND st.stop_id=:stop AND st.dep IS NOT NULL
              AND st.dep BETWEEN :lo AND :hi
            ORDER BY st.dep LIMIT 12
        """), {"feed": feed, "stop": stop, "day": today,
               "lo": now_secs, "hi": now_secs + horizon_min * 60}
        ).mappings().all()
        for r in rows:
            delay = r["fleet_delay"] * 60 if r["fleet_delay"] is not None else (
                r["st_delay"] if r["st_delay"] is not None else r["trip_delay"])
            items.append({
                "trip_id": r["trip_id"], "train": r["train_number"],
                "line": line_label(r), "dep_secs": r["dep_secs"],
                "dest": r["dest"], "delay_sec": delay,
                "realtime": delay is not None,
            })
    return sorted(items, key=lambda i: i["dep_secs"])


def pick_alert(sub: dict, now: datetime) -> dict | None:
    """Devuelve {key,title,body,url} si hay algo que notificar, o None.
    `sub` lleva 'conn' inyectada para las consultas."""
    cfg = sub["config"]
    threshold = int(cfg.get("threshold_min", 5))
    now_secs = now.hour * 3600 + now.minute * 60 + now.second
    today = now.date().isoformat()

    if cfg.get("type") == "journey" and cfg.get("from_key") and cfg.get("to_key"):
        trains = next_trains_journey(
            sub["conn"], cfg["from_key"], cfg["to_key"], horizon_min=90)
        url = f"/trayecto?from={cfg['from_key']}&to={cfg['to_key']}"
    elif cfg.get("station_key"):
        trains = next_trains_station(
            sub["conn"], cfg["station_key"], horizon_min=90)
        url = f"/estacion/{cfg['station_key']}"
    else:
        return None

    for tr in trains:
        if not tr["realtime"] or tr["delay_sec"] is None:
            continue  # nunca notificar sobre dato no confirmado
        if not (now_secs < tr["dep_secs"] <= now_secs + 90 * 60):
            continue
        delay_min = tr["delay_sec"] / 60
        if delay_min < threshold:
            continue
        key = f"{tr['trip_id']}:{today}:{int(delay_min // STEP_MIN)}"
        if key == sub["last_notify_key"]:
            continue
        hh = int(tr["dep_secs"]) // 3600 % 24
        mm = int(tr["dep_secs"]) // 60 % 60
        label = tr["line"] or tr["train"] or "Tren"
        dest = f" → {tr['dest']}" if tr.get("dest") else ""
        return {
            "key": key,
            "title": f"{label} {tr['train'] or ''} · +{int(delay_min)} min",
            "body": (f"Sale a las {hh:02d}:{mm:02d}{dest} "
                     f"con {int(delay_min)} min de retraso."),
            "url": url,
        }
    return None


def send_push(endpoint: str, p256dh: str, auth: str, payload: dict) -> str:
    """Devuelve 'ok' | 'gone' | 'error'."""
    try:
        from pywebpush import webpush
        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth},
            },
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
            timeout=15,
        )
        return "ok"
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            return "gone"
        log.warning("push send failed: %s", e)
        return "error"


def run_push_cycle() -> int:
    """Evalúa cada REGLA por separado (un dispositivo puede tener varias).

    Dedup y límite de frecuencia son por regla: avisar de un trayecto no
    silencia la regla de otro trayecto o estación del mismo navegador.
    Endpoint muerto (404/410) -> se borra el dispositivo y sus reglas."""
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return 0
    now = datetime.now(TZINFO)
    sent = 0
    gone: set[str] = set()
    with engine.begin() as conn:
        rules = conn.execute(text("""
            SELECT r.id, r.device_id, r.config, r.last_notify_key,
                   r.last_notify_at, d.endpoint, d.p256dh, d.auth
            FROM push_rules r JOIN push_devices d ON d.id = r.device_id
            WHERE r.enabled = 1
            ORDER BY r.created_at""")).mappings().all()
        for s in rules:
            if s["device_id"] in gone:
                continue
            cfg = s["config"]
            if not in_window(cfg, now):
                continue
            min_int = int(cfg.get("min_interval_min", 30))
            if (s["last_notify_at"]
                    and now - s["last_notify_at"].astimezone(TZINFO)
                    < timedelta(minutes=min_int)):
                continue
            row = dict(s)
            row["conn"] = conn
            alert = pick_alert(row, now)
            if not alert:
                continue
            alert["tag"] = f"rule-{s['id']}"
            alert["rule_id"] = s["id"]
            res = send_push(s["endpoint"], s["p256dh"], s["auth"], alert)
            if res == "ok":
                conn.execute(text(
                    "UPDATE push_rules SET last_notify_key=:k, last_notify_at=now()"
                    " WHERE id=:id"), {"k": alert["key"], "id": s["id"]})
                sent += 1
            elif res == "gone":
                gone.add(s["device_id"])
                conn.execute(text("DELETE FROM push_rules WHERE device_id=:d"),
                             {"d": s["device_id"]})
                conn.execute(text("DELETE FROM push_devices WHERE id=:d"),
                             {"d": s["device_id"]})
    return sent
