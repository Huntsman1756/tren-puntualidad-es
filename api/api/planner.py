"""Planificador con un único transbordo.

Modelo: origen → T1 → intercambiador X → (enlace) → Y → T2 → destino.

Fuentes de enlaces entre paradas, por orden de confianza:
1. `transfer_link` — catálogo curado de enlaces verificados entre
   estaciones físicas distintas (Atocha Cercanías↔Puerta de Atocha, etc.).
   Nunca inferidos por proximidad.
2. `gtfs_transfer` — transfers.txt oficial (solo CER; tipo 3 = prohibido).
3. Mismo stop_id: si existe en ambos feeds comparte estación física.
   Mismo (feed, stop): transbordo trivial dentro de la misma red.

Slack mínimo por defecto cuando no hay `min_transfer_time`:
- cer→cer misma parada: 5 min   (andén a andén)
- ld→ld misma parada:   15 min  (control de acceso AV/LD)
- cer→ld misma parada:  15 min
- ld→cer misma parada:  10 min

El tiempo real (si el día consultado es hoy) ajusta la llegada del primer
tramo y la salida del segundo; una conexión programada válida pero con RT
desfavorable se etiqueta `risky` y nunca desaparece silenciosamente.
"""

import time
from collections import defaultdict

from sqlalchemy import text

from api.common import line_info, local_midnight
from api.db import engine

SLACK = {("cer", "cer"): 300, ("ld", "ld"): 900, ("cer", "ld"): 900, ("ld", "cer"): 600}
LEG1_LIMIT = 40  # primeros trenes en salir del origen
MAX_DOWNSTREAM = 60  # paradas posteriores de T1 consideradas
RESULT_LIMIT = 20

_edges_cache: dict = {"ts": 0.0, "links": {}, "gtfs": {}}


def reset_cache():
    _edges_cache["ts"] = 0.0


def _edges():
    """Enlaces dirigidos: {from_key: [edge]} y transfers.txt aparte
    (puede llevar filtros de route/trip evaluados por viaje)."""
    if time.time() - _edges_cache["ts"] < 300:
        return _edges_cache
    links, gtfs = defaultdict(list), defaultdict(list)
    try:
        with engine.connect() as c:
            for r in c.execute(
                text(
                    "SELECT from_feed,from_stop_id,to_feed,to_stop_id,min_secs,kind,label FROM transfer_link"
                )
            ):
                links[(r[0], r[1])].append({"to": (r[2], r[3]), "slack": r[4], "kind": r[5], "label": r[6]})
            for r in c.execute(
                text(
                    "SELECT feed,from_stop_id,to_stop_id,from_route_id,"
                    "to_route_id,from_trip_id,to_trip_id,transfer_type,"
                    "min_transfer_time FROM gtfs_transfer"
                )
            ):
                gtfs[(r[0], r[1])].append(
                    {
                        "to_stop": r[2],
                        "from_route": r[3],
                        "to_route": r[4],
                        "from_trip": r[5],
                        "to_trip": r[6],
                        "ttype": r[7],
                        "min_secs": r[8],
                    }
                )
    except Exception:
        pass  # tablas aún no creadas: se trabaja solo con misma parada
    _edges_cache.update(ts=time.time(), links=links, gtfs=gtfs)
    return _edges_cache


def _targets(feed, stop_id, t1_route, t1_trip):
    """Paradas alcanzables desde (feed,stop_id) con su slack y evidencia."""
    out = [{"to": (feed, stop_id), "slack": SLACK[(feed, feed)], "kind": "same_stop", "label": None}]
    other = "ld" if feed == "cer" else "cer"
    out.append({"to": (other, stop_id), "slack": SLACK[(feed, other)], "kind": "same_station", "label": None})
    out += _edges()["links"].get((feed, stop_id), [])
    for g in _edges()["gtfs"].get((feed, stop_id), []):
        if g["ttype"] == 3:
            continue  # transbordo prohibido por el operador
        if g["from_route"] and g["from_route"] != t1_route:
            continue
        if g["from_trip"] and g["from_trip"] != t1_trip:
            continue
        out.append(
            {
                "to": (feed, g["to_stop"]),
                "slack": g["min_secs"] if g["min_secs"] is not None else SLACK[(feed, feed)],
                "kind": "gtfs_transfer",
                "to_route": g["to_route"] or None,
                "to_trip": g["to_trip"] or None,
                "label": None,
            }
        )
    return out


def _rt_delays(trip_keys, stop_ids):
    """Delay por (feed,trip_id,stop_id) y fallback por viaje."""
    per_stop, per_trip = {}, {}
    if not trip_keys:
        return per_stop, per_trip
    feeds = sorted({f for f, _ in trip_keys})
    tids = sorted({t for _, t in trip_keys})
    with engine.connect() as c:
        for r in c.execute(
            text(
                "SELECT feed, trip_id, stop_id, delay FROM rt_stop_update "
                "WHERE feed=ANY(:f) AND trip_id=ANY(:t) AND stop_id=ANY(:s)"
            ),
            {"f": feeds, "t": tids, "s": list(stop_ids)},
        ):
            per_stop[(r[0], r[1], r[2])] = r[3]
        for r in c.execute(
            text(
                "SELECT feed, trip_id, delay, sched_rel FROM rt_trip WHERE feed=ANY(:f) AND trip_id=ANY(:t)"
            ),
            {"f": feeds, "t": tids},
        ):
            per_trip[(r[0], r[1])] = {"delay": r[2], "rel": r[3]}
    return per_stop, per_trip


def find_transfers(f_pairs, t_pairs, day, t0, span, is_today, limit=RESULT_LIMIT, max_wait=120 * 60):
    """Opciones origen→destino con un transbordo, ordenadas por llegada
    efectiva. f_pairs/t_pairs: [(feed, stop_id)]."""
    midnight = local_midnight(day)
    from_by_feed = defaultdict(list)
    to_by_feed = defaultdict(set)
    for f, s in f_pairs:
        from_by_feed[f].append(s)
    for f, s in t_pairs:
        to_by_feed[f].add(s)

    # ---- tramo 1: servicios que salen del origen en la ventana
    leg1 = []
    with engine.connect() as c:
        for feed, stops in from_by_feed.items():
            rows = (
                c.execute(
                    text("""
                SELECT st.stop_id AS o_stop, st.seq AS o_seq,
                       st.dep + (sd.day - :day) * 86400 AS dep_eff,
                       sd.day AS service_day,
                       t.trip_id, t.train_number, t.route_id,
                       r.short_name AS line
                FROM stop_times st
                JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
                JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
                                  AND sd.day BETWEEN :day - 1 AND :day
                LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
                WHERE st.feed=:f AND st.stop_id=ANY(:os)
                  AND st.dep + (sd.day - :day) * 86400 BETWEEN :lo AND :hi
                ORDER BY dep_eff LIMIT :lim"""),
                    {"f": feed, "os": stops, "day": day, "lo": t0, "hi": t0 + span, "lim": LEG1_LIMIT},
                )
                .mappings()
                .all()
            )
            leg1 += [dict(r, feed=feed) for r in rows]
    if not leg1:
        return []
    # un viaje puede repetirse por varias paradas de origen: nos quedamos
    # con el embarque más temprano (menor seq)
    seen1 = {}
    for lg in leg1:
        k = (lg["feed"], lg["trip_id"], lg["service_day"])
        if k not in seen1 or lg["o_seq"] < seen1[k]["o_seq"]:
            seen1[k] = lg
    leg1 = list(seen1.values())

    # ---- paradas posteriores de cada T1 (candidatos a intercambiador)
    t1_keys = sorted({(lg["feed"], lg["trip_id"]) for lg in leg1})
    o_seq_of = {(lg["feed"], lg["trip_id"]): lg["o_seq"] for lg in leg1}
    downstream = defaultdict(list)  # (feed,trip) -> [(stop,seq,arr,dep)]
    with engine.connect() as c:
        for feed in {f for f, _ in t1_keys}:
            tids = [t for f, t in t1_keys if f == feed]
            for r in c.execute(
                text(
                    "SELECT trip_id, stop_id, seq, arr, dep FROM stop_times "
                    "WHERE feed=:f AND trip_id=ANY(:t) ORDER BY trip_id, seq"
                ),
                {"f": feed, "t": tids},
            ):
                if r[2] > o_seq_of[(feed, r[0])]:
                    downstream[(feed, r[0])].append(r[1:])

    # ---- tramo 2: una query por feed destino sobre todas las Y posibles
    y_by_feed = defaultdict(set)
    edges_by_x = {}
    for lg in leg1:
        feed1, tid = lg["feed"], lg["trip_id"]
        for s in downstream.get((feed1, tid), [])[:MAX_DOWNSTREAM]:
            x = s[0]
            if (feed1, x) not in edges_by_x:
                tg = _targets(feed1, x, lg["route_id"], tid)
                edges_by_x[(feed1, x)] = tg
                for e in tg:
                    f2, y = e["to"]
                    if to_by_feed.get(f2):
                        y_by_feed[f2].add(y)

    # caminar en origen/destino: un enlace verificado puede usarse también
    # para llegar A PIE al tren (origen → Y) o bajar del tren y seguir a pie
    # hasta el destino real (X → destino)
    walk_from = []  # [(origen_pair, edge)]
    for o in f_pairs:
        for e in _targets(o[0], o[1], None, None):
            if e["kind"] == "same_stop":
                continue  # mismo (feed,stop): es un viaje directo, no enlace
            f2, y = e["to"]
            if to_by_feed.get(f2):
                y_by_feed[f2].add(y)
                walk_from.append((o, e))
    # destino como parada Y de un enlace desde algún X: índice inverso de
    # enlaces curados + misma estación / misma parada
    dest_edges = defaultdict(list)  # (feed_d,stop_d) -> [(x_feed,x,edge)]
    for f_d, s_d in t_pairs:
        other = "ld" if f_d == "cer" else "cer"
        dest_edges[(other, s_d)].append(  # misma estación, otra red
            {"to": (f_d, s_d), "slack": SLACK[(other, f_d)], "kind": "same_station", "label": None}
        )
        for (ff, fs), edges in _edges()["links"].items():
            for e in edges:
                if e["to"] == (f_d, s_d):
                    dest_edges[(ff, fs)].append(e)
        for (fg, x), gs in _edges()["gtfs"].items():
            if fg != f_d:
                continue
            for g in gs:
                if g["to_stop"] == s_d and g["ttype"] != 3:
                    dest_edges[(fg, x)].append(
                        {
                            "to": (f_d, s_d),
                            "slack": g["min_secs"] if g["min_secs"] is not None else SLACK[(f_d, f_d)],
                            "kind": "gtfs_transfer",
                            "label": None,
                        }
                    )

    leg2_by_stop = defaultdict(list)  # (feed2,y) -> [row]
    with engine.connect() as c:
        for f2, ys in y_by_feed.items():
            rows = (
                c.execute(
                    text("""
                SELECT st.stop_id AS y,
                       st.dep + (sd.day - :day) * 86400 AS dep_eff,
                       sd.day AS service_day,
                       t.trip_id, t.train_number, t.route_id,
                       r.short_name AS line,
                       d2.stop_id AS dest,
                       d2.arr + (sd.day - :day) * 86400 AS arr_eff
                FROM stop_times st
                JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
                JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
                                  AND sd.day BETWEEN :day - 1 AND :day + 1
                LEFT JOIN routes r ON r.feed=t.feed AND r.route_id=t.route_id
                JOIN stop_times d2 ON d2.feed=st.feed AND d2.trip_id=st.trip_id
                                  AND d2.seq>st.seq AND d2.stop_id=ANY(:ds)
                WHERE st.feed=:f AND st.stop_id=ANY(:ys)
                  AND st.dep + (sd.day - :day) * 86400 BETWEEN :lo AND :hi
                ORDER BY dep_eff"""),
                    {
                        "f": f2,
                        "ys": list(ys),
                        "ds": list(to_by_feed[f2]),
                        "day": day,
                        "lo": t0,
                        "hi": t0 + span + max_wait,
                    },
                )
                .mappings()
                .all()
            )
            for r in rows:
                leg2_by_stop[(f2, r["y"])].append(dict(r))

    # ---- RT de los viajes implicados (solo si se consulta hoy)
    per_stop = per_trip = {}
    if is_today:
        trips2 = {(f2, r["trip_id"]) for (f2, _), rows in leg2_by_stop.items() for r in rows}
        stops2 = {y for (_, y) in leg2_by_stop} | {x for (_, x) in edges_by_x} | {lg["o_stop"] for lg in leg1}
        per_stop, per_trip = _rt_delays(set(t1_keys) | trips2, stops2)

    def est(feed, tid, stop, base):
        """(epoch_estimada, delay|None)."""
        if not is_today:
            return base, None
        d = per_stop.get((feed, tid, stop))
        if d is None:
            d = (per_trip.get((feed, tid)) or {}).get("delay")
        return (base + d if d is not None else base), d

    def cancelled(feed, tid):
        return (per_trip.get((feed, tid)) or {}).get("rel") == "CANCELED"

    # ---- composición de enlaces
    out, seen = [], set()
    for lg1 in leg1:
        feed1, tid1, svc1 = lg1["feed"], lg1["trip_id"], lg1["service_day"]
        if cancelled(feed1, tid1):
            continue
        shift = (svc1 - day).days * 86400
        dep_o = midnight + lg1["dep_eff"]
        dep_o_est, dep_o_d = est(feed1, tid1, lg1["o_stop"], dep_o)
        for s in downstream.get((feed1, tid1), [])[:MAX_DOWNSTREAM]:
            x = s[0]
            arr_x = s[2] if s[2] is not None else s[3]  # arr, si no dep
            if arr_x is None:
                continue
            arr_x_eff = midnight + arr_x + shift
            arr_x_est, d1 = est(feed1, tid1, x, arr_x_eff)
            # llegada a X + caminar hasta el destino (X enlaza con una
            # parada del destino real)
            for e in dest_edges.get((feed1, x), []):
                f_d, s_d = e["to"]
                arr_d = arr_x_eff + e["slack"]
                arr_d_est = arr_x_est + e["slack"]
                key = (feed1, tid1, x, f_d, s_d, "walk")
                if key in seen:
                    continue
                seen.add(key)
                arr_eff = arr_d_est if is_today else arr_d
                out.append(
                    {
                        "risk": "ok",
                        "leg1": {
                            "feed": feed1,
                            "trip_id": tid1,
                            "train_number": lg1["train_number"],
                            "line": lg1["line"],
                            "line_info": line_info(feed1, lg1["route_id"], lg1["line"]),
                            "from_stop": lg1["o_stop"],
                            "dep_scheduled": dep_o,
                            "dep_estimated": dep_o_est,
                            "service_date": str(svc1),
                            "realtime": dep_o_d is not None or d1 is not None,
                        },
                        "transfer": {
                            "at_stop": x,
                            "at_feed": feed1,
                            "to_stop": s_d,
                            "to_feed": f_d,
                            "arr_scheduled": arr_x_eff,
                            "arr_estimated": arr_x_est,
                            "slack_sec": e["slack"],
                            "buffer_sec": e["slack"],
                            "buffer_rt_sec": None,
                            "kind": e["kind"],
                            "label": e.get("label"),
                        },
                        "leg2": None,
                        "dep_epoch": dep_o_est if is_today else dep_o,
                        "arr_epoch": arr_eff,
                        "duration_sec": arr_eff - dep_o,
                    }
                )
            for e in edges_by_x.get((feed1, x), []):
                f2, y = e["to"]
                for r2 in leg2_by_stop.get((f2, y), []):
                    tid2 = r2["trip_id"]
                    if (feed1, tid1) == (f2, tid2) or cancelled(f2, tid2):
                        continue
                    if e.get("to_route") and e["to_route"] != r2["route_id"]:
                        continue
                    if e.get("to_trip") and e["to_trip"] != tid2:
                        continue
                    dep2 = midnight + r2["dep_eff"]
                    dep2_est, d2d = est(f2, tid2, y, dep2)
                    arr_dest = midnight + r2["arr_eff"]
                    arr_dest_est, _ = est(f2, tid2, r2["dest"], arr_dest)
                    buffer = dep2 - arr_x_eff
                    slack = e["slack"]
                    if buffer < slack or buffer > max_wait:
                        continue
                    key = (feed1, tid1, x, f2, y, tid2)
                    if key in seen:
                        continue
                    seen.add(key)
                    buffer_rt = dep2_est - arr_x_est
                    risk = "risky" if buffer_rt < slack else "tight" if buffer < slack * 1.5 else "ok"
                    arr_eff = arr_dest_est if is_today else arr_dest
                    out.append(
                        {
                            "risk": risk,
                            "leg1": {
                                "feed": feed1,
                                "trip_id": tid1,
                                "train_number": lg1["train_number"],
                                "line": lg1["line"],
                                "line_info": line_info(feed1, lg1["route_id"], lg1["line"]),
                                "from_stop": lg1["o_stop"],
                                "dep_scheduled": dep_o,
                                "dep_estimated": dep_o_est,
                                "service_date": str(svc1),
                                "realtime": dep_o_d is not None or d1 is not None,
                            },
                            "transfer": {
                                "at_stop": x,
                                "at_feed": feed1,
                                "to_stop": y,
                                "to_feed": f2,
                                "arr_scheduled": arr_x_eff,
                                "arr_estimated": arr_x_est,
                                "slack_sec": slack,
                                "buffer_sec": buffer,
                                "buffer_rt_sec": buffer_rt if is_today else None,
                                "kind": e["kind"],
                                "label": e.get("label"),
                            },
                            "leg2": {
                                "feed": f2,
                                "trip_id": tid2,
                                "train_number": r2["train_number"],
                                "line": r2["line"],
                                "line_info": line_info(f2, r2["route_id"], r2["line"]),
                                "from_stop": y,
                                "to_stop": r2["dest"],
                                "dep_scheduled": dep2,
                                "dep_estimated": dep2_est,
                                "arr_scheduled": arr_dest,
                                "arr_estimated": arr_dest_est,
                                "service_date": str(r2["service_day"]),
                                "realtime": d2d is not None,
                            },
                            "dep_epoch": dep_o_est if is_today else dep_o,
                            "arr_epoch": arr_eff,
                            "duration_sec": arr_eff - dep_o,
                        }
                    )
    # ---- leg0: caminar desde el origen hasta una parada enlazada y coger
    # el tren allí (el enlace ES el primer tramo del viaje)
    for (f_o, s_o), e in walk_from:
        f2, y = e["to"]
        for r2 in leg2_by_stop.get((f2, y), []):
            tid2 = r2["trip_id"]
            if cancelled(f2, tid2):
                continue
            dep2 = midnight + r2["dep_eff"]
            dep2_est, d2d = est(f2, tid2, y, dep2)
            walk_start = dep2 - e["slack"]  # hora prudente de inicio
            if walk_start < t0:  # no da tiempo ni programado
                continue
            arr_dest = midnight + r2["arr_eff"]
            arr_dest_est, _ = est(f2, tid2, r2["dest"], arr_dest)
            key = ("walk0", f_o, s_o, f2, y, tid2)
            if key in seen:
                continue
            seen.add(key)
            arr_eff = arr_dest_est if is_today else arr_dest
            out.append(
                {
                    "risk": "ok",
                    "leg1": None,
                    "transfer": {
                        "at_stop": s_o,
                        "at_feed": f_o,
                        "to_stop": y,
                        "to_feed": f2,
                        "arr_scheduled": None,
                        "arr_estimated": None,
                        "dep_suggested": walk_start,
                        "slack_sec": e["slack"],
                        "buffer_sec": dep2 - walk_start,
                        "buffer_rt_sec": dep2_est - walk_start if is_today else None,
                        "kind": e["kind"],
                        "label": e.get("label"),
                    },
                    "leg2": {
                        "feed": f2,
                        "trip_id": tid2,
                        "train_number": r2["train_number"],
                        "line": r2["line"],
                        "line_info": line_info(f2, r2["route_id"], r2["line"]),
                        "from_stop": y,
                        "to_stop": r2["dest"],
                        "dep_scheduled": dep2,
                        "dep_estimated": dep2_est,
                        "arr_scheduled": arr_dest,
                        "arr_estimated": arr_dest_est,
                        "service_date": str(r2["service_day"]),
                        "realtime": d2d is not None,
                    },
                    "dep_epoch": walk_start,
                    "arr_epoch": arr_eff,
                    "duration_sec": arr_eff - walk_start,
                }
            )
    # ---- nombres de las paradas citadas en los resultados
    keys = set()
    for j in out[:limit]:
        t = j["transfer"]
        keys.add((t["at_feed"], t["at_stop"]))
        keys.add((t["to_feed"], t["to_stop"]))
        if j["leg1"]:
            keys.add((j["leg1"]["feed"], j["leg1"]["from_stop"]))
        if j["leg2"]:
            keys.add((j["leg2"]["feed"], j["leg2"]["from_stop"]))
            keys.add((j["leg2"]["feed"], j["leg2"]["to_stop"]))
    names = {}
    if keys:
        feeds = sorted({f for f, _ in keys})
        ids = sorted({s for _, s in keys})
        with engine.connect() as c:
            for r in c.execute(
                text("SELECT feed, stop_id, name FROM stops WHERE feed=ANY(:f) AND stop_id=ANY(:s)"),
                {"f": feeds, "s": ids},
            ):
                names[(r[0], r[1])] = r[2]
    for j in out[:limit]:
        t = j["transfer"]
        t["at_name"] = names.get((t["at_feed"], t["at_stop"]))
        t["to_name"] = names.get((t["to_feed"], t["to_stop"]))
        if j["leg1"]:
            j["leg1"]["from_name"] = names.get((j["leg1"]["feed"], j["leg1"]["from_stop"]))
        if j["leg2"]:
            j["leg2"]["from_name"] = names.get((j["leg2"]["feed"], j["leg2"]["from_stop"]))
            j["leg2"]["to_name"] = names.get((j["leg2"]["feed"], j["leg2"]["to_stop"]))
    out.sort(key=lambda j: (j["arr_epoch"], j["dep_epoch"]))
    return out[:limit]
