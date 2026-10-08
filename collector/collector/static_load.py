"""Descarga y carga del GTFS estático (por feed: 'cer' | 'ld')."""
import io
import logging
import shutil
import time
import zipfile
from datetime import datetime

import httpx
from sqlalchemy import text

from collector.config import GTFS_STATIC
from collector.db import engine, get_meta, set_meta
from collector.gtfsutil import (
    TZINFO,
    expand_service_days,
    extract_train_number,
    hms_to_secs,
    read_gtfs_csv,
)

log = logging.getLogger("collector.static")


def _copy_rows(conn, table, cols, rows):
    """COPY FROM STDIN vía psycopg3 para carga masiva."""
    raw = conn.connection.dbapi_connection
    with raw.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
            for r in rows:
                copy.write_row(r)


def load_feed(feed: str, path: str, conn):
    """Sustituye en una transacción todos los datos estáticos del feed."""
    today = datetime.now(TZINFO).date()
    counts = {}

    for t in ("stop_times", "service_days", "trips", "routes", "stops"):
        conn.execute(text(f"DELETE FROM {t} WHERE feed=:f"), {"f": feed})

    with zipfile.ZipFile(path) as z:
        # stops
        rows = [
            (feed, r["stop_id"], r.get("stop_name", ""), _f(r.get("stop_lat")), _f(r.get("stop_lon")))
            for r in read_gtfs_csv(z, "stops.txt") if r.get("stop_id")
        ]
        _copy_rows(conn, "stops", ["feed", "stop_id", "name", "lat", "lon"], rows)
        counts["stops"] = len(rows)

        # routes
        rows = [
            (feed, r["route_id"], r.get("route_short_name"), r.get("route_long_name"),
             _i(r.get("route_type")), r.get("route_color"), r.get("route_text_color"))
            for r in read_gtfs_csv(z, "routes.txt") if r.get("route_id")
        ]
        _copy_rows(conn, "routes",
                   ["feed", "route_id", "short_name", "long_name", "route_type", "color", "text_color"],
                   rows)
        counts["routes"] = len(rows)

        # trips
        rows = [
            (feed, r["trip_id"], r.get("route_id"), r.get("service_id"),
             r.get("trip_headsign"),
             extract_train_number(feed, r["trip_id"], r.get("trip_short_name", "")))
            for r in read_gtfs_csv(z, "trips.txt") if r.get("trip_id")
        ]
        _copy_rows(conn, "trips",
                   ["feed", "trip_id", "route_id", "service_id", "headsign", "train_number"],
                   rows)
        counts["trips"] = len(rows)

        # service_days
        cal = list(read_gtfs_csv(z, "calendar.txt"))
        cald = list(read_gtfs_csv(z, "calendar_dates.txt"))
        days = expand_service_days(cal, cald, today)
        rows = [(feed, sid, d) for sid, ds in days.items() for d in ds]
        _copy_rows(conn, "service_days", ["feed", "service_id", "day"], rows)
        counts["service_days"] = len(rows)

        # stop_times (streaming, el CER supera los 3M de filas)
        gen = (
            (feed, r["trip_id"], _i(r.get("stop_sequence")) or 0, r.get("stop_id"),
             hms_to_secs(r.get("arrival_time", "")), hms_to_secs(r.get("departure_time", "")))
            for r in read_gtfs_csv(z, "stop_times.txt") if r.get("trip_id")
        )
        n = 0
        batch = []
        for r in gen:
            batch.append(r)
            if len(batch) >= 50000:
                _copy_rows(conn, "stop_times",
                           ["feed", "trip_id", "seq", "stop_id", "arr", "dep"], batch)
                n += len(batch)
                batch.clear()
        if batch:
            _copy_rows(conn, "stop_times",
                       ["feed", "trip_id", "seq", "stop_id", "arr", "dep"], batch)
            n += len(batch)
        counts["stop_times"] = n

    conn.execute(text("ANALYZE stop_times"))
    conn.execute(text("ANALYZE trips"))
    counts["trip_flags"] = compute_trip_flags(conn, feed)
    return counts


def compute_trip_flags(conn, feed: str) -> int:
    """Marca viajes semidirectos: mismo origen/destino que el patrón modal
    de su ruta pero omitiendo >=2 paradas interiores (subsecuencia).

    No es una marca oficial: Renfe no publica CIVIS en GTFS; es una
    inferencia verificable sobre stop_times.
    """
    conn.execute(text("DELETE FROM trip_flags WHERE feed=:f"), {"f": feed})
    route_ids = [r[0] for r in conn.execute(text(
        "SELECT DISTINCT route_id FROM trips WHERE feed=:f"), {"f": feed})]
    out = []
    for rid in route_ids:
        pats = conn.execute(text("""
            SELECT st.trip_id, array_agg(st.stop_id ORDER BY st.seq) AS pat
            FROM stop_times st
            JOIN trips t ON t.feed=st.feed AND t.trip_id=st.trip_id
            WHERE st.feed=:f AND t.route_id=:rid
            GROUP BY st.trip_id"""), {"f": feed, "rid": rid}).all()

        # agrupar por (origen, destino) — variantes de ramal son grupos distintos
        groups: dict = {}
        for tid, pat in pats:
            if not pat:
                continue
            groups.setdefault((pat[0], pat[-1]), {}).setdefault(tuple(pat), []).append(tid)

        for _ends, pat_map in groups.items():
            lens = {tid: len(pat) for pat, tids in pat_map.items() for tid in tids}
            for tid, (semi, skipped) in classify_patterns(pat_map).items():
                out.append((feed, tid, lens[tid], semi, skipped))
    if out:
        _copy_rows(conn, "trip_flags",
                   ["feed", "trip_id", "n_stops", "semidirect", "skipped"], out)
    return len(out)


def classify_patterns(pat_map: dict) -> dict:
    """pat_map: {pattern_tuple: [trip_ids]} de un mismo (ruta,origen,destino).
    Devuelve {trip_id: (semidirect, skipped)}.

    Un viaje es semidirecto si su patrón es subsecuencia estricta del patrón
    modal y omite >=2 paradas interiores. Nunca es marca oficial de CIVIS."""
    canon_pat, _ = max(pat_map.items(), key=lambda kv: (len(kv[1]), len(kv[0])))
    canon_pos = {s: i for i, s in enumerate(canon_pat)}
    canon_interior = set(canon_pat[1:-1])
    result = {}
    for pat, tids in pat_map.items():
        pos = [canon_pos[s] for s in pat if s in canon_pos]
        is_subseq = len(pos) == len(pat) and pos == sorted(pos)
        skipped = len(canon_interior - set(pat)) if is_subseq and pat != canon_pat else 0
        for tid in tids:
            result[tid] = (1 if skipped >= 2 else 0, skipped)
    return result


def _f(v):
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _i(v):
    try:
        return int(v) if v else None
    except ValueError:
        return None


def remote_signature(url: str) -> str:
    try:
        r = httpx.head(url, timeout=20, follow_redirects=True)
        lm = r.headers.get("last-modified", "")
        cl = r.headers.get("content-length", "")
        etag = r.headers.get("etag", "")
        return f"{lm}|{cl}|{etag}"
    except Exception as e:
        log.warning("HEAD %s failed: %s", url, e)
        return ""


MIN_FREE_BYTES = int(__import__("os").environ.get("MIN_FREE_BYTES", 2 * 1024**3))


def disk_ok() -> bool:
    free = shutil.disk_usage("/").free
    if free < MIN_FREE_BYTES:
        log.error("disco bajo presión: %d MB libres; se omite la recarga estática",
                  free // 1024**2)
        return False
    return True


def maybe_reload(feed: str, force=False) -> bool:
    """Descarga y recarga el GTFS estático si la firma remota cambió."""
    if not disk_ok():
        return False
    url = GTFS_STATIC[feed]
    sig = remote_signature(url)
    with engine.begin() as conn:
        prev = get_meta(conn, f"static_sig_{feed}")
    if not force and sig and sig == prev:
        return False
    log.info("downloading static GTFS %s ...", feed)
    t0 = time.time()
    content = httpx.get(url, timeout=300, follow_redirects=True).content
    with engine.begin() as conn:
        counts = load_feed(feed, io.BytesIO(content), conn)
        set_meta(conn, f"static_sig_{feed}", sig)
        set_meta(conn, f"static_loaded_{feed}", int(time.time()))
    log.info("static %s loaded in %.1fs: %s", feed, time.time() - t0, counts)
    return True
