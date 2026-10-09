"""Mapa por núcleo: trazados validados, estaciones y posiciones publicadas.

- Trazados: solo shapes con status='valid' (ver collector/shapes.py). Las
  incompletas se listan en `excluded` y NO se dibujan.
- Posiciones: tal como las publica Renfe (visor `flota.json` y GTFS-RT
  vehicle_positions). No interpolamos ni las llamamos GPS. Cada posición
  lleva su antigüedad: fresh ≤ 2 min, recent ≤ 10 min; más antiguas no se
  devuelven (solo se cuentan).
"""
import time

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from api.common import NUCLEO_BY_SLUG, line_info, line_routes, nucleo_public
from api.db import engine

router = APIRouter(prefix="/api/v1")

FRESH_SEC = 120
RECENT_SEC = 600
MAX_POINTS = 1500


def _thin(coords: list, max_points=MAX_POINTS) -> list:
    if len(coords) <= max_points:
        return coords
    step = len(coords) / max_points
    out = [coords[int(i * step)] for i in range(max_points)]
    out.append(coords[-1])
    return out


@router.get("/mapa/{nslug}")
def mapa(nslug: str, linea: str | None = Query(None)):
    code = NUCLEO_BY_SLUG.get(nslug)
    if not code:
        raise HTTPException(404, "núcleo desconocido")
    lr = {rid: r for (f, rid), r in line_routes().items()
          if f == "cer" and r["nucleo_code"] == code
          and (not linea or linea in (r["line_slug"], r["family_slug"]))}
    if not lr:
        raise HTTPException(404, "línea desconocida en este núcleo")
    rids = sorted(lr)
    now = int(time.time())
    with engine.connect() as c:
        shape_rows = c.execute(text("""
            SELECT t.shape_id, t.route_id, count(*) AS n
            FROM trips t WHERE t.feed='cer' AND t.route_id = ANY(:r)
              AND t.shape_id IS NOT NULL AND t.shape_id <> ''
            GROUP BY 1, 2"""), {"r": rids}).all()
        shape_route: dict = {}
        for sid, rid, n in shape_rows:
            if n > shape_route.get(sid, ("", 0))[1]:
                shape_route[sid] = (rid, n)
        sids = sorted(shape_route)
        quality = {r["shape_id"]: dict(r) for r in c.execute(text("""
            SELECT shape_id, status, share, stations, stations_near, length_m
            FROM shape_quality WHERE feed='cer' AND shape_id = ANY(:s)"""),
            {"s": sids}).mappings()} if sids else {}
        valid = [s for s in sids if quality.get(s, {}).get("status") == "valid"]
        pts: dict = {}
        if valid:
            for sid, lat, lon in c.execute(text("""
                    SELECT shape_id, lat, lon FROM shapes
                    WHERE feed='cer' AND shape_id = ANY(:s) ORDER BY shape_id, seq"""),
                    {"s": valid}):
                pts.setdefault(sid, []).append([round(lat, 5), round(lon, 5)])
        stations = [dict(r) for r in c.execute(text("""
            SELECT DISTINCT s.stop_id, s.name, s.lat, s.lon
            FROM trips t
            JOIN stop_times st ON st.feed=t.feed AND st.trip_id=t.trip_id
            JOIN stops s ON s.feed=st.feed AND s.stop_id=st.stop_id
            WHERE t.feed='cer' AND t.route_id = ANY(:r) AND s.lat IS NOT NULL"""),
            {"r": rids}).mappings()]
        fleet = c.execute(text("""
            SELECT f.trip_id, f.train_number, f.lat, f.lon, f.ts, f.delay_min,
                   f.cur_stop_id, f.next_stop_id, t.route_id
            FROM rt_fleet f JOIN trips t ON t.feed=f.feed AND t.trip_id=f.trip_id
            WHERE f.feed='cer' AND t.route_id = ANY(:r)
              AND f.lat IS NOT NULL AND f.lon IS NOT NULL"""),
            {"r": rids}).mappings().all()
        veh = c.execute(text("""
            SELECT v.trip_id, t.train_number, v.lat, v.lon, v.ts, v.status,
                   v.stop_id, t.route_id
            FROM rt_vehicle v JOIN trips t ON t.feed=v.feed AND t.trip_id=v.trip_id
            WHERE v.feed='cer' AND t.route_id = ANY(:r)
              AND v.lat IS NOT NULL AND v.lon IS NOT NULL"""),
            {"r": rids}).mappings().all()
    # una posición por tren: la más reciente entre las dos fuentes
    best: dict = {}
    for src, rows in (("visor", fleet), ("gtfs_rt", veh)):
        for r in rows:
            if not r["ts"]:
                continue
            cur = best.get(r["trip_id"])
            if cur is None or r["ts"] > cur["ts"]:
                best[r["trip_id"]] = {**dict(r), "source": src}
    trains, stale = [], 0
    for tid, r in best.items():
        age = max(0, now - int(r["ts"]))
        if age > RECENT_SEC:
            stale += 1
            continue
        dm = r.get("delay_min")
        trains.append({
            "trip_id": tid, "train_number": r["train_number"],
            "lat": r["lat"], "lon": r["lon"], "ts": r["ts"], "age_sec": age,
            "freshness": "fresh" if age <= FRESH_SEC else "recent",
            "source": r["source"],
            "delay_sec": dm * 60 if dm is not None and -60 <= dm <= 600 else None,
            "line_info": line_info("cer", r["route_id"]),
        })
    shapes = []
    excluded = []
    for sid in sids:
        rid = shape_route[sid][0]
        li = line_info("cer", rid)
        q = quality.get(sid) or {"status": "sin_control"}
        if sid in pts:
            shapes.append({"shape_id": sid, "line": li["code"] if li else None,
                           "color": lr[rid]["color"], "coords": _thin(pts[sid])})
        else:
            excluded.append({"shape_id": sid, "line": li["code"] if li else None,
                             "status": q.get("status"), "share": q.get("share")})
    return {
        "nucleo": nucleo_public(code), "linea": linea, "generated_at": now,
        "shapes": shapes, "excluded_shapes": excluded,
        "stations": [{**s, "key": f"cer:{s['stop_id']}"} for s in stations],
        "trains": sorted(trains, key=lambda t: t["age_sec"]),
        "stale_hidden": stale,
        "rules": {"fresh_sec": FRESH_SEC, "recent_sec": RECENT_SEC,
                  "shape_min_share": 0.9, "shape_max_dist_m": 300},
        "attribution": "Renfe Operadora (GTFS/GTFS-RT, visor tiempo-real), CC-BY 4.0",
    }
