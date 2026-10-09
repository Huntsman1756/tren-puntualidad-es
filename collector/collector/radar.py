"""Adaptador opcional RadarDeTrenes → enriquecimiento LD.

Fuente: https://radardetrenes.com/api/v1/fleet?type=ld (API pública de
solo lectura, proyecto independiente — intermediario sobre datos Renfe/ADIF).

Qué aporta sobre nuestro GTFS-RT: `platform` real de la parada actual,
`rollingStock` (unidad física) y `nextStationArrival` (ETA de la próxima
parada). Lo almacenamos en `rt_ext_ld` con trazabilidad completa
(provider_ts + observed_at + source='radar') y la API lo expone como
bloque `ext` — nunca sustituye al dato Renfe ni bloquea a la API.

Identidad estricta: (trainCode, fecha de circulación). La fecha se deriva
del trip_id LD (`NNNNNvYYYY-MM-DD`) al enlazar; el servicio mostrado es el
que circula ahora en la zona Europe/Madrid.

Desactivado por defecto: RADAR_ENABLED=1 lo activa. Si la fuente falla,
los datos previos quedan y se marcan `stale` en la API.
"""

import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from collector.config import RADAR_BASE, RADAR_ENABLED, RADAR_UA
from collector.db import engine, set_meta

log = logging.getLogger("collector.radar")
TZ = ZoneInfo("Europe/Madrid")

_TIMEOUT = 12
_PROVIDER_STALE_S = 15 * 60  # provider_ts con más antigüedad -> stale en API


def _iso_to_epoch(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except (ValueError, TypeError):
        return None


def poll_radar() -> int:
    """Un ciclo de recogida de la flota LD de RadarDeTrenes.

    Devuelve filas escritas; 0 si desactivado o sin datos. Los fallos de
    red no rompen el bucle: quedan en el log y los datos previos siguen
    sirviendo (con su provider_ts para evaluar frescura)."""
    if not RADAR_ENABLED:
        return 0
    r = httpx.get(
        f"{RADAR_BASE}/fleet",
        params={"type": "ld"},
        headers={"User-Agent": RADAR_UA, "Accept": "application/json"},
        timeout=_TIMEOUT,
        follow_redirects=True,
    )
    r.raise_for_status()
    body = r.json()
    trains = body.get("trains") or []
    provider_ts = _iso_to_epoch(body.get("updatedAt"))
    observed = int(time.time())
    rows = []
    for t in trains:
        code = (t.get("trainCode") or "").strip()
        if not code:
            continue
        platform = (t.get("platform") or "").strip()
        rows.append(
            {
                "tn": code,
                # el feed lista trenes en circulación: su fecha de servicio es
                # el día civil actual en Europe/Madrid (salvo el raro caso de
                # un nocturno que aún no hemos podido verificar — la PK lo
                # admite: añade otra fila sin colisionar)
                "sd": datetime.now(TZ).date(),
                "pf": platform if platform and platform != "0" else None,
                "rs": json.dumps(t["rollingStock"]) if t.get("rollingStock") else None,
                "ns": (t.get("nextStationCode") or "").strip() or None,
                "eta": _iso_to_epoch(t.get("nextStationArrival")),
                "dm": t.get("delayMinutes"),
                "pr": t.get("productName"),
                "pts": _iso_to_epoch(t.get("timestamp")) or provider_ts,
                "obs": observed,
            }
        )
    if not rows:
        return 0
    with engine.begin() as c:
        for w in rows:
            c.execute(
                text("""
                INSERT INTO rt_ext_ld(train_number, service_date, platform,
                    rolling_stock, next_stop_id, next_eta, delay_min,
                    product, provider_ts, observed_at, source)
                VALUES(:tn,:sd,:pf,CAST(:rs AS jsonb),:ns,:eta,:dm,:pr,:pts,:obs,'radar')
                ON CONFLICT(train_number,service_date) DO UPDATE SET
                  platform=EXCLUDED.platform, rolling_stock=EXCLUDED.rolling_stock,
                  next_stop_id=EXCLUDED.next_stop_id, next_eta=EXCLUDED.next_eta,
                  delay_min=EXCLUDED.delay_min, product=EXCLUDED.product,
                  provider_ts=EXCLUDED.provider_ts, observed_at=EXCLUDED.observed_at
            """),
                w,
            )
        # descartar filas viejas: la flota RT solo cubre trenes de hoy/ayer
        c.execute(text("DELETE FROM rt_ext_ld WHERE observed_at < :cut"), {"cut": observed - 26 * 3600})
        # métricas de conciliación: cuántos trenes del proveedor casan con
        # nuestro GTFS por (número, fecha de servicio)
        matched = c.execute(
            text("""
            SELECT count(*) FROM rt_ext_ld e WHERE EXISTS (
              SELECT 1 FROM trips t
              JOIN service_days sd ON sd.feed='ld'
                AND sd.service_id=t.service_id AND sd.day=e.service_date
              WHERE t.feed='ld' AND t.train_number=e.train_number)""")
        ).scalar()
        set_meta(
            c,
            "radar_stats",
            json.dumps(
                {
                    "provider_rows": len(rows),
                    "matched": matched,
                    "provider_ts": provider_ts,
                    "observed_at": observed,
                }
            ),
        )
    return len(rows)
