"""Bucle principal del collector."""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from collector.config import (
    GTFS_STATIC,
    POLL_ALERTS,
    POLL_FLOTA,
    POLL_PUSH,
    POLL_STATIC,
    POLL_TRIP_UPDATES,
    POLL_VEHICLE_POSITIONS,
)
from collector.db import engine, get_meta, set_meta, wait_and_create
from collector.geo import run_geo
from collector.gtfsutil import TZINFO
from collector.lines import compute_trip_spans, refresh_lines
from collector.push import run_push_cycle
from collector.realtime import (
    poll_alerts,
    poll_fleet,
    poll_trip_updates,
    poll_vehicle_positions,
    prune_history,
)
from collector.static_load import (
    compute_trip_flags,
    ensure_snapshots,
    maybe_reload,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("collector")


async def loop_task(name, interval, fn):
    while True:
        t0 = time.time()
        try:
            n = await asyncio.to_thread(fn)
            log.info("%s ok (%s) en %.1fs", name, n, time.time() - t0)
        except Exception:
            log.exception("%s failed", name)
        await asyncio.sleep(max(1, interval - (time.time() - t0)))


async def static_loop():
    while True:
        for feed in GTFS_STATIC:
            try:
                await asyncio.to_thread(maybe_reload, feed)
            except Exception:
                log.exception("static reload %s failed", feed)
            try:
                # histórico mínimo: también cuando el GTFS no cambia
                await asyncio.to_thread(ensure_snapshots, feed)
            except Exception:
                log.exception("snapshot %s failed", feed)
        await asyncio.sleep(POLL_STATIC)


async def rt_trip_loop():
    while True:
        for feed, fn in (("cer", lambda: poll_trip_updates("cer")),
                         ("ld", lambda: poll_trip_updates("ld"))):
            try:
                n = await asyncio.to_thread(fn)
                log.info("trip_updates %s: %d viajes", feed, n)
            except Exception:
                log.exception("trip_updates %s failed", feed)
        await asyncio.sleep(POLL_TRIP_UPDATES)


async def rt_vehicle_loop():
    while True:
        for feed in ("cer", "ld"):
            try:
                n = await asyncio.to_thread(poll_vehicle_positions, feed)
                log.info("vehicle_positions %s: %d vehículos", feed, n)
            except Exception:
                log.exception("vehicle_positions %s failed", feed)
        await asyncio.sleep(POLL_VEHICLE_POSITIONS)


async def fleet_loop():
    while True:
        try:
            n = await asyncio.to_thread(poll_fleet)
            log.info("flota: %d trenes", n)
        except Exception:
            log.exception("flota failed")
        await asyncio.sleep(POLL_FLOTA)


async def push_loop():
    while True:
        try:
            n = await asyncio.to_thread(run_push_cycle)
            if n:
                log.info("push: %d notificaciones enviadas", n)
        except Exception:
            log.exception("push failed")
        await asyncio.sleep(POLL_PUSH)


async def alerts_loop():
    while True:
        try:
            n = await asyncio.to_thread(poll_alerts, "cer")
            log.info("alerts: %d", n)
        except Exception:
            log.exception("alerts failed")
        await asyncio.sleep(POLL_ALERTS)


async def maintenance_loop():
    while True:
        now = datetime.now(TZINFO)
        nxt = (now + timedelta(days=1)).replace(hour=4, minute=30, second=0)
        await asyncio.sleep(max(60, (nxt - now).total_seconds()))
        try:
            await asyncio.to_thread(prune_history)
            for feed in GTFS_STATIC:
                await asyncio.to_thread(maybe_reload, feed, True)  # recarga diaria forzada
                await asyncio.to_thread(ensure_snapshots, feed)   # cierra el día
            await asyncio.to_thread(run_geo)  # reclasificar tras recarga
            await asyncio.to_thread(refresh_lines, engine)
        except Exception:
            log.exception("maintenance failed")


async def main():
    await asyncio.to_thread(wait_and_create)
    log.info("collector arrancado")
    # carga estática inicial en primer plano (la API la necesita)
    for feed in GTFS_STATIC:
        try:
            await asyncio.to_thread(maybe_reload, feed, True)
        except Exception:
            log.exception("initial static load %s failed", feed)
    # backfill de trip_flags si el estático ya estaba cargado antes de existir
    def _backfill_flags():
        from sqlalchemy import text
        with engine.begin() as conn:
            for feed in GTFS_STATIC:
                if get_meta(conn, f"trip_flags_{feed}"):
                    continue
                n = conn.execute(text(
                    "SELECT count(*) FROM trips WHERE feed=:f"), {"f": feed}).scalar()
                if n:
                    k = compute_trip_flags(conn, feed)
                    set_meta(conn, f"trip_flags_{feed}", k)
                    log.info("trip_flags %s: %d viajes clasificados", feed, k)
    try:
        await asyncio.to_thread(_backfill_flags)
    except Exception:
        log.exception("trip_flags backfill failed")
    # spans de viaje (backfill si el estático se cargó antes de v0.3.4)
    def _backfill_spans():
        from sqlalchemy import text
        with engine.begin() as conn:
            for feed in GTFS_STATIC:
                if not conn.execute(text(
                        "SELECT 1 FROM trip_span WHERE feed=:f LIMIT 1"),
                        {"f": feed}).first():
                    log.info("trip_span %s: %d", feed,
                             compute_trip_spans(conn, feed))
    try:
        await asyncio.to_thread(_backfill_spans)
    except Exception:
        log.exception("trip_span backfill failed")
    # identidad de líneas: núcleo verificable por route_id
    try:
        log.info("line_route: %s", await asyncio.to_thread(refresh_lines, engine))
    except Exception:
        log.exception("line_route failed")
    # histórico mínimo de programación: cubre también el caso en que el
    # GTFS no se recargó en el arranque (firma sin cambios)
    for feed in GTFS_STATIC:
        try:
            n = await asyncio.to_thread(ensure_snapshots, feed)
            if n:
                log.info("snapshots %s: %d días escritos", feed, n)
        except Exception:
            log.exception("ensure_snapshots %s failed", feed)
    # conciliación territorial (catálogos oficiales Renfe)
    try:
        st = await asyncio.to_thread(run_geo)
        log.info("geo reconcile: %s", st)
    except Exception:
        log.exception("geo reconcile failed")
    await asyncio.gather(
        rt_trip_loop(), rt_vehicle_loop(), fleet_loop(), alerts_loop(),
        static_loop(), maintenance_loop(), push_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
