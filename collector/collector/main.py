"""Bucle principal del collector."""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import text

from collector.anomalies import run_anomaly_cycle
from collector.config import (
    GTFS_STATIC,
    POLL_ALERTS,
    POLL_FLOTA,
    POLL_PUSH,
    POLL_RADAR,
    POLL_STATIC,
    POLL_TRIP_UPDATES,
    POLL_VEHICLE_POSITIONS,
    RADAR_ENABLED,
)
from collector.db import engine, get_meta, set_meta, wait_and_create
from collector.geo import run_geo
from collector.gtfsutil import TZINFO
from collector.lines import compute_trip_spans, refresh_lines
from collector.push import run_push_cycle
from collector.radar import poll_radar
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


async def radar_loop():
    """Enriquecimiento opcional LD vía RadarDeTrenes (RADAR_ENABLED=1)."""
    while True:
        try:
            n = await asyncio.to_thread(poll_radar)
            if n:
                log.info("radar: %d trenes LD", n)
        except Exception:
            log.exception("radar failed")
        await asyncio.sleep(POLL_RADAR)


async def alerts_loop():
    while True:
        try:
            n = await asyncio.to_thread(poll_alerts, "cer")
            log.info("alerts: %d", n)
        except Exception:
            log.exception("alerts failed")
        await asyncio.sleep(POLL_ALERTS)


def _notices_cycle() -> dict:
    """Avisos oficiales: interpreta los pegados a mano (pendiente) y, si
    WAHA está configurado, lee los canales de WhatsApp de Renfe."""
    from collector.db import engine as _eng
    from collector.notices import process_pending, run_whatsapp_cycle
    n_wa = run_whatsapp_cycle()            # 0 y sin red si WAHA_URL vacío
    with _eng.begin() as conn:
        res = process_pending(conn, int(time.time()))
    return {"whatsapp": n_wa, **(res or {})}


async def anomaly_loop():
    """Posibles incidencias inferidas: una evaluación por minuto."""
    while True:
        try:
            r = await asyncio.to_thread(run_anomaly_cycle)
            if r.get("opened") or r.get("confirmed") or r.get("resolved"):
                log.info("anomalías: %s", r)
        except Exception:
            log.exception("anomalías failed")
        await asyncio.sleep(60)


async def notices_loop():
    while True:
        try:
            r = await asyncio.to_thread(_notices_cycle)
            if any(v for v in r.values() if isinstance(v, int)):
                log.info("avisos oficiales: %s", r)
        except Exception:
            log.exception("avisos oficiales failed")
        await asyncio.sleep(60)


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


def db_populated(conn) -> bool:
    """True si ya hay paradas de todos los feeds estáticos y ambas cargas
    registradas en meta: el arranque puede saltarse la espera inicial."""
    for feed in GTFS_STATIC:
        if not conn.execute(text("SELECT 1 FROM stops WHERE feed=:f LIMIT 1"),
                            {"f": feed}).first():
            return False
        if not get_meta(conn, f"static_loaded_{feed}"):
            return False
    return True


def _check_populated() -> bool:
    with engine.begin() as conn:
        return db_populated(conn)


def _backfill_flags():
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


def _backfill_spans():
    with engine.begin() as conn:
        for feed in GTFS_STATIC:
            if not conn.execute(text(
                    "SELECT 1 FROM trip_span WHERE feed=:f LIMIT 1"),
                    {"f": feed}).first():
                log.info("trip_span %s: %d", feed,
                         compute_trip_spans(conn, feed))


async def initial_static_work():
    """Trabajo pesado de arranque, en el mismo orden en ambas rutas: recarga
    estática forzada, backfills, líneas, snapshots y conciliación territorial.
    Cada paso aísla sus errores para que uno no tumbe a los demás."""
    for feed in GTFS_STATIC:
        try:
            await asyncio.to_thread(maybe_reload, feed, True)
        except Exception:
            log.exception("initial static load %s failed", feed)
    # backfill de trip_flags si el estático ya estaba cargado antes de existir
    try:
        await asyncio.to_thread(_backfill_flags)
    except Exception:
        log.exception("trip_flags backfill failed")
    # spans de viaje (backfill si el estático se cargó antes de v0.3.4)
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


async def main():
    await asyncio.to_thread(wait_and_create)
    log.info("collector arrancado")
    populated = await asyncio.to_thread(_check_populated)
    rt_loops = (rt_trip_loop(), rt_vehicle_loop(), fleet_loop(),
                alerts_loop(), push_loop(), anomaly_loop(), notices_loop())
    if RADAR_ENABLED:
        rt_loops += (radar_loop(),)
    if populated:
        # BD ya poblada: el RT no espera a la carga. El trabajo pesado y
        # static/maintenance (que recargan el GTFS) van en segundo plano,
        # en serie, para no solapar dos recargas del mismo feed.
        log.info("arranque: BD poblada -> RT inmediato; init estático en segundo plano")

        async def _init_then_static():
            await initial_static_work()
            await asyncio.gather(static_loop(), maintenance_loop())

        await asyncio.gather(_init_then_static(), *rt_loops)
    else:
        # BD vacía: la API necesita el estático, carga inicial bloqueante primero
        log.info("arranque: BD vacía -> carga estática inicial bloqueante antes de RT")
        await initial_static_work()
        await asyncio.gather(static_loop(), maintenance_loop(), *rt_loops)


if __name__ == "__main__":
    asyncio.run(main())
