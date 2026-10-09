"""Evaluación read-only del gate de estadísticas contra producción.

NO toca `STATS_PUBLIC`: solo informa si los datos de producción pasarían
las reglas existentes (representatividad diaria + gate descriptivo/
comparativo). Diseñado para ejecutarse con una conexión de SOLO LECTURA
(p. ej. usuario `readonly` o sesión con `default_transaction_read_only`).

Uso:
    DATABASE_URL="postgresql://readonly:***@db/renfe" \
        python scripts/stats_gate_check.py

Salida: JSON en stdout con, por feed/fuente: días evaluados, elegibles,
excluidos por motivo, observaciones, cobertura estimada y veredicto del
gate (descriptive / comparative) según api/stats.py::STATS_GATE.
"""

import collections
import datetime as dt
import json
import os
import sys

import psycopg

TZ = dt.timezone(dt.timedelta(hours=2))  # Europe/Madrid (aprox.; el gate
# oficial usa zoneinfo — aquí solo se informa, no se decide publicación)

STATS_GATE = {
    "descriptive": {"min_instances": 30, "min_days": 2, "min_coverage_pct": 40.0},
    "comparative": {"min_instances": 100, "min_days": 5, "min_coverage_pct": 70.0,
                    "min_units": 2},
}
CAPTURE_FIRST_MAX_SEC = 4 * 3600
CAPTURE_LAST_MIN_SEC = 23 * 3600 + 1800
MAX_GAP_SEC = 1800
REP_SOURCE = {"reported": "fleet", "prediction": "trip_update"}

DB_URL = os.environ["DATABASE_URL"]  # sin default: forzar conexión explícita


def main():
    conn = psycopg.connect(DB_URL, autocommit=True)
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    out = {"generated": dt.datetime.now(dt.UTC).isoformat(),
           "note": "informativo: replica las reglas de api/stats.py; la "
                   "publicación real la decide la API con STATS_PUBLIC"}

    # 1. inicio efectivo de captura por feed/fuente
    caps = {}
    for k, v in conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'capture_start_%'"):
        _, _, feed, source = k.split("_", 3)
        caps.setdefault(feed, {})[source] = int(v)
    out["capture_start"] = caps

    # 2. días con observaciones por feed
    obs = conn.execute("""
        SELECT feed, service_date, count(*)
        FROM observations WHERE service_date IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    by_feed = collections.defaultdict(list)
    for feed, day, n in obs:
        by_feed[feed].append((day, n))

    # 3. días con snapshot de programación (circulation)
    snap_days = {}
    try:
        for feed, day in conn.execute(
                "SELECT feed, day FROM circulation GROUP BY 1, 2"):
            snap_days.setdefault(feed, set()).add(day)
    except Exception as e:
        out["circulation_error"] = str(e)[:200]
        snap_days = {}

    today = dt.datetime.now(TZ).date()
    for feed, days in by_feed.items():
        for source in ("reported", "prediction"):
            if source == "reported" and feed == "ld":
                continue  # flota solo existe para CER
            cap = (caps.get(feed) or {}).get(REP_SOURCE[source])
            eligible, excluded = [], collections.Counter()
            for day, n in days:
                if day >= today:
                    excluded["dia_en_curso"] += 1
                    continue
                if cap and dt.datetime.fromtimestamp(cap, TZ).date() > day:
                    excluded["antes_de_captura"] += 1
                    continue
                if cap is None:
                    excluded["sin_inicio_captura"] += 1
                    continue
                if feed in snap_days and day not in snap_days[feed]:
                    excluded["sin_snapshot"] += 1
                    continue
                # salud de captura del día (si existe tabla capture_health)
                try:
                    h = conn.execute(
                        """SELECT polls, first_ts, last_ts, max_gap_sec
                           FROM capture_health
                           WHERE feed=%s AND source=%s AND day=%s""",
                        (feed, REP_SOURCE[source], day)).fetchone()
                except Exception:
                    h = None
                if h is None:
                    excluded["sin_registro_captura"] += 1
                    continue
                _polls, first_ts, last_ts, max_gap = h
                lo = dt.datetime.combine(day, dt.time(), tzinfo=TZ).timestamp()
                if first_ts and first_ts - lo > CAPTURE_FIRST_MAX_SEC:
                    excluded["inicio_tardio"] += 1
                    continue
                if last_ts and (lo + 86400) - last_ts > (86400 - CAPTURE_LAST_MIN_SEC):
                    excluded["fin_temprano"] += 1
                    continue
                if max_gap and max_gap > MAX_GAP_SEC:
                    excluded["hueco_captura"] += 1
                    continue
                eligible.append((day, n))
            n_obs = sum(n for _, n in eligible)
            verdict = {}
            for level, g in STATS_GATE.items():
                verdict[level] = {
                    "pass": (n_obs >= g["min_instances"]
                             and len(eligible) >= g["min_days"]),
                    "instances": n_obs, "days": len(eligible),
                    "need": g,
                }
            out[f"{feed}/{source}"] = {
                "dias_con_obs": len(days), "elegibles": len(eligible),
                "excluidos": dict(excluded), "gate": verdict}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    sys.exit(main())
