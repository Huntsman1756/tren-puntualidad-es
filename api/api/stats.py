"""Estadísticas históricas por ámbito (línea, núcleo, territorio,
estación, trayecto) — retrasos informados, nunca puntualidad real.

Importa de api.main SOLO en tiempo de llamada para evitar el ciclo
main -> stats -> main.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from api.db import engine

TZ = None  # se resuelve perezoso desde api.main en cada llamada

stats_router = APIRouter()


def _tz():
    from api.main import TZ
    return TZ


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


def _tslug(s):
    from api.main import _tslug as f
    return f(s)


def _slug_ccaa(x):
    from api.main import _slug_ccaa as f
    return f(x)


def _slug_prov(x):
    from api.main import _slug_prov as f
    return f(x)

# ---------- estadísticas históricas (v0.4.0) ----------
#
# Semántica honesta: NUNCA hay "puntualidad real" ni "llegada efectiva".
# Solo existen dos tipos de dato observado:
#   kind='reported'   retraso informado por la flota (visor Renfe),
#                     válido en la parada donde se notificó
#   kind='prediction' estimación del feed GTFS-RT trip_updates
# El denominador sale del snapshot `circulation`/`circulation_stop`
# (programación capturada e inmutable una vez cerrado el día), acotado
# al inicio efectivo de captura tipificada por (feed, fuente).

# Gate estadístico reproducible — los umbrales son públicos y se devuelven
# en la propia respuesta para que cualquiera pueda reproducir el juicio.
STATS_GATE = {
    "descriptive": {"min_instances": 30, "min_days": 2,
                    "min_coverage_pct": 40.0},
    "comparative": {"min_instances": 100, "min_days": 5,
                    "min_coverage_pct": 70.0, "min_units": 2},
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
    "real observada.")


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
    return datetime.fromtimestamp(ts, _tz()).date() if ts else None


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


def _nucleo_slugs() -> dict:
    """slug -> nombre oficial de núcleo (geo_station, fuente verificable)."""
    try:
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT DISTINCT nucleo FROM geo_station"
                " WHERE nucleo IS NOT NULL")).scalars().all()
    except Exception:
        return {}
    return {_tslug(n): n for n in rows}


def _terr_codes(ccaa, provincia):
    """slug ccaa/provincia -> (ccaa_code, nombre provincia) o error."""
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
        nucs = _nucleo_slugs()
        if nucleo not in nucs:
            return None, {"error": f"núcleo desconocido: {nucleo}"}, None
        p["nuc"] = nucs[nucleo]
        where.append("""EXISTS (SELECT 1 FROM route_core rc
            WHERE rc.feed=c.feed AND rc.route_id=c.route_id
              AND rc.nucleo=:nuc)""")
    if line:
        p["line"] = line
        where.append("""EXISTS (SELECT 1 FROM routes r
            WHERE r.feed=c.feed AND r.route_id=c.route_id
              AND r.short_name=:line)""")
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


@stats_router.get("/stats/options")
def stats_options():
    """Ámbitos filtrables verificables para /stats/delays."""
    nucs = _nucleo_slugs()
    with engine.connect() as c:
        lines = c.execute(text("""
            SELECT rc.nucleo, r.short_name, count(DISTINCT r.route_id) AS n
            FROM route_core rc
            JOIN routes r ON r.feed=rc.feed AND r.route_id=rc.route_id
            WHERE rc.nucleo IS NOT NULL
            GROUP BY rc.nucleo, r.short_name ORDER BY 1, 2""")).mappings().all()
        st_per_nuc = {r["nucleo"]: r["count"] for r in c.execute(text(
            "SELECT nucleo, count(*) FROM geo_station"
            " WHERE nucleo IS NOT NULL AND active=1"
            " GROUP BY nucleo")).mappings()}
        ld_lines = c.execute(text(
            "SELECT DISTINCT short_name FROM routes WHERE feed='ld'"
            " AND short_name IS NOT NULL AND short_name != ''"
            " ORDER BY 1")).scalars().all()
    nuc_lines: dict = {}
    for r in lines:
        nuc_lines.setdefault(r["nucleo"], []).append(
            {"line": r["short_name"], "routes": r["n"]})
    nucleos = [{"slug": _tslug(n), "name": n,
                "stations": st_per_nuc.get(n, 0),
                "lines": nuc_lines.get(n, [])}
               for n in nucs.values()]
    nucleos.sort(key=lambda x: -x["stations"])
    return {"nucleos": nucleos, "ld_lines": ld_lines,
            "gate": STATS_GATE, "semantics": _STATS_SEMANTICS}


def _delay_stats(feed, nucleo, line, ccaa, provincia, station,
                 frm, to, d0, d1, h0, h1):
    """Núcleo del cálculo: denominador por día + última obs por instancia
    y kind. Devuelve dict para la respuesta o lanza HTTPException."""
    where, p, obs_conds = _stats_scope(feed, nucleo, line, ccaa, provincia,
                                       station, frm, to, h0, h1)
    if where is None:
        raise HTTPException(400, p.get("error", "filtros inválidos"))
    p["d0"], p["d1"] = d0, d1
    caps = _capture_starts()
    feeds_scope = [feed] if feed else ["cer", "ld"]
    # día de captura por (feed, kind); la fuente de 'reported' es fleet
    capd = {f: {"reported": _cap_day(f, "fleet", caps),
                "prediction": _cap_day(f, "trip_update", caps)}
            for f in feeds_scope}
    stop_cond = ("AND (" + " AND ".join(obs_conds) + ")") if obs_conds else ""
    with engine.connect() as c:
        sched_rows = c.execute(text(f"""
            SELECT c.feed, c.day, count(DISTINCT c.trip_id) AS n
            FROM circulation c WHERE {where}
            GROUP BY c.feed, c.day ORDER BY c.day"""), p).mappings().all()
        numer = {}
        for kind in ("reported", "prediction"):
            params = dict(p)
            for f in ("cer", "ld"):          # binds siempre presentes
                params[f"capd_{f}"] = (capd.get(f) or {}).get(kind)
            rows = c.execute(text(f"""
                SELECT DISTINCT ON (o.trip_id, o.service_date, o.feed)
                       o.feed, o.trip_id, o.service_date, o.delay
                FROM observations o
                WHERE o.kind=:k AND o.delay IS NOT NULL
                  AND o.service_date BETWEEN :d0 AND :d1
                  AND o.service_date >=
                      CASE o.feed WHEN 'cer' THEN CAST(:capd_cer AS date)
                                  ELSE CAST(:capd_ld AS date) END
                  AND (CAST(:feed AS text) IS NULL OR o.feed = :feed)
                  AND EXISTS (SELECT 1 FROM circulation c
                              WHERE c.feed=o.feed AND c.trip_id=o.trip_id
                                AND c.day=o.service_date AND {where})
                  {stop_cond}
                ORDER BY o.trip_id, o.service_date, o.feed,
                         o.observed_at DESC"""),
                {**params, "k": kind}).mappings().all()
            numer[kind] = rows
    return {"sched_rows": sched_rows, "numer": numer,
            "capd": capd, "caps": caps, "feeds_scope": feeds_scope}


def _kind_block(kind, rows, sched_rows, capd, feeds_scope, today):
    src = "fleet" if kind == "reported" else "trip_update"
    delays = sorted(int(r["delay"]) for r in rows)
    n = len(delays)
    days_obs = len({(r["feed"], r["service_date"]) for r in rows})
    def in_cap(r_feed, r_day):
        cd = capd.get(r_feed, {}).get(kind)
        return cd is None or r_day >= cd
    sched_k = sum(r["n"] for r in sched_rows if in_cap(r["feed"], r["day"]))
    closed = [r for r in sched_rows if r["day"] < today]
    sched_closed = sum(r["n"] for r in closed
                       if in_cap(r["feed"], r["day"]))
    obs_closed = sum(1 for r in rows if r["service_date"] < today
                     and in_cap(r["feed"], r["service_date"]))
    cov = round(100 * obs_closed / sched_closed, 1) if sched_closed else None
    return {
        "source": src,
        "capture_since": {f: str(capd[f][kind]) if capd[f][kind] else None
                          for f in feeds_scope},
        "with_data": n, "scheduled": sched_k,
        "scheduled_closed_days": sched_closed,
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
    ÚLTIMO retraso informado dentro del ámbito. La cobertura se mide solo
    sobre días cerrados (hasta ayer): el día en curso entra en n y en la
    distribución pero no puede juzgar aún su cobertura."""
    _, _, today = _now()
    d0 = _parse_date(date_from) or today - timedelta(days=90)
    d1 = _parse_date(date_to) or today
    if d1 < d0:
        raise HTTPException(400, "date_from debe ser <= date_to")
    h0, h1 = _parse_time(hour_from), _parse_time(hour_to)
    if (h0 is None) != (h1 is None):
        raise HTTPException(400, "hour_from y hour_to van juntos")
    if h0 is not None and h1 <= h0:
        raise HTTPException(400, "hour_to debe ser > hour_from")
    r = _delay_stats(feed, nucleo, line, ccaa, provincia, station,
                     frm, to, d0, d1, h0, h1)
    kinds = {k: _kind_block(k, r["numer"][k], r["sched_rows"],
                            r["capd"], r["feeds_scope"], today)
             for k in ("reported", "prediction")}
    # cobertura diaria para el gráfico
    obs_by_day: dict = {}
    for kind, rows in r["numer"].items():
        for row in rows:
            k = str(row["service_date"])
            obs_by_day.setdefault(k, {})[kind] =                 obs_by_day.setdefault(k, {}).get(kind, 0) + 1
    by_day = [{"day": str(x["day"]), "feed": x["feed"],
               "scheduled": x["n"],
               "with_data": obs_by_day.get(str(x["day"]), {})}
              for x in r["sched_rows"]]
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
        "gate": STATS_GATE,
        "links": links,
        "semantics": _STATS_SEMANTICS,
    }


@stats_router.get("/stats/compare")
def stats_compare(by: str = Query("line", pattern="^(line|station)$"),
                  nucleo: str | None = None,
                  feed: str | None = Query(None, pattern="^(cer|ld)$"),
                  ccaa: str | None = None, provincia: str | None = None,
                  date_from: str | None = None, date_to: str | None = None,
                  hour_from: str | None = None, hour_to: str | None = None):
    """Comparativa por unidad (líneas de un núcleo, o estaciones de un
    territorio). Solo se habilita si >= min_units unidades superan el gate
    comparativo; si no, responde enabled=false y las filas marcadas —
    nunca compara muestras indefendibles."""
    units: list[tuple[str, str]] = []
    if by == "line":
        if not nucleo and feed != "ld":
            raise HTTPException(400, "comparar líneas requiere nucleo (cer) "
                                     "o feed=ld")
        p: dict = {}
        nuc_cond = ""
        if nucleo:
            nucs = _nucleo_slugs()
            if nucleo not in nucs:
                raise HTTPException(400, f"núcleo desconocido: {nucleo}")
            p["nuc"] = nucs[nucleo]
            nuc_cond = "AND rc.nucleo = :nuc"
        if feed == "ld":
            sql = ("SELECT DISTINCT short_name FROM routes WHERE feed='ld'"
                   " AND short_name IS NOT NULL AND short_name != ''"
                   " ORDER BY 1")
        else:
            sql = f"""SELECT DISTINCT r.short_name
                FROM route_core rc
                JOIN routes r ON r.feed=rc.feed AND r.route_id=rc.route_id
                WHERE rc.feed='cer' {nuc_cond} ORDER BY 1"""
        with engine.connect() as c:
            units = [(s, s) for s in c.execute(text(sql), p).scalars()]
    else:
        cc, pv = _terr_codes(ccaa, provincia)
        with engine.connect() as c:
            rows = c.execute(text("""
                SELECT s.feed, s.stop_id, s.name FROM geo_station g
                JOIN stops s ON s.feed=g.feed AND s.stop_id=g.stop_id
                WHERE g.active=1
                  AND (:cc IS NULL OR g.ccaa_code=:cc)
                  AND (:pv IS NULL OR g.provincia=:pv)
                  AND (:nuc IS NULL OR g.nucleo=:nuc)
                ORDER BY s.name LIMIT 40"""),
                {"cc": cc, "pv": pv,
                 "nuc": _nucleo_slugs().get(nucleo) if nucleo else None}
            ).mappings().all()
        units = [(f"{r['feed']}:{r['stop_id']}", r["name"]) for r in rows]
    _, _, today = _now()
    d0 = _parse_date(date_from) or today - timedelta(days=90)
    d1 = _parse_date(date_to) or today
    h0, h1 = _parse_time(hour_from), _parse_time(hour_to)
    out_units = []
    for val, label in units:
        r = _delay_stats(feed=feed, nucleo=nucleo,
                         line=val if by == "line" else None,
                         ccaa=ccaa, provincia=provincia,
                         station=val if by == "station" else None,
                         frm=None, to=None, d0=d0, d1=d1,
                         h0=h0, h1=h1)
        k = _kind_block("reported", r["numer"]["reported"],
                        r["sched_rows"], r["capd"], r["feeds_scope"], today)
        out_units.append({
            "unit": val, "label": label,
            "with_data": k["with_data"], "scheduled": k["scheduled"],
            "coverage_pct": k["coverage_pct"],
            "delay_median_sec": k["delay_median_sec"],
            "delay_p90_sec": k["delay_p90_sec"],
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
        "semantics": _STATS_SEMANTICS,
    }


