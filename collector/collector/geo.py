"""Conciliación territorial de paradas GTFS con el catálogo oficial Renfe.

Fuentes oficiales (CC-BY 4.0, data.renfe.com):
- Estaciones. Listado completo (ancho ibérico+internacional)
- Estaciones FEVE (ancho métrico)
- Listado AV/LD/MD
- Catálogos por núcleo de Cercanías (Madrid, Málaga, Murcia/Alicante,
  San Sebastián, Sevilla, Valencia, Zaragoza)

Tiers de conciliación (conservadores, auditables):
  T1 catalogo        join exacto stops.stop_id == CÓDIGO catálogo
  T2 geo_inferida    provincia del vecino catalogado más cercano (<15 km);
                     solo se hereda provincia/CCAA, nunca la población
                     (la localidad se deduce del nombre sólo si hay
                     coincidencia textual con la población del vecino < 3 km)
  sin clasificar     queda a NULL, visible en el informe de cobertura

La tabla geo_station NO se vacía al recargar el GTFS: las paradas que
desaparecen pasan a active=0 conservando su clasificación.
"""
import csv
import io
import logging
import math
import unicodedata

import httpx
from sqlalchemy import text

from collector.db import engine, set_meta
from collector.geo_spain import PROVINCES, province_code

log = logging.getLogger("geo")

CATALOGS = {
    "renfe_completo": "https://ssl.renfe.com/ftransit/Fichero_estaciones/estaciones.csv",
    "renfe_avld": "https://data.renfe.com/dataset/1146f3f1-e06d-477c-8f74-84f8d0668cf9/resource/b22cd560-3a2b-45dd-a25d-2406941f6fcc/download/listado_completo_av_ld_md.csv",
    "feve": "https://data.renfe.com/dataset/f72abe7d-fea8-4af1-9e3d-cf0997b81955/resource/4de015e6-987c-43ae-9350-8bf941ef66c7/download/listado-de-estaciones-feve-2.csv",
    "cer_madrid": "https://data.renfe.com/dataset/0b763a1a-860d-4346-8818-765d2559248f/resource/daa68d9b-77cf-4024-890f-285d31184c5a/download/listado-estaciones-cercanias-madrid.csv",
    "cer_malaga": "https://data.renfe.com/dataset/9cd08424-f617-4adb-a230-68abdb2c8abf/resource/4b651d25-3fed-4f6a-be4d-18352d7e2aea/download/listado-estaciones-cercanias-malaga-v4.csv",
    "cer_murcia_alicante": "https://data.renfe.com/dataset/9b7b24d0-57d0-489c-bcd6-cb78e96135e1/resource/1d17b744-4c68-4578-bdc3-b51457892967/download/listado-estaciones-cercanias-murcia-alicante.csv",
    "cer_san_sebastian": "https://data.renfe.com/dataset/f3dc3712-cc1e-4b23-ada0-50562e9cf38b/resource/e8e8d882-865e-4032-8d39-d60cd0cfcdff/download/listado-estaciones-cercanias-san-sebastian.csv",
    "cer_sevilla": "https://data.renfe.com/dataset/d13549fe-f132-4633-aa50-5ed70b2f71dd/resource/ac3b4229-1c69-412f-a6de-29df913ece92/download/listado-estaciones-cercanias-sevilla.csv",
    "cer_valencia": "https://data.renfe.com/dataset/b5a039a1-4f02-4f9c-b617-a94d55779647/resource/0b1caf38-e6a9-4965-a94d-9a24c5a4ae84/download/listado-estaciones-cercanias-valencia.csv",
    "cer_zaragoza": "https://data.renfe.com/dataset/479fa2dd-76e0-4a97-ac88-b848943f9133/resource/9651ce18-9437-4274-ad04-143f1cc9e6db/download/listado-estaciones-cercanias-zaragoza_2.csv",
}

NEAR_M = 10_000        # umbral de inferencia geográfica (fallback offline)
NEAR_CHAIN_M = 8_000   # propagación a paradas ya clasificadas
GEOCODER_URL = "https://www.cartociudad.es/geocoder/api/geocoder/reverseGeocode"
GEOCODER_PAUSE = 0.2   # educado con el servicio IGN

# Núcleo de Cercanías: clasificación oficial del visor Renfe
# (NUCLEO / NOMBRE_NUCLEO / LINEAS por CODIGO_ESTACION).
NUCLEO_GEOJSON = "https://tiempo-real.renfe.com/data/estaciones.geojson"
ROUTE_CORE_MIN_SHARE = 0.8   # asignación ruta->núcleo solo si >=80% acuerdo


def fetch_nucleos() -> dict:
    """GeoJSON oficial del visor Renfe: núcleo por CODIGO_ESTACION.

    Devuelve {codigo: {"nuc_code", "nuc", "lines"}}. Los códigos de
    estación se comparten entre los feeds cer/ld (misma estación física).
    """
    try:
        r = httpx.get(NUCLEO_GEOJSON, timeout=60, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
    except Exception:
        log.exception("geojson núcleos falló")
        return {}
    out = {}
    for f in data.get("features", []):
        p = f.get("properties") or {}
        code = str(p.get("CODIGO_ESTACION") or "").strip()
        name = (p.get("NOMBRE_NUCLEO") or "").strip()
        if not code or not name:
            continue
        ent = {"nuc_code": str(p.get("NUCLEO") or "").strip(),
               "nuc": name,
               "lines": (p.get("LINEAS") or "").strip() or None}
        out[code] = ent
        if code.isdigit() and len(code) < 5:
            out[code.zfill(5)] = ent  # GTFS usa códigos de 5 dígitos
    return out


def apply_nucleos(conn, nuc: dict) -> dict:
    """Escribe el núcleo oficial en geo_station y el núcleo mayoritario
    verificable de cada ruta CER en route_core.

    route_core solo se asigna si el >=80% de las paradas clasificadas de
    la ruta coinciden en el mismo núcleo; si no, queda NULL (ambiguo)."""
    if not nuc:
        return {"nucleos": 0}
    conn.execute(text(
        "UPDATE geo_station SET nucleo_code=NULL, nucleo=NULL, lineas=NULL"))
    conn.execute(text("""
        UPDATE geo_station SET nucleo_code=:nc, nucleo=:n, lineas=:l
        WHERE stop_id=:code"""),
        [{"code": c, "nc": v["nuc_code"], "n": v["nuc"], "l": v["lines"]}
         for c, v in nuc.items()])
    n_st = conn.execute(text(
        "SELECT count(*) FROM geo_station WHERE nucleo IS NOT NULL")).scalar()

    # ruta -> núcleo por mayoría de sus paradas clasificadas (CER únicamente:
    # una ruta LD que pisa Atocha no pertenece al núcleo de Madrid)
    # pares (ruta, parada) distintos — la evidencia son paradas, no viajes
    votes = conn.execute(text("""
        SELECT u.route_id, g.nucleo, g.nucleo_code, count(*) AS n
        FROM (SELECT DISTINCT t.route_id, st.stop_id
              FROM trips t JOIN stop_times st
                ON st.feed=t.feed AND st.trip_id=t.trip_id
              WHERE t.feed='cer') u
        JOIN geo_station g ON g.feed='cer' AND g.stop_id=u.stop_id
                          AND g.nucleo IS NOT NULL
        GROUP BY u.route_id, g.nucleo, g.nucleo_code""")).all()
    totals = dict(conn.execute(text("""
        SELECT route_id, count(*) FROM (
            SELECT DISTINCT t.route_id, st.stop_id
            FROM trips t JOIN stop_times st
              ON st.feed=t.feed AND st.trip_id=t.trip_id
            WHERE t.feed='cer') u GROUP BY route_id""")).all())
    by_route: dict = {}
    for rid, nuc_name, nuc_code, n in votes:
        by_route.setdefault(rid, []).append((n, nuc_name, nuc_code))
    conn.execute(text("DELETE FROM route_core"))
    rows = []
    now = int(__import__("time").time())
    for rid, lst in by_route.items():
        lst.sort(reverse=True)
        matched_total = sum(n for n, _, _ in lst)
        top_n, top_nuc, top_code = lst[0]
        share = top_n / matched_total
        rows.append({"rid": rid,
                     "nc": top_code if share >= ROUTE_CORE_MIN_SHARE else None,
                     "n": top_nuc if share >= ROUTE_CORE_MIN_SHARE else None,
                     "share": round(share, 3), "m": top_n,
                     "t": totals.get(rid, matched_total), "now": now})
    # rutas sin parada clasificada: fila explícita con nucleo NULL
    for rid in set(totals) - set(by_route):
        rows.append({"rid": rid, "nc": None, "n": None, "share": None,
                     "m": 0, "t": totals[rid], "now": now})
    if rows:
        conn.execute(text("""
            INSERT INTO route_core (feed, route_id, nucleo_code, nucleo,
                                    share, matched, total, updated_at)
            VALUES ('cer', :rid, :nc, :n, :share, :m, :t, :now)"""), rows)
    return {"stops_nucleo": n_st, "routes": len(rows),
            "routes_con_nucleo": sum(1 for r in rows if r["n"])}


def _cartociudad(lat: float, lon: float) -> dict | None:
    """Geocodificador inverso oficial IGN. Devuelve cpro/ccaa/muni o None."""
    try:
        r = httpx.get(GEOCODER_URL,
                      params={"lat": lat, "lon": lon}, timeout=15)
        if not r.is_success:
            return None
        d = r.json()
        prov = (d.get("provinceCode") or "").zfill(2)
        ccaa = (d.get("comunidadAutonomaCode") or "").zfill(2)
        if prov not in PROVINCES:
            return None
        return {"cpro": prov, "muni": d.get("muni"), "muni_code": d.get("muniCode"),
                "ccaa_code": ccaa}
    except Exception:
        return None


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def _hav(a: float, b: float, c: float, d: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def _load_csv(raw: bytes) -> list[list[str]]:
    for enc in ("utf-8-sig", "iso-8859-1"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = [r for r in csv.reader(io.StringIO(txt), delimiter=";",
                                  quotechar='"') if r and r[0].strip()]
    return rows[1:]


def fetch_catalogs() -> dict:
    """Devuelve {codigo: {desc, pob, prov_name, lat, lon, src}}.
    El primer catálogo con la entrada gana (orden del dict)."""
    cat: dict = {}
    for src, url in CATALOGS.items():
        try:
            r = httpx.get(url, timeout=60, follow_redirects=True)
            r.raise_for_status()
            for row in _load_csv(r.content):
                code = row[0].strip()
                if not code or code in cat:
                    continue
                try:
                    lat = float((row[2] or "").replace(",", "."))
                    lon = float((row[3] or "").replace(",", "."))
                except (ValueError, IndexError):
                    lat = lon = None
                cat[code] = {
                    "desc": row[1].strip() if len(row) > 1 else "",
                    "pob": row[6].strip() if len(row) > 6 else "",
                    "prov_name": row[7].strip() if len(row) > 7 else "",
                    "lat": lat, "lon": lon, "src": src,
                }
            log.info("catálogo %s: %d entradas (total %d)", src,
                     len([c for c in cat.values() if c["src"] == src]), len(cat))
        except Exception:
            log.exception("catálogo %s falló", src)
    return cat


def _resolved(row: dict) -> tuple:
    """(cpro, provincia, ccaa_code, ccaa) o (None,)x4 si provincia no INE."""
    cpro = province_code(row.get("prov_name") or "")
    if not cpro:
        return None, None, None, None
    pname, ccaa_code, ccaa = PROVINCES[cpro]
    return cpro, pname, ccaa_code, ccaa


def reconcile(conn, stops: list[dict], cat: dict) -> dict:
    """Clasifica las paradas y hace upsert en geo_station.

    stops: [{feed, stop_id, name, lat, lon}] actuales del GTFS.
    Devuelve métricas de cobertura.
    """
    resolved = {}
    for st in stops:
        code = st["stop_id"].strip()
        ent = cat.get(code)
        if ent:
            cpro, prov, ccode, ccaa = _resolved(ent)
            resolved[(st["feed"], st["stop_id"])] = {
                "cpro": cpro, "provincia": prov, "ccaa_code": ccode,
                "ccaa": ccaa, "poblacion": ent["pob"] or None,
                "source": "catalogo", "matched_code": code, "dist_m": None,
                "_lat": ent["lat"], "_lon": ent["lon"],
            }

    # T2: geocodificador oficial IGN (Cartociudad) sobre coordenadas.
    # Autoritativo en provincia/municipio; deduplicado por rejilla de 100 m
    # (las paradas cer/ld de la misma estación comparten coordenadas).
    import time as _time
    geo_cache: dict = {}
    for st in stops:
        k = (st["feed"], st["stop_id"])
        if k in resolved or st["lat"] is None or st["lon"] is None:
            continue
        cell = (round(st["lat"], 3), round(st["lon"], 3))
        if cell not in geo_cache:
            geo_cache[cell] = _cartociudad(st["lat"], st["lon"])
            _time.sleep(GEOCODER_PAUSE)
        g = geo_cache[cell]
        if not g:
            continue
        pname, ccaa_code, ccaa = PROVINCES[g["cpro"]]
        resolved[k] = {
            "cpro": g["cpro"], "provincia": pname,
            "ccaa_code": ccaa_code or PROVINCES[g["cpro"]][1],
            "ccaa": ccaa, "poblacion": g.get("muni"),
            "source": "cartociudad", "matched_code": None, "dist_m": None,
        }

    # T3: coincidencia por población del catálogo. Solo si el nombre de la
    # parada coincide íntegramente con una población catalogada única en
    # provincia (evita 'Villabona de Asturias' -> Gipuzkoa).
    pob_prov: dict = {}
    for ent in cat.values():
        p, prov = _norm(ent.get("pob") or ""), ent.get("prov_name") or ""
        if p and prov:
            pob_prov.setdefault(p, set()).add(province_code(prov))
    for st in stops:
        k = (st["feed"], st["stop_id"])
        if k in resolved:
            continue
        cands = pob_prov.get(_norm(st["name"]))
        if cands and len(cands) == 1 and None not in cands:
            cpro = next(iter(cands))
            pname, ccode, ccaa = PROVINCES[cpro]
            resolved[k] = {
                "cpro": cpro, "provincia": pname, "ccaa_code": ccode,
                "ccaa": ccaa, "poblacion": st["name"],
                "source": "catalogo_nombre", "matched_code": None,
                "dist_m": None,
            }

    # T3: vecino catalogado más cercano (< NEAR_M) con guarda de frontera:
    # si el segundo más cercano es de otra provincia y a <1.5x de distancia,
    # la posición es ambigua -> sin clasificar.
    pending = [s for s in stops
               if (s["feed"], s["stop_id"]) not in resolved]
    anchors = [(k, v) for k, v in resolved.items()
               if v.get("_lat") is not None and v.get("_lon") is not None]
    for st in pending:
        if st["lat"] is None or st["lon"] is None or not anchors:
            continue
        ranked = sorted(
            ((_hav(st["lat"], st["lon"], v["_lat"], v["_lon"]), k, v)
             for k, v in anchors), key=lambda x: x[0])
        dist, k, v = ranked[0]
        if dist >= NEAR_M:
            continue
        # guarda anti-frontera: si el segundo ancla CON provincia conocida
        # pertenece a otra provincia y está a <1.5x, es ambiguo.
        second = next((x for x in ranked[1:]
                       if x[2].get("cpro") and v.get("cpro")
                       and x[2]["cpro"] != v["cpro"]), None)
        if second and second[0] < dist * 1.5:
            continue  # dos provincias a distancia pareja -> ambiguo
        pob = None
        if dist < 3000 and _norm(v["poblacion"] or "") and \
                _norm(v["poblacion"]) in _norm(st["name"]):
            pob = v["poblacion"]
        resolved[(st["feed"], st["stop_id"])] = {
            "cpro": v["cpro"], "provincia": v["provincia"],
            "ccaa_code": v["ccaa_code"], "ccaa": v["ccaa"],
            "poblacion": pob, "source": "geo_inferida",
            "matched_code": k[1], "dist_m": round(dist, 1),
        }

    # cadena (punto fijo): paradas sin clasificar heredan de la parada GTFS
    # ya clasificada más cercana (<NEAR_CHAIN_M). Itera para puentear tramos
    # donde todos los vecinos directos también faltan en catálogo.
    stop_idx = {(s["feed"], s["stop_id"]): s for s in stops}
    for _pass in range(4):
        changed = False
        latlons = []
        for k, v in resolved.items():
            srow = stop_idx.get(k)
            if (v.get("cpro") and srow
                    and srow["lat"] is not None and srow["lon"] is not None):
                latlons.append((k, v, srow["lat"], srow["lon"]))
        if not latlons:
            break
        for st in [s for s in stops
                   if (s["feed"], s["stop_id"]) not in resolved]:
            if st["lat"] is None or st["lon"] is None:
                continue
            ranked = sorted(
                ((_hav(st["lat"], st["lon"], la, lo), vv)
                 for _k, vv, la, lo in latlons), key=lambda x: x[0])
            dist, best_v = ranked[0]
            # misma guarda de frontera en la cadena
            second = next((x for x in ranked[1:]
                           if x[1]["cpro"] != best_v["cpro"]), None)
            if second and second[0] < dist * 1.5:
                continue
            if dist < NEAR_CHAIN_M and best_v.get("cpro"):
                resolved[(st["feed"], st["stop_id"])] = {
                    "cpro": best_v["cpro"], "provincia": best_v["provincia"],
                    "ccaa_code": best_v["ccaa_code"], "ccaa": best_v["ccaa"],
                    "poblacion": None, "source": "geo_inferida",
                    "matched_code": best_v["matched_code"],
                    "dist_m": round(dist, 1),
                }
                changed = True
        if not changed:
            break

    # upsert persistente
    now_keys = {(s["feed"], s["stop_id"]) for s in stops}
    # desactivar los que ya no están en el GTFS
    cur = conn.execute(text("SELECT feed, stop_id FROM geo_station")).all()
    for f, s in cur:
        if (f, s) not in now_keys:
            conn.execute(text(
                "UPDATE geo_station SET active=0 WHERE feed=:f AND stop_id=:s"),
                {"f": f, "s": s})

    rows = []
    for st in stops:
        k = (st["feed"], st["stop_id"])
        r = resolved.get(k)
        rows.append({
            "feed": st["feed"], "stop_id": st["stop_id"],
            "cpro": r["cpro"] if r else None,
            "provincia": r["provincia"] if r else None,
            "ccaa_code": r["ccaa_code"] if r else None,
            "ccaa": r["ccaa"] if r else None,
            "poblacion": r["poblacion"] if r else None,
            "source": r["source"] if r else "sin_datos",
            "matched_code": r["matched_code"] if r else None,
            "dist_m": r["dist_m"] if r else None,
            "active": 1,
        })
    for r in rows:
        conn.execute(text("""
            INSERT INTO geo_station (feed, stop_id, cpro, provincia,
                ccaa_code, ccaa, poblacion, source, matched_code, dist_m, active)
            VALUES (:feed,:stop_id,:cpro,:provincia,:ccaa_code,:ccaa,
                :poblacion,:source,:matched_code,:dist_m,1)
            ON CONFLICT (feed, stop_id) DO UPDATE SET
                cpro=EXCLUDED.cpro, provincia=EXCLUDED.provincia,
                ccaa_code=EXCLUDED.ccaa_code, ccaa=EXCLUDED.ccaa,
                poblacion=EXCLUDED.poblacion, source=EXCLUDED.source,
                matched_code=EXCLUDED.matched_code, dist_m=EXCLUDED.dist_m,
                active=1
        """), r)

    stats = {
        "total": len(stops),
        "catalogo": sum(1 for r in rows if r["source"] == "catalogo"),
        "geo_inferida": sum(1 for r in rows if r["source"] == "geo_inferida"),
        "sin_clasificar": sum(1 for r in rows if r["source"] == "sin_datos"),
        "sin_provincia": sum(1 for r in rows if not r["cpro"]),
    }
    return stats


def run_geo() -> dict:
    """Descarga catálogos y reconcilia contra las paradas actuales.
    Idempotente y tolerante a fallos de descarga."""
    cat = fetch_catalogs()
    if not cat:
        return {"error": "sin catálogos"}
    nuc = fetch_nucleos()
    with engine.begin() as conn:
        stops = [dict(r) for r in conn.execute(text(
            "SELECT feed, stop_id, name, lat, lon FROM stops")).mappings()]
        stats = reconcile(conn, stops, cat)
        if nuc:
            stats.update(apply_nucleos(conn, nuc))
            set_meta(conn, "nucleo_stats",
                     {k: stats[k] for k in
                      ("stops_nucleo", "routes_con_nucleo")})
        set_meta(conn, "geo_run_ts", __import__("time").time())
        set_meta(conn, "geo_stats", stats)
    return stats
