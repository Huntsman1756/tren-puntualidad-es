"""Piloto aislado: contraste de datos ADIF/RadarDeTrenes con nuestro modelo.

Solo lectura. No escribe en BD ni toca producción. Para cada estación de
prueba descarga `board-renfe` de radardetrenes.com y lo cruza con nuestro
GTFS (stop_times/trips/service_days) + RT (rt_trip/rt_stop_update).

Uso:
    DATABASE_URL=postgresql://renfe:***@localhost:5433/renfe \
        python scripts/pilot_adif.py

Salida: pilot_tmp/pilot_report.json + resumen por consola.
"""

import collections
import datetime as dt
import json
import os
import sys

import httpx
import psycopg

RADAR = "https://radardetrenes.com/api/v1"
UA = {"User-Agent": "trenes-h1756-pilot/0.1 (https://trenes.h1756.es)"}
STATIONS = {  # codigo Renfe/ADIF -> nombre
    "60000": "Madrid Pta. Atocha",
    "17000": "Madrid Chamartín",
    "71801": "Barcelona Sants",
    "04040": "Zaragoza Delicias",
    "03216": "Valencia Joaquín Sorolla",
}
TZ = dt.timezone(dt.timedelta(hours=2), name="CEST")  # hora local estación
DB_URL = os.environ.get("DATABASE_URL", "postgresql://renfe:change_me_strong@localhost:5433/renfe")

HTTP = httpx.Client(timeout=25, headers=UA)


def radar(path: str):
    r = HTTP.get(f"{RADAR}{path}")
    r.raise_for_status()
    return r.json(), dict(r.headers)


def local_midnight(day: dt.date) -> int:
    return int(dt.datetime(day.year, day.month, day.day, tzinfo=TZ).timestamp())


def ours_board(conn, station: str, day: dt.date):
    """Servicios programados + RT en `station` para el día local `day`.

    Devuelve {train_number: [rows]}; cada fila = (sched_epoch_dep,
    sched_epoch_arr, delay_sec_best, source, trip_id, feed).
    """
    mid = local_midnight(day)
    rows = conn.execute(
        """
        SELECT st.dep, st.arr, t.train_number, t.trip_id, st.stop_id,
               rt.delay AS trip_delay, su.delay AS stop_delay, su.time AS stop_time
        FROM stop_times st
        JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
        JOIN service_days sd ON sd.feed=t.feed AND sd.service_id=t.service_id
                            AND sd.day=%(day)s
        LEFT JOIN rt_trip rt ON rt.feed=t.feed AND rt.trip_id=t.trip_id
        LEFT JOIN rt_stop_update su ON su.feed=t.feed AND su.trip_id=t.trip_id
                                   AND su.stop_id=st.stop_id
        WHERE st.stop_id=%(s)s
        ORDER BY st.dep
    """,
        {"day": day, "s": station},
    ).fetchall()
    out = collections.defaultdict(list)
    for dep, arr, tn, tid, sid, td, sd_, st_ in rows:
        if tn is None:
            continue
        delay = sd_ if sd_ is not None else td
        out[tn.strip()].append(
            {
                "dep": mid + dep if dep is not None else None,
                "arr": mid + arr if arr is not None else None,
                "delay": delay,
                "stop_time": st_,
                "trip_id": tid,
                "stop_id": sid,
            }
        )
    return out


def main():
    today = dt.datetime.now(TZ).date()
    report = {"generated": dt.datetime.now(dt.UTC).isoformat(), "stations": {}}
    conn = psycopg.connect(DB_URL)

    for code, name in STATIONS.items():
        try:
            (board, hdrs) = radar(f"/stations/{code}/board-renfe")
        except Exception as e:
            report["stations"][code] = {"error": str(e)}
            continue
        items = [("dep", x) for x in board.get("departures") or []] + [
            ("arr", x) for x in board.get("arrivals") or []
        ]
        ours = ours_board(conn, code, today)  # cubre ambos feeds (mismo stop_id)

        res = {
            "name": name,
            "n_items": len(items),
            "matched": 0,
            "time_ok": 0,
            "time_diff": [],
            "delay_diff": [],
            "unmatched_trains": [],
            "operators": collections.Counter(),
            "products": collections.Counter(),
            "fields": set(),
            "extra": {"timeType": collections.Counter(), "circulationState": collections.Counter()},
            "freshness": {"updatedAt": board.get("updatedAt"), "cache_control": hdrs.get("cache-control")},
        }
        for kind, it in items:
            tn = (it.get("trainCode") or "").strip()
            pt = (it.get("plannedTime") or 0) / 1000
            delay_r = it.get("delayMinutes")
            res["operators"][it.get("operator")] += 1
            res["products"][it.get("product")] += 1
            res["extra"]["timeType"][it.get("timeType")] += 1
            res["extra"]["circulationState"][it.get("circulationState") or "∅"] += 1
            res["fields"].update(it.keys())

            cands = ours.get(tn)
            if not cands:
                res["unmatched_trains"].append(tn)
                continue
            res["matched"] += 1
            # mejor candidato: el que tiene la hora programada más cercana
            best, bd = None, None
            for c in cands:
                ref = c["dep"] if kind == "dep" else c["arr"]
                if ref is None:
                    continue
                d = abs(ref - pt)
                if bd is None or d < bd:
                    best, bd = c, d
            if best is None:
                continue
            res["time_diff"].append(bd)
            if bd <= 120:
                res["time_ok"] += 1
            if delay_r is not None and best["delay"] is not None:
                res["delay_diff"].append(best["delay"] - delay_r * 60)

        for k in ("operators", "products"):
            res[k] = dict(res[k])
        for k in res["extra"]:
            res["extra"][k] = dict(res["extra"][k])
        res["fields"] = sorted(res["fields"])
        td = res.pop("time_diff")
        dd = res.pop("delay_diff")
        res["time_diff_stats"] = {
            "n": len(td),
            "max": max(td) if td else None,
            "median": sorted(td)[len(td) // 2] if td else None,
            "exact": sum(1 for x in td if x == 0),
        }
        res["delay_diff_stats"] = {
            "n": len(dd),
            "n_equal": sum(1 for x in dd if abs(x) <= 30),
            "n_conflict": sum(1 for x in dd if abs(x) > 60),
            "examples": sorted(dd, key=abs, reverse=True)[:8],
        }
        res["unmatched_trains"] = sorted(set(res["unmatched_trains"]))
        report["stations"][code] = res

    with open("pilot_tmp/pilot_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)

    for code, r in report["stations"].items():
        if "error" in r:
            print(f"{code} ERROR {r['error']}")
            continue
        print(
            f"\n== {code} {r['name']} ==  items={r['n_items']} matched={r['matched']} time_ok={r['time_ok']}"
        )
        print("  ops:", r["operators"], "| prods:", r["products"])
        print("  timeType:", r["extra"]["timeType"])
        print("  time_diff:", r["time_diff_stats"])
        print("  delay_diff:", r["delay_diff_stats"])
        print("  unmatched:", r["unmatched_trains"][:10])
        print("  freshness:", r["freshness"])
    print("\n-> pilot_tmp/pilot_report.json")


if __name__ == "__main__":
    sys.exit(main())
