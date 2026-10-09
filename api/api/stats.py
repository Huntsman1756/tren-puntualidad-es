"""Estadísticas históricas por ámbito (línea, núcleo, territorio,
estación, trayecto) — retrasos informados, nunca puntualidad real.

Importa de api.main SOLO en tiempo de llamada para evitar el ciclo
main -> stats -> main.
"""
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from api.common import NUCLEO_BY_SLUG, NUCLEOS, TZ, local_midnight
from api.db import engine

stats_router = APIRouter()


def _now():
    from api.main import _now as f
    return f()


def _parse_date(s):
    from api.main import _parse_date as f
    return f(s)


def _parse_time(s):
    from api.main import _parse_time as f
    return f(s)


def _geo_rows_():
    from api.main import _geo_rows as f
    return f()


def _slug_ccaa(x):
    from api.main import _slug_ccaa as f
    return f(x)


def _slug_prov(x):
    from api.main import _slug_prov as f
    return f(x)


# ---------- estadísticas históricas (v0.4.0 / v0.3.6) ----------
#
# Semántica honesta: NUNCA hay "puntualidad real" ni "llegada efectiva".
# Solo existen dos tipos de dato observado:
#   kind='reported'   retraso informado por la flota (visor Renfe),
#                     válido en la parada donde se notificó
#   kind='prediction' estimación del feed GTFS-RT trip_updates
# El denominador sale del snapshot `circulation`/`circulation_stop`
# (programación capturada e inmutable una vez cerrado el día).
# Identidad de línea: tabla `line_route` (núcleo + familia canónicos).

# Gate estadístico reproducible — los umbrales son públicos y se devuelven
# en la propia respuesta para que cualquiera pueda reproducir el juicio.
STATS_GATE = {
    "descriptive": {"min_instances": 30, "min_days": 2,
                    "min_coverage_pct": 40.0},
    "comparative": {"min_instances": 100, "min_days": 5,
                    "min_coverage_pct": 70.0, "min_units": 2},
}

# Gate diario (serie /stats/daily): solo días representativos con datos.
DAY_GATE = {"min_with_data": 30, "min_coverage_pct": 40.0}

# Representatividad de día: sondeos RT de la fuente correspondiente.
CAPTURE_FIRST_MAX_SEC = 4 * 3600          # primer sondeo antes de las 04:00
CAPTURE_LAST_MIN_SEC = 23 * 3600 + 1800   # último sondeo desde las 23:30
MAX_GAP_SEC = 1800                        # hueco máximo entre sondeos (30 min)
REP_SOURCE = {"reported": "fleet", "prediction": "trip_update"}
KINDS = ("reported", "prediction")

REPRESENTATIVITY = {
    "capture_first_poll_max": "04:00",
    "capture_last_poll_min": "23:30",
    "max_gap_sec": MAX_GAP_SEC,
    "sources": REP_SOURCE,
    "reasons": {
        "dia_en_curso": "el día aún no ha cerrado",
        "antes_de_captura": "día anterior al inicio de captura de la fuente",
        "dia_inicio_captura": "día de inicio de captura, que empezó tarde",
        "sin_inicio_captura": "no consta inicio de captura para la fuente",
        "sin_snapshot": "sin snapshot de programación para el día",
        "snapshot_retrospectivo": "programación capturada después del día",
        "sin_fuente": "la fuente no existe para este feed (flota solo CER)",
        "sin_registro_captura": "sin registro de salud de captura ese día",
        "inicio_tardio": "primer sondeo posterior a las 04:00 local",
        "fin_temprano": "último sondeo anterior a las 23:30 local",
        "hueco_captura": "hueco entre sondeos mayor de 30 min",
    },
    "day_gate": DAY_GATE,
}

DELAY_BUCKETS = [  # (lo_s_excl, hi_s_incl, etiqueta)
    (None, -60, "adelanto >1 min"),
    (-60, 120, "a tiempo (<=2 min)"),
    (120, 300, "2-5 min"),
    (300, 600, "5-10 min"),
    (600, 1200, "10-20 min"),
    (1200, 1800, "20-30 min"),
    (1800, 3600, "30-60 min"),
    (3600, 7200, "1-2 h"),
    (7200, None, ">2 h"),
]

_STATS_SEMANTICS = (
    "Distribución del retraso INFORMADO por las fuentes RT (flota del visor "
    "o predicciones GTFS-RT) en el ámbito filtrado. No son llegadas "
    "efectivas ni puntualidad real: no existe feed oficial de llegada "
    "real observada. Las cifras gated solo usan días representativos "
    "(ver representativity); el resto se explica en excluded_days.")


def _natkey(s: str | None):
    """Clave de orden natural: C2 < C10."""
    return [int(x) if x.isdigit() else x.lower()
            for x in re.split(r"(\d+)", s or "")]


def _capture_starts() -> dict:
    """{feed: {source: epoch}} — inicio efectivo de captura tipificada."""
    try:
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT key, value FROM meta"
                " WHERE key LIKE 'capture_start_%'")).all()
    except Exception:
        return {}
    out: dict = {}
    for k, v in rows:
        parts = k.split("_", 3)   # capture_start_<feed>_<source>
        if len(parts) == 4:
            out.setdefault(parts[2], {})[parts[3]] = int(v)
    return out


def _cap_day(feed: str, source: str, caps: dict):
    """Fecha local del inicio de captura de (feed, fuente) o None."""
    ts = (caps.get(feed) or {}).get(source)
    return datetime.fromtimestamp(ts, TZ).date() if ts else None


def _eval_gate(n: int, days_obs: int, cov: float | None, level: str) -> dict:
    g = STATS_GATE[level]
    checks = {
        "min_instances": {"need": g["min_instances"], "have": n,
                          "pass": n >= g["min_instances"]},
        "min_days": {"need": g["min_days"], "have": days_obs,
                     "pass": days_obs >= g["min_days"]},
        "min_coverage_pct": {"need": g["min_coverage_pct"], "have": cov,
                             "pass": cov is not None
                             and cov >= g["min_coverage_pct"]},
    }
    return {"level": level,
            "pass": all(v["pass"] for v in checks.values()),
            "checks": checks}


def representative_days(conn, feeds, kind, d0, d1, today, caps=None) -> dict:
    """{(feed, day): {"ok": bool, "reasons": [...]}} para cada día con
    circulación en [d0, d1]. Un día es representativo solo si cumple TODAS
    las reglas: cerrado, posterior al inicio de captura de la fuente,
    snapshot no retrospectivo y salud de captura correcta."""
    src = REP_SOURCE[kind]
    caps = _capture_starts() if caps is None else caps
    params = {"feeds": list(feeds), "d0": d0, "d1": d1, "src": src}
    days = conn.execute(text("""
        SELECT DISTINCT feed, day FROM circulation
        WHERE feed = ANY(:feeds) AND day BETWEEN :d0 AND :d1"""),
        params).all()
    sched = {(f, d): late for f, d, late in conn.execute(text("""
        SELECT feed, day, late FROM sched_capture
        WHERE feed = ANY(:feeds) AND day BETWEEN :d0 AND :d1"""), params)}
    health = {(f, d): (a, b, g) for f, d, a, b, g in conn.execute(text("""
        SELECT feed, day, first_ts, last_ts, max_gap_sec FROM capture_health
        WHERE source = :src AND feed = ANY(:feeds)
          AND day BETWEEN :d0 AND :d1"""), params)}
    out: dict = {}
    for feed, day in days:
        why = []
        if day >= today:
            why.append("dia_en_curso")
        s = sched.get((feed, day))
        if s is None:
            why.append("sin_snapshot")
        elif s:
            why.append("snapshot_retrospectivo")
        mid = local_midnight(day)
        if feed == "ld" and kind == "reported":
            why.append("sin_fuente")          # LD no publica flota
        else:
            ts = (caps.get(feed) or {}).get(src)
            if ts is None:
                why.append("sin_inicio_captura")
            else:
                cd = datetime.fromtimestamp(ts, TZ).date()
                if day < cd:
                    why.append("antes_de_captura")
                elif day == cd and ts > mid + CAPTURE_FIRST_MAX_SEC:
                    why.append("dia_inicio_captura")
            h = health.get((feed, day))
            if h is None:
                why.append("sin_registro_captura")
            else:
                first, last, gap = h
                if first is None or first > mid + CAPTURE_FIRST_MAX_SEC:
                    why.append("inicio_tardio")
                if last is None or last < mid + CAPTURE_LAST_MIN_SEC:
                    why.append("fin_temprano")
                if (gap or 0) > MAX_GAP_SEC:
                    why.append("hueco_captura")
        out[(feed, day)] = {"ok": not why, "reasons": why}
    return out


def _stats_scope(feed, nucleo, line, ccaa, provincia, station,
                 frm, to, h0, h1):
    """Devuelve (where_sql, params, obs_conds) sobre alias c=circulation.

    obs_conds restringe qué observaciones cuentan: para estación/trayecto/
    territorio solo cuentan las obs EN las paradas del ámbito.
    """
    where = ["c.day BETWEEN :d0 AND :d1"]
    p: dict = {"feed": feed}   # siempre definido (puede ser NULL)
    obs_conds: list[str] = []
    hour_obs = "(COALESCE(cs.dep,cs.arr) % 86400) BETWEEN :h0 AND :h1"
    if feed:
        where.append("c.feed = :feed")
    if nucleo:
        code = NUCLEO_BY_SLUG.get(nucleo)
        if not code:
            return None, {"error": f"núcleo desconocido: {nucleo}"}, None
        p["nuc_code"] = code
        where.append("""EXISTS (SELECT 1 FROM line_route lr
            WHERE lr.feed=c.feed AND lr.route_id=c.route_id
              AND lr.nucleo_code=:nuc_code)""")
    if line:
        # línea (C4a) o familia (C4 = C4, C4a, C4b); insensible a mayúsculas
        p["line"] = line.lower()
        where.append("""EXISTS (SELECT 1 FROM line_route lr
            WHERE lr.feed=c.feed AND lr.route_id=c.route_id
              AND (lr.line_slug=:line OR lr.family_slug=:line))""")
    if station:
        fs = station.split(":")
        if len(fs) != 2 or fs[0] not in ("cer", "ld"):
            return None, {"error": "station debe ser feed:stop_id"}, None
        p["st_feed"], p["st_id"] = fs
        where.append(f"""EXISTS (SELECT 1 FROM circulation_stop cs
            WHERE cs.feed=c.feed AND cs.day=c.day AND cs.trip_id=c.trip_id
              AND cs.feed=:st_feed AND cs.stop_id=:st_id
              {'AND ' + hour_obs if h0 is not None else ''})""")
        obs_conds.append("o.feed=:st_feed AND o.stop_id=:st_id")
    if frm or to:
        if not (frm and to):
            return None, {"error": "from y to van juntos"}, None
        for tag, v in (("frm", frm), ("to", to)):
            fs = v.split(":")
            if len(fs) != 2 or fs[0] not in ("cer", "ld"):
                return None, {"error": "from/to deben ser feed:stop_id"}, None
            p[tag + "_f"], p[tag + "_s"] = fs
        where.append(f"""EXISTS (SELECT 1
            FROM circulation_stop a
            JOIN circulation_stop b
              ON b.feed=a.feed AND b.day=a.day AND b.trip_id=a.trip_id
             AND b.seq>a.seq AND b.feed=:to_f AND b.stop_id=:to_s
            WHERE a.feed=c.feed AND a.day=c.day AND a.trip_id=c.trip_id
              AND a.feed=:frm_f AND a.stop_id=:frm_s
              {'AND (a.dep % 86400) BETWEEN :h0 AND :h1' if h0 is not None else ''})""")
        # retraso informado al llegar a la parada destino del trayecto
        obs_conds.append("o.feed=:to_f AND o.stop_id=:to_s")
    if ccaa or provincia:
        cc, pv = _terr_codes(ccaa, provincia)
        if (ccaa and not cc) or (provincia and not pv):
            return None, {"error": "territorio desconocido"}, None
        tcond = []
        if cc:
            tcond.append("g.ccaa_code = :cc")
            p["cc"] = cc
        if pv:
            tcond.append("g.provincia = :pv")
            p["pv"] = pv
        twhere = " AND ".join(tcond)
        where.append(f"""EXISTS (SELECT 1 FROM circulation_stop cs
            JOIN geo_station g ON g.feed=cs.feed AND g.stop_id=cs.stop_id
            WHERE cs.feed=c.feed AND cs.day=c.day AND cs.trip_id=c.trip_id
              AND g.active=1 AND {twhere}
              {'AND ' + hour_obs if h0 is not None else ''})""")
        obs_conds.append(f"""EXISTS (SELECT 1 FROM geo_station g2
            WHERE g2.feed=o.feed AND g2.stop_id=o.stop_id
              AND g2.active=1 AND {twhere})""")
    if h0 is not None:
        p["h0"], p["h1"] = h0, h1
        if not (station or (frm and to) or ccaa or provincia):
            # ámbito sin parada (feed/línea/núcleo): la franja se aplica a
            # la primera salida programada de la circulación
            where.append("(c.dep_secs % 86400) BETWEEN :h0 AND :h1")
    return " AND ".join(where), p, obs_conds


def _terr_codes(ccaa, provincia):
    """slug ccaa/provincia -> (ccaa_code, nombre provincia) o (None, None)."""
    geo = _geo_rows_()
    cc = pv = None
    for g in geo.values():
        if ccaa and _slug_ccaa(g["ccaa_code"]) == ccaa:
            cc = g["ccaa_code"]
        if provincia and _slug_prov(g["provincia"] or "") == provincia:
            pv = g["provincia"]
    if (ccaa and not cc) or (provincia and not pv):
        return None, None
    return cc, pv


def _hist(delays: list[int]) -> list[dict]:
    return [{"label": label, "lo_sec": lo, "hi_sec": hi,
             "n": sum(1 for d in delays
                      if (lo is None or d > lo) and (hi is None or d <= hi))}
            for lo, hi, label in DELAY_BUCKETS]


def _pctl(sorted_vals: list[int], q: float):
    if not sorted_vals:
        return None
    return sorted_vals[min(int(len(sorted_vals) * q), len(sorted_vals) - 1)]


def _hm(secs: int) -> str:
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}"


def _window(date_from, date_to, hour_from, hour_to, default_days):
    """(d0, d1, h0, h1, today) validados para los endpoints de estadística."""
    _, _, today = _now()
    d0 = _parse_date(date_from) or today - timedelta(days=default_days)
    d1 = _parse_date(date_to) or today
    if d1 < d0:
        raise HTTPException(400, "date_from debe ser <= date_to")
    h0, h1 = _parse_time(hour_from), _parse_time(hour_to)
    if (h0 is None) != (h1 is None):
        raise HTTPException(400, "hour_from y hour_to van juntos")
    if h0 is not None and h1 <= h0:
        raise HTTPException(400, "hour_to debe ser > hour_from")
    return d0, d1, h0, h1, today


@stats_router.get("/stats/options")
def stats_options():
    """Ámbitos filtrables verificables para /stats/delays.

    Líneas = familias (C4 agrupa C4, C4a, C4b) con viajes en el GTFS vigente.
    stations = paradas distintas servidas por las rutas del núcleo
    (line_route + trips + stop_times, feed cer)."""
    with engine.connect() as c:
        fams = c.execute(text("""
            SELECT lr.nucleo_code, lr.family_slug, min(lr.family_code) AS fam,
                   count(*) AS routes
            FROM line_route lr
            WHERE lr.feed='cer' AND lr.nucleo_code IS NOT NULL
              AND lr.family_slug IS NOT NULL AND lr.n_trips > 0
            GROUP BY lr.nucleo_code, lr.family_slug""")).mappings().all()
        stops = c.execute(text("""
            SELECT lr.nucleo_code, count(DISTINCT st.stop_id) AS n
            FROM line_route lr
            JOIN trips t ON t.feed=lr.feed AND t.route_id=lr.route_id
            JOIN stop_times st ON st.feed=t.feed AND st.trip_id=t.trip_id
            WHERE lr.feed='cer' AND lr.nucleo_code IS NOT NULL
              AND lr.n_trips > 0
            GROUP BY lr.nucleo_code""")).mappings().all()
        ld = c.execute(text("""
            SELECT DISTINCT line_code, line_slug FROM line_route
            WHERE feed='ld' AND n_trips > 0
              AND line_slug IS NOT NULL AND line_slug <> ''""")).mappings().all()
    st_by = {r["nucleo_code"]: r["n"] for r in stops}
    lines_by: dict = {}
    for r in fams:
        lines_by.setdefault(r["nucleo_code"], []).append(
            {"line": r["fam"], "slug": r["family_slug"], "routes": r["routes"]})
    nucleos = []
    for code, lines in lines_by.items():
        info = NUCLEOS.get(code)
        if not info:
            continue
        lines.sort(key=lambda x: _natkey(x["line"]))
        nucleos.append({"slug": info["slug"], "name": info["name"],
                        "stations": st_by.get(code, 0), "lines": lines})
    nucleos.sort(key=lambda x: (-x["stations"], x["name"]))
    ld_lines = sorted(({"line": r["line_code"], "slug": r["line_slug"]}
                       for r in ld), key=lambda x: _natkey(x["line"]))
    return {"nucleos": nucleos, "ld_lines": ld_lines,
            "gate": STATS_GATE, "representativity": REPRESENTATIVITY,
            "semantics": _STATS_SEMANTICS}


def _delay_stats(feed, nucleo, line, ccaa, provincia, station,
                 frm, to, d0, d1, h0, h1, today):
    """Núcleo del cálculo: denominador por día, representatividad por
    (feed, día) y última obs por instancia y kind. Devuelve dict para la
    respuesta o lanza HTTPException."""
    where, p, obs_conds = _stats_scope(feed, nucleo, line, ccaa, provincia,
                                       station, frm, to, h0, h1)
    if where is None:
        raise HTTPException(400, p.get("error", "filtros inválidos"))
    p["d0"], p["d1"] = d0, d1
    feeds_scope = [feed] if feed else ["cer", "ld"]
    caps = _capture_starts()
    stop_cond = ("AND (" + " AND ".join(obs_conds) + ")") if obs_conds else ""
    with engine.connect() as c:
        sched_rows = c.execute(text(f"""
            SELECT c.feed, c.day, count(DISTINCT c.trip_id) AS n
            FROM circulation c WHERE {where}
            GROUP BY c.feed, c.day ORDER BY c.day"""), p).mappings().all()
        rep = {k: representative_days(c, feeds_scope, k, d0, d1, today, caps)
               for k in KINDS}
        numer = {}
        for kind in KINDS:
            rows = c.execute(text(f"""
                SELECT DISTINCT ON (o.trip_id, o.service_date, o.feed)
                       o.feed, o.trip_id, o.service_date, o.delay
                FROM observations o
                WHERE o.kind=:k AND o.delay IS NOT NULL
                  AND o.service_date BETWEEN :d0 AND :d1
                  AND (CAST(:feed AS text) IS NULL OR o.feed = :feed)
                  AND EXISTS (SELECT 1 FROM circulation c
                              WHERE c.feed=o.feed AND c.trip_id=o.trip_id
                                AND c.day=o.service_date AND {where})
                  {stop_cond}
                ORDER BY o.trip_id, o.service_date, o.feed,
                         o.observed_at DESC"""),
                {**p, "k": kind}).mappings().all()
            numer[kind] = rows
    return {"sched_rows": sched_rows, "numer": numer, "rep": rep,
            "caps": caps, "feeds_scope": feeds_scope}


def _kind_block(kind, rows, sched_rows, rep, caps, feeds_scope):
    """Bloque de un kind. Todas las cifras gated usan solo observaciones y
    circulaciones de días representativos; `with_data_all` es el bruto."""
    src = REP_SOURCE[kind]

    def ok(f, d):
        return rep.get((f, d), {}).get("ok", False)

    good = [r for r in rows if ok(r["feed"], r["service_date"])]
    delays = sorted(int(r["delay"]) for r in good)
    n = len(delays)
    days_obs = len({(r["feed"], r["service_date"]) for r in good})
    sched_all = sum(r["n"] for r in sched_rows)
    sched_rep = sum(r["n"] for r in sched_rows if ok(r["feed"], r["day"]))
    rep_days = sum(1 for r in sched_rows if ok(r["feed"], r["day"]))
    cov = round(100 * n / sched_rep, 1) if sched_rep else None
    excluded = [{"day": str(r["day"]), "feed": r["feed"],
                 "reasons": rep.get((r["feed"], r["day"]), {}).get(
                     "reasons", ["sin_snapshot"])}
                for r in sched_rows if not ok(r["feed"], r["day"])]
    since = {}
    for f in feeds_scope:
        cd = _cap_day(f, src, caps)
        since[f] = str(cd) if cd else None
    return {
        "source": src,
        "capture_since": since,
        "with_data": n,
        "with_data_all": len(rows),
        "scheduled": sched_all,
        "scheduled_representative": sched_rep,
        "representative_days": rep_days,
        "excluded_days": excluded,
        "coverage_pct": cov,
        "days_observed": days_obs,
        "delay_median_sec": _pctl(delays, 0.5),
        "delay_p90_sec": _pctl(delays, 0.9),
        "delay_mean_sec": round(sum(delays) / n) if n else None,
        "histogram": _hist(delays),
        "gate_descriptive": _eval_gate(n, days_obs, cov, "descriptive"),
        "gate_comparative": _eval_gate(n, days_obs, cov, "comparative"),
    }


@stats_router.get("/stats/delays")
def stats_delays(feed: str | None = Query(None, pattern="^(cer|ld)$"),
                 nucleo: str | None = None, line: str | None = None,
                 ccaa: str | None = None, provincia: str | None = None,
                 station: str | None = None,
                 frm: str | None = Query(None, alias="from"),
                 to: str | None = None,
                 date_from: str | None = None,
                 date_to: str | None = None,
                 hour_from: str | None = None,
                 hour_to: str | None = None):
    """Distribución del retraso informado por ámbito, con gate estadístico.

    Por instancia de circulación (feed, trip_id, service_date) se toma el
    ÚLTIMO retraso informado dentro del ámbito. Las cifras gated solo usan
    días representativos; los días excluidos se listan con sus motivos."""
    d0, d1, h0, h1, today = _window(date_from, date_to, hour_from, hour_to, 90)
    r = _delay_stats(feed, nucleo, line, ccaa, provincia, station,
                     frm, to, d0, d1, h0, h1, today)
    kinds = {k: _kind_block(k, r["numer"][k], r["sched_rows"], r["rep"][k],
                            r["caps"], r["feeds_scope"])
             for k in KINDS}
    # serie diaria bruta (no gated; la gated está en kinds y /stats/daily)
    obs_cnt: dict = {}
    for kind in KINDS:
        for row in r["numer"][kind]:
            key = (row["feed"], row["service_date"])
            obs_cnt.setdefault(key, {}).setdefault(kind, 0)
            obs_cnt[key][kind] += 1
    by_day = []
    for x in r["sched_rows"]:
        key = (x["feed"], x["day"])
        by_day.append({
            "day": str(x["day"]), "feed": x["feed"], "scheduled": x["n"],
            "with_data": {k: obs_cnt.get(key, {}).get(k, 0) for k in KINDS},
            "representative": {k: r["rep"][k][key]["ok"] for k in KINDS}})
    links = {}
    if station:
        links["station"] = f"/estacion/{station}"
    if frm and to:
        links["journey"] = f"/trayecto?from={frm}&to={to}"
    return {
        "scope": {k: v for k, v in
                  {"feed": feed, "nucleo": nucleo, "line": line,
                   "ccaa": ccaa, "provincia": provincia,
                   "station": station,
                   "journey": f"{frm}->{to}" if frm or to else None}.items()
                  if v},
        "window": {"from": str(d0), "to": str(d1),
                   "hour": [_hm(h0), _hm(h1)] if h0 is not None else None},
        "capture_start": r["caps"],
        "days_monitored": len({(x["feed"], x["day"]) for x in r["sched_rows"]}),
        "by_day": by_day,
        "kinds": kinds,
        "representativity": REPRESENTATIVITY,
        "gate": STATS_GATE,
        "links": links,
        "semantics": _STATS_SEMANTICS,
    }


def _daily_entries(r) -> list[dict]:
    """Una entrada por (feed, día) con circulación en el ámbito."""
    sched = {(x["feed"], x["day"]): x["n"] for x in r["sched_rows"]}
    grp: dict = {k: {} for k in KINDS}
    for kind in KINDS:
        for row in r["numer"][kind]:
            grp[kind].setdefault((row["feed"], row["service_date"]), []).append(
                int(row["delay"]))
    out = []
    for (feed, day), n_sched in sorted(sched.items(),
                                       key=lambda kv: (kv[0][1], kv[0][0])):
        kinds = {}
        for kind in KINDS:
            rep = r["rep"][kind].get((feed, day),
                                     {"ok": False, "reasons": ["sin_snapshot"]})
            ds = sorted(grp[kind].get((feed, day), []))
            n = len(ds)
            cov = round(100 * n / n_sched, 1) if n_sched else None
            gate = (rep["ok"] and n >= DAY_GATE["min_with_data"]
                    and cov is not None
                    and cov >= DAY_GATE["min_coverage_pct"])
            kinds[kind] = {
                "representative": rep["ok"],
                "reasons": rep["reasons"],
                "with_data": n,
                "coverage_pct": cov,
                "delay_median_sec": _pctl(ds, 0.5) if gate else None,
                "delay_p90_sec": _pctl(ds, 0.9) if gate else None,
                "over_5min_pct": (round(100 * sum(d > 300 for d in ds) / n, 1)
                                  if gate else None),
                "gate_day": gate,
            }
        # día representativo = todas las fuentes aplicables (LD sin flota)
        appl = [k for k in KINDS if not (k == "reported" and feed == "ld")]
        reasons: list[str] = []
        for k in appl:
            for why in kinds[k]["reasons"]:
                if why not in reasons:
                    reasons.append(why)
        out.append({
            "day": str(day), "feed": feed, "scheduled": n_sched,
            "representative": all(kinds[k]["representative"] for k in appl),
            "reasons": reasons, "kinds": kinds})
    return out


@stats_router.get("/stats/daily")
def stats_daily(feed: str | None = Query(None, pattern="^(cer|ld)$"),
                nucleo: str | None = None, line: str | None = None,
                ccaa: str | None = None, provincia: str | None = None,
                station: str | None = None,
                frm: str | None = Query(None, alias="from"),
                to: str | None = None,
                date_from: str | None = None,
                date_to: str | None = None,
                hour_from: str | None = None,
                hour_to: str | None = None):
    """Serie diaria del retraso informado y predicho por ámbito.

    Ventana por defecto: últimos 30 días. gate_day exige día representativo,
    >= DAY_GATE.min_with_data observaciones y cobertura >= min_coverage_pct;
    si no se cumple, median/p90/over_5min_pct van a null (conteos y
    cobertura siempre se devuelven)."""
    d0, d1, h0, h1, today = _window(date_from, date_to, hour_from, hour_to, 29)
    r = _delay_stats(feed, nucleo, line, ccaa, provincia, station,
                     frm, to, d0, d1, h0, h1, today)
    return {
        "scope": {k: v for k, v in
                  {"feed": feed, "nucleo": nucleo, "line": line,
                   "ccaa": ccaa, "provincia": provincia,
                   "station": station,
                   "journey": f"{frm}->{to}" if frm or to else None}.items()
                  if v},
        "window": {"from": str(d0), "to": str(d1),
                   "hour": [_hm(h0), _hm(h1)] if h0 is not None else None},
        "representativity": REPRESENTATIVITY,
        "days": _daily_entries(r),
        "semantics": _STATS_SEMANTICS,
    }


def _line_units(nucleo, feed):
    """[(valor, etiqueta)] de líneas: familias del núcleo o productos LD."""
    with engine.connect() as c:
        if nucleo:
            code = NUCLEO_BY_SLUG.get(nucleo)
            if not code:
                raise HTTPException(400, f"núcleo desconocido: {nucleo}")
            rows = c.execute(text("""
                SELECT family_slug, min(family_code) AS fam FROM line_route
                WHERE feed='cer' AND nucleo_code=:nuc AND n_trips > 0
                  AND family_slug IS NOT NULL
                GROUP BY family_slug"""), {"nuc": code}).mappings().all()
            name = NUCLEOS[code]["name"]
            units = [(r["family_slug"], f"{r['fam']} · {name}") for r in rows]
        elif feed == "ld":
            rows = c.execute(text("""
                SELECT DISTINCT line_slug, line_code FROM line_route
                WHERE feed='ld' AND n_trips > 0
                  AND line_slug IS NOT NULL AND line_slug <> ''""")).mappings().all()
            units = [(r["line_slug"], r["line_code"]) for r in rows]
        else:
            raise HTTPException(400, "comparar líneas requiere nucleo (cer) "
                                     "o feed=ld")
    return sorted(units, key=lambda u: _natkey(u[1]))


@stats_router.get("/stats/compare")
def stats_compare(by: str = Query("line", pattern="^(line|station)$"),
                  nucleo: str | None = None,
                  feed: str | None = Query(None, pattern="^(cer|ld)$"),
                  ccaa: str | None = None, provincia: str | None = None,
                  date_from: str | None = None, date_to: str | None = None,
                  hour_from: str | None = None, hour_to: str | None = None):
    """Comparativa por unidad (familias de líneas de un núcleo, productos
    LD, o estaciones). Solo se habilita si >= min_units unidades superan el
    gate comparativo. Las unidades que no lo superan NO devuelven mediana,
    p90 ni cobertura (null): nunca se compara muestra indefendible."""
    d0, d1, h0, h1, today = _window(date_from, date_to, hour_from, hour_to, 90)
    if by == "line":
        units = _line_units(nucleo, feed)
    else:
        cc, pv = _terr_codes(ccaa, provincia)
        if (ccaa and not cc) or (provincia and not pv):
            raise HTTPException(400, "territorio desconocido")
        with engine.connect() as c:
            if nucleo:
                code = NUCLEO_BY_SLUG.get(nucleo)
                if not code:
                    raise HTTPException(400, f"núcleo desconocido: {nucleo}")
                rows = c.execute(text("""
                    SELECT s.feed, s.stop_id, s.name FROM stops s
                    WHERE s.feed='cer'
                      AND EXISTS (SELECT 1 FROM stop_times st
                        JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
                        JOIN line_route lr
                          ON lr.feed=t.feed AND lr.route_id=t.route_id
                        WHERE st.feed=s.feed AND st.stop_id=s.stop_id
                          AND lr.nucleo_code=:nuc)
                      AND (CAST(:cc AS text) IS NULL OR EXISTS (
                        SELECT 1 FROM geo_station g
                        WHERE g.feed=s.feed AND g.stop_id=s.stop_id
                          AND g.active=1 AND g.ccaa_code=:cc))
                      AND (CAST(:pv AS text) IS NULL OR EXISTS (
                        SELECT 1 FROM geo_station g
                        WHERE g.feed=s.feed AND g.stop_id=s.stop_id
                          AND g.active=1 AND g.provincia=:pv))
                    ORDER BY s.name LIMIT 40"""),
                    {"nuc": code, "cc": cc, "pv": pv}).mappings().all()
            else:
                rows = c.execute(text("""
                    SELECT s.feed, s.stop_id, s.name FROM geo_station g
                    JOIN stops s ON s.feed=g.feed AND s.stop_id=g.stop_id
                    WHERE g.active=1
                      AND (CAST(:cc AS text) IS NULL OR g.ccaa_code=:cc)
                      AND (CAST(:pv AS text) IS NULL OR g.provincia=:pv)
                    ORDER BY s.name LIMIT 40"""),
                    {"cc": cc, "pv": pv}).mappings().all()
        units = [(f"{r['feed']}:{r['stop_id']}", r["name"]) for r in rows]
    out_units = []
    for val, label in units:
        r = _delay_stats(feed=feed, nucleo=nucleo,
                         line=val if by == "line" else None,
                         ccaa=ccaa, provincia=provincia,
                         station=val if by == "station" else None,
                         frm=None, to=None, d0=d0, d1=d1,
                         h0=h0, h1=h1, today=today)
        k = _kind_block("reported", r["numer"]["reported"], r["sched_rows"],
                        r["rep"]["reported"], r["caps"], r["feeds_scope"])
        passed = k["gate_comparative"]["pass"]
        out_units.append({
            "unit": val, "label": label,
            "with_data": k["with_data"],
            "scheduled_representative": k["scheduled_representative"],
            "coverage_pct": k["coverage_pct"] if passed else None,
            "delay_median_sec": k["delay_median_sec"] if passed else None,
            "delay_p90_sec": k["delay_p90_sec"] if passed else None,
            "gate": k["gate_comparative"],
        })
    g = STATS_GATE["comparative"]
    eligible = [u for u in out_units if u["gate"]["pass"]]
    enabled = len(eligible) >= g["min_units"]
    return {
        "by": by, "enabled": enabled, "gate": g,
        "reason": None if enabled else
        f"solo {len(eligible)} unidades superan el gate comparativo "
        f"(se necesitan >= {g['min_units']}) — comparativa desactivada",
        "units": out_units,
        "representativity": REPRESENTATIVITY,
        "semantics": _STATS_SEMANTICS,
    }
