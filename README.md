# Puntualidad Renfe

Web de puntualidad ferroviaria por **estación**, **trayecto** y **tren**, con datos oficiales en tiempo real de Renfe. Alternativa a tardenfe.com / retrasosrenfe.com: en lugar de un simple ranking de retrasos, el usuario puede responder «¿cuándo llega mi próximo tren?», «¿cuánto retraso lleva?» y «¿qué puntualidad tiene mi trayecto?».

## Arquitectura

```
Renfe data.renfe.com
  GTFS estático (cercanías + AV/LD/MD)  ──►  collector (Python) ──► PostgreSQL
  GTFS-RT: trip_updates, vehicle_positions, alerts ──► sondeo 20-60 s
                                            │
PostgreSQL ──► api (FastAPI :8000) ──► web (Astro SSR + Svelte :4321) ──► Caddy :443
```

- **collector/**: descarga el GTFS estático (firma remota por `Last-Modified`/`ETag`, recarga diaria forzada) y sondea los feeds GTFS-RT. Guarda estado actual (`rt_trip`, `rt_stop_update`, `rt_vehicle`, `alerts`) y un histórico append-only (`observations`) solo cuando cambia el retraso/hora prevista.
- **api/**: FastAPI. Endpoints:
  - `GET /api/stations/search?q=` — buscador de estaciones
  - `GET /api/stations/{feed}/{stop_id}/board?kind=departures|arrivals` — tablero con retrasos
  - `GET /api/journeys?from=cer:xxxxx&to=cer:yyyyy` — trayecto origen→destino
  - `GET /api/trains/{feed}/{trip_id}` — recorrido completo del tren
  - `GET /api/delays/ranking`, `GET /api/alerts`, `GET /api/meta/status`
- **web/**: Astro SSR (adaptador Node) + islas Svelte. Páginas indexables por estación y tren; el tablero se refresca solo cada 30 s.

`feed` = `cer` (Cercanías/Rodalies nacional) o `ld` (Alta Velocidad, Larga y Media Distancia).

## Desarrollo local

```bash
cp .env.example .env
docker compose up -d db          # solo Postgres
# primera carga del GTFS estático (~1-3 min, el zip de cercanías ocupa ~240 MB descomprimido)
docker compose up -d collector api
cd web && npm install && npm run dev   # http://localhost:4321
```

La API queda en `http://localhost:8000` (docs en `/docs`).

## Despliegue en el VPS (OVH)

```bash
git clone <repo> && cd retrasosreferealertas
cp .env.example .env   # poner POSTGRES_PASSWORD fuerte y DOMAIN=tudominio.com
docker compose up -d --build
```

Caddy obtiene el certificado HTTPS automáticamente para `DOMAIN`. Apunta el DNS A/AAAA del dominio al VPS antes del primer arranque.

## Fiabilidad del dato (decisiones deliberadas)

- La especificación GTFS-RT avisa: **la ausencia de actualización no significa puntualidad**. La UI marca cada fila como `a tiempo` solo si hay dato RT; si no, `prog.`/`s/d`.
- Los trip_updates de Renfe suelen incluir **solo la próxima parada**: para el resto de paradas se propaga el retraso a nivel de viaje (`delay_source: trip` vs `stop`).
- Los vehículos de LD se actualizan cada ~15 min (los de Cercanías, ~20 s). La UI muestra la antigüedad del dato.
- En `observations` se guarda histórico para estadísticas de puntualidad por franja/línea/día (fase posterior).
- Retención: observaciones 90 días; `rt_trip` se purga tras 12 h sin actualización.

## Fuentes oficiales (data.renfe.com)

| Recurso | URL | Cadencia |
|---|---|---|
| GTFS Cercanías | `ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip` | diario |
| GTFS AV/LD/MD | `ssl.renfe.com/gtransit/Fichero_AV_LD/google_transit.zip` | diario |
| trip_updates CER | `gtfsrt.renfe.com/trip_updates.json` | ~20 s |
| trip_updates LD | `gtfsrt.renfe.com/trip_updates_LD.json` | ~30 s |
| vehicle_positions CER/LD | `gtfsrt.renfe.com/vehicle_positions[_LD].json` | ~20 s / 15 min |
| alerts CER | `gtfsrt.renfe.com/alerts.json` | ~20 s |

Reutilización permitida con atribución (CC-BY 4.0 / condiciones del portal Renfe).
