"""Motor de anomalías: posibles incidencias INFERIDAS de retrasos RT.

No son avisos oficiales de Renfe. Por línea (núcleo + familia) se calcula una
señal con los trenes de RT fresco; la señal debe persistir para confirmar un
episodio (models.AnomalyEpisode). Ciclo: evaluate() o run_anomaly_cycle().

La regla de retraso efectivo replica api/lines_api.py (EFF_DELAY_SQL): el
contenedor del collector no tiene el paquete api.
"""
import json
import logging
import statistics
import time
from datetime import datetime

from sqlalchemy import text

from collector.db import engine, get_meta
from collector.gtfsutil import TZINFO

log = logging.getLogger("collector.anomalies")

RULE = {
    "min_monitored": 3,        # trenes con RT fresco en la línea
    "min_delayed": 3,          # ... con retraso >= delay_min_sec y share >= min_share
    "min_share": 0.20,
    "min_delayed_pair": 2,     # ... o bien 2 retrasados con share >= 50 %
    "min_share_pair": 0.5,
    "delay_min_sec": 900,      # 15 min
    "delay_severe_sec": 1800,  # 30 min
    "fleet_min": -60,          # flota: minutos válidos (fuera = sin dato)
    "fleet_max": 600,
    "delay_plausible_min": -3600,   # segundos
    "delay_plausible_max": 36000,
    "row_max_age_sec": 900,    # rt_trip.updated_at más antiguo = no fresco
    "top_trains": 5,
}
ANOMALY_CONFIRM_SEC = 300      # señal persistente >= 5 min...
ANOMALY_CONFIRM_EVALS = 3      # ... y >= 3 evaluaciones con señal
ANOMALY_CLEAR_SEC = 600        # ventana de despeje: muestras frescas sin señal
ANOMALY_CLEAR_MIN_SAMPLES = 5
ANOMALY_CLEAR_TOL_SEC = 90    # tolerancia al inicio de la ventana
RT_FRESH_MAX_AGE = 600         # meta rt_trip_updates_cer más antigua = no fresco
SAMPLE_KEEP_SEC = 7 * 86400
EPISODE_KEEP_SEC = 30 * 86400
OPEN_STATES = "('observacion','confirmada')"
ZERO_METRICS = {"monitored": 0, "scheduled_now": 0, "delayed_15": 0,
                "delayed_30": 0, "share": 0.0, "max_delay_sec": 0,
                "median_delay_sec": 0}


def effective_delay(rt_delay, fleet_min):
    """Retraso efectivo en segundos: flota si es plausible, si no predicción RT.
    Fuera de [-3600, 36000] s devuelve None (sin dato, no cuenta como retraso)."""
    if fleet_min is not None and RULE["fleet_min"] <= fleet_min <= RULE["fleet_max"]:
        d = fleet_min * 60
    else:
        d = rt_delay
    if d is None or not (RULE["delay_plausible_min"] <= d <= RULE["delay_plausible_max"]):
        return None
    return d


def is_candidate(m: dict) -> bool:
    """Regla de señal: >=3 monitorizados y (>=3 retrasados con share>=20 %
    o >=2 retrasados con share>=50 %)."""
    if m["monitored"] < RULE["min_monitored"]:
        return False
    d, s = m["delayed_15"], m["share"]
    return ((d >= RULE["min_delayed"] and s >= RULE["min_share"])
            or (d >= RULE["min_delayed_pair"] and s >= RULE["min_share_pair"]))


def _scheduled_now(conn, route_ids, now: int) -> dict:
    """Circulaciones que el horario deberían tener en marcha ahora, por route_id.
    Misma SQL que api.lines_api.scheduled_now_by_route (día actual y anterior+24h)."""
    if not route_ids:
        return {}
    dt = datetime.fromtimestamp(now, TZINFO)
    day = dt.date()
    secs = dt.hour * 3600 + dt.minute * 60 + dt.second
    rows = conn.execute(text("""
        SELECT t.route_id, count(*) FROM trip_span ts
        JOIN trips t ON t.feed=ts.feed AND t.trip_id=ts.trip_id
        JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
             AND sd.day BETWEEN CAST(:day AS date) - 1 AND CAST(:day AS date)
        WHERE ts.feed='cer' AND t.route_id = ANY(:r)
          AND ts.first_dep + (sd.day - CAST(:day AS date)) * 86400 <= :s
          AND ts.last_arr + (sd.day - CAST(:day AS date)) * 86400 >= :s
        GROUP BY t.route_id"""),
        {"r": sorted(route_ids), "day": day, "s": secs}).all()
    return {rid: n for rid, n in rows}


def line_metrics(conn, now) -> dict:
    """{(nucleo_code, family_slug): métricas} para líneas Cercanías con al
    menos un tren monitorizado. Solo trenes con RT fresco y próxima parada futura."""
    now = int(now)
    fam_of = {r.route_id: (r.nucleo_code, r.family_slug) for r in conn.execute(text("""
        SELECT route_id, nucleo_code, family_slug FROM line_route
        WHERE feed='cer' AND nucleo_code IS NOT NULL AND family_slug IS NOT NULL"""))}
    if not fam_of:
        return {}
    trains = conn.execute(text("""
        SELECT rt.trip_id, t.route_id, t.train_number, rt.delay,
               fl.delay_min AS fleet_min, s.name AS next_stop_name
        FROM rt_trip rt
        JOIN trips t ON t.feed=rt.feed AND t.trip_id=rt.trip_id
        LEFT JOIN rt_fleet fl ON fl.feed=rt.feed AND fl.trip_id=rt.trip_id
        LEFT JOIN stops s ON s.feed=rt.feed AND s.stop_id=rt.next_stop_id
        WHERE rt.feed='cer' AND rt.updated_at > :fresh
          AND rt.next_stop_time > :now AND t.route_id = ANY(:r)"""),
        {"fresh": now - RULE["row_max_age_sec"], "now": now,
         "r": sorted(fam_of)}).mappings().all()
    groups: dict = {}
    for t in trains:
        key = fam_of.get(t["route_id"])
        if key is None:
            continue
        d = effective_delay(t["delay"], t["fleet_min"])
        groups.setdefault(key, []).append((t, d))
    sched_route = _scheduled_now(conn, {t["route_id"] for t in trains}, now)
    sched: dict = {}
    for rid, n in sched_route.items():
        sched[fam_of[rid]] = sched.get(fam_of[rid], 0) + n
    out = {}
    for key, items in groups.items():
        delays = [d for _, d in items if d is not None]
        ranked = sorted(items, key=lambda x: (x[1] is None, -(x[1] or 0)))
        monitored = len(items)
        d15 = sum(1 for d in delays if d >= RULE["delay_min_sec"])
        out[key] = {
            "monitored": monitored,
            "scheduled_now": sched.get(key, 0),
            "delayed_15": d15,
            "delayed_30": sum(1 for d in delays if d >= RULE["delay_severe_sec"]),
            "share": d15 / monitored,
            "max_delay_sec": max(delays) if delays else None,
            "median_delay_sec": round(statistics.median(delays)) if delays else None,
            "trains": [{"trip_id": t["trip_id"], "train_number": t["train_number"],
                        "delay_sec": d, "next_stop_name": t["next_stop_name"]}
                       for t, d in ranked[:RULE["top_trains"]]],
        }
    return out


def _rt_fresh(conn, now: int) -> bool:
    try:
        return now - int(get_meta(conn, "rt_trip_updates_cer")) <= RT_FRESH_MAX_AGE
    except (TypeError, ValueError):
        return False


def _sample(conn, eid: int, now: int, m: dict, signal: int):
    conn.execute(text("""
        INSERT INTO anomaly_sample (episode_id, ts, monitored, scheduled_now,
            delayed_15, delayed_30, share, max_delay_sec, median_delay_sec, signal)
        VALUES (:e, :ts, :mon, :sch, :d15, :d30, :sh, :mx, :med, :sig)
        ON CONFLICT DO NOTHING"""),
        {"e": eid, "ts": now, "mon": m["monitored"], "sch": m["scheduled_now"],
         "d15": m["delayed_15"], "d30": m["delayed_30"], "sh": m["share"],
         "mx": m["max_delay_sec"], "med": m["median_delay_sec"], "sig": signal})


def _clear_ok(conn, eid: int, now: int) -> bool:
    """Resuelve solo con evidencia de muestras frescas sin señal: en
    [now-CLEAR, now] hay >= CLEAR_MIN_SAMPLES muestras, todas signal=0, y la
    más antigua llega como mucho CLEAR_TOL_SEC tarde (un minuto perdido al inicio)."""
    lo = now - ANOMALY_CLEAR_SEC
    r = conn.execute(text("""
        SELECT count(*) AS n, min(ts) AS first,
               COALESCE(bool_and(signal = 0), false) AS all_clear
        FROM anomaly_sample WHERE episode_id=:e AND ts BETWEEN :lo AND :now"""),
        {"e": eid, "lo": lo, "now": now}).mappings().one()
    return (r["n"] >= ANOMALY_CLEAR_MIN_SAMPLES and r["all_clear"]
            and r["first"] <= lo + ANOMALY_CLEAR_TOL_SEC)


def evaluate(conn, now) -> dict:
    """Un ciclo de evaluación en `now` (epoch). Repetir con el mismo `now`
    no vuelve a contar la evaluación de un episodio."""
    now = int(now)
    if not _rt_fresh(conn, now):
        # Sin RT fresco no se abre ni se cierra nada: solo se marca el hueco.
        conn.execute(text(f"""
            UPDATE anomaly_episode SET rt_gap_since = COALESCE(rt_gap_since, :now),
                   last_eval_at = :now
            WHERE status IN {OPEN_STATES}"""), {"now": now})
        return {"rt_fresh": False}

    conn.execute(text(f"""
        UPDATE anomaly_episode SET rt_gap_since = NULL
        WHERE status IN {OPEN_STATES} AND rt_gap_since IS NOT NULL"""))
    metrics = line_metrics(conn, now)
    cands = {k for k, m in metrics.items() if is_candidate(m)}
    open_eps = {(e["nucleo_code"], e["family_slug"]): dict(e) for e in conn.execute(text(
        f"SELECT * FROM anomaly_episode WHERE status IN {OPEN_STATES} ORDER BY id")).mappings()}
    counts = {"rt_fresh": True, "candidates": len(cands),
              "opened": 0, "confirmed": 0, "resolved": 0}

    # Líneas con señal: abrir o sumar evaluación con señal
    for key in sorted(cands):
        m = metrics[key]
        mx = m["max_delay_sec"] or 0
        lm = json.dumps(m, ensure_ascii=False)
        ep = open_eps.get(key)
        if ep is None:
            eid = conn.execute(text("""
                INSERT INTO anomaly_episode (nucleo_code, family_slug, status,
                    opened_at, last_signal_at, last_eval_at, evaluations,
                    signal_evaluations, peak_delayed_15, peak_share,
                    peak_max_delay_sec, last_metrics)
                VALUES (:n, :f, 'observacion', :now, :now, :now, 1, 1, :d15,
                    :sh, :mx, CAST(:lm AS jsonb)) RETURNING id"""),
                {"n": key[0], "f": key[1], "now": now, "d15": m["delayed_15"],
                 "sh": m["share"], "mx": mx, "lm": lm}).scalar()
            counts["opened"] += 1
        else:
            if ep["last_eval_at"] == now:
                continue  # ya evaluado en este instante
            eid = ep["id"]
            n_sig = (ep["signal_evaluations"] or 0) + 1
            status, confirmed_at = ep["status"], ep["confirmed_at"]
            if (status == "observacion"
                    and now - ep["opened_at"] >= ANOMALY_CONFIRM_SEC
                    and n_sig >= ANOMALY_CONFIRM_EVALS):
                status, confirmed_at = "confirmada", now
                counts["confirmed"] += 1
            conn.execute(text("""
                UPDATE anomaly_episode SET status=:st, confirmed_at=:cf,
                    last_signal_at=:now, last_eval_at=:now,
                    evaluations=COALESCE(evaluations,0)+1,
                    signal_evaluations=:ns,
                    peak_delayed_15=GREATEST(COALESCE(peak_delayed_15,0), :d15),
                    peak_share=GREATEST(COALESCE(peak_share,0), :sh),
                    peak_max_delay_sec=GREATEST(COALESCE(peak_max_delay_sec,0), :mx),
                    last_metrics=CAST(:lm AS jsonb)
                WHERE id=:id"""),
                {"st": status, "cf": confirmed_at, "now": now, "ns": n_sig,
                 "d15": m["delayed_15"], "sh": m["share"], "mx": mx, "lm": lm,
                 "id": eid})
        _sample(conn, eid, now, m, 1)

    # Episodios abiertos cuya línea ya no tiene señal: evaluación sin señal.
    # Solo se escribe en evaluaciones FRESCAS, así que un hueco de RT nunca
    # cuenta como tiempo despejado.
    for key, ep in open_eps.items():
        if key in cands or ep["last_eval_at"] == now:
            continue
        m = metrics.get(key) or ZERO_METRICS
        conn.execute(text("""
            UPDATE anomaly_episode SET evaluations=COALESCE(evaluations,0)+1,
                last_eval_at=:now, last_metrics=CAST(:lm AS jsonb)
            WHERE id=:id"""),
            {"now": now, "lm": json.dumps(m, ensure_ascii=False), "id": ep["id"]})
        _sample(conn, ep["id"], now, m, 0)
        if _clear_ok(conn, ep["id"], now):
            conn.execute(text("""UPDATE anomaly_episode SET status='resuelta',
                resolved_at=:now WHERE id=:id"""), {"now": now, "id": ep["id"]})
            counts["resolved"] += 1

    conn.execute(text("DELETE FROM anomaly_sample WHERE ts < :c"),
                 {"c": now - SAMPLE_KEEP_SEC})
    conn.execute(text("""DELETE FROM anomaly_episode
        WHERE status = 'resuelta' AND resolved_at < :c"""),
        {"c": now - EPISODE_KEEP_SEC})
    return counts


def run_anomaly_cycle() -> dict:
    """Ejecuta un ciclo de anomalías con la hora actual, en una transacción."""
    with engine.begin() as conn:
        return evaluate(conn, int(time.time()))
