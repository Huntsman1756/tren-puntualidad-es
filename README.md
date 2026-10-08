# Trenes a tiempo

Buscador independiente de **puntualidad ferroviaria en España**: salidas y
llegadas por estación, trayectos origen-destino, ficha de tren y ranking de
retrasos, sobre datos oficiales de Renfe en tiempo real.

La diferencia frente a webs de "ranking de retrasos": cualquier persona puede
buscar **su estación** o **su trayecto** y obtener horarios programados,
estimaciones y retrasos observados con la frescura de cada fuente visible.

## Arquitectura

```
data.renfe.com + tiempo-real.renfe.com
  GTFS estático (CER + AV/LD/MD)          ─┐
  GTFS-RT trip_updates / vehicle_positions ├─► collector (Python) ─► PostgreSQL
  alerts.json / flota.json                ─┘                          │
                                          api (FastAPI) ◄─────────────┘
                                                │
                                    web (Astro SSR + Svelte)
                                                │
                              Traefik (VPS) o Caddy (standalone) :443
```

- **collector/** — ingesta: GTFS estático (COPY masivo, recarga diaria y por
  firma `Last-Modified`), sondeo RT (~25 s CER / ~30 s LD), flota del visor
  (~45 s, retraso **observado**), avisos, histórico deduplicado en
  `observations`, retención 90 días, guarda de disco.
- **api/** — FastAPI, API pública versionada `/api/v1`:
  - `GET /api/v1/stations/search?q=` — autocompletado, sin acentos, agrupa la
    misma estación física entre redes
  - `GET /api/v1/stations/board?stops=cer:18000,ld:18000` — tablero combinado
    salidas/llegadas
  - `GET /api/v1/journeys?from=&to=` — trayectos directos (secuencia real de
    paradas + calendario del día)
  - `GET /api/v1/trains/{feed}/{trip_id}` — recorrido y estado del tren
  - `GET /api/v1/trains/by-number/{n}` — instancias de hoy por nº comercial
  - `GET /api/v1/delays/ranking`, `GET /api/v1/alerts`, `GET /api/v1/data/status`
  - `/health/live`, `/health/ready`, OpenAPI en `/docs`
- **web/** — Astro SSR + islas Svelte: buscador protagonista (favoritos e
  historial local), página por estación con auto-refresh, trayecto, tren,
  ranking, `/estado` (frescura de fuentes) y `/fuentes` (metodología).
- **infra/compose/docker-compose.prod.yml** — despliegue sobre Traefik
  (Coolify) con TLS automático, límites de memoria, backup diario pg_dump.
  `docker-compose.yml` base + `docker-compose.dev.yml` (puerto pg local) +
  perfil `standalone` con Caddy para despliegue sin Traefik.

`feed` = `cer` (Cercanías/Rodalies nacional) o `ld` (AV · LD · MD).
Las claves de estación son `feed:stop_id` y pueden combinarse con `,`
(`/estacion/cer:18000,ld:18000` muestra Atocha completa).

## Desarrollo local

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
docker compose up -d collector api          # usa la BD en :5433
cd web && npm install && npm run dev        # http://localhost:4321
# con proxy HTTPS local: docker compose --profile standalone up -d
```

Tests: `pip install -r requirements-dev.txt && pytest tests -m "not integration"`.

## Despliegue (VPS con Coolify/Traefik)

```bash
git clone <repo> trenes && cd trenes
cp .env.example .env   # DOMAIN=trenes.h1756.es, POSTGRES_PASSWORD fuerte
sudo docker compose -f docker-compose.yml -f infra/compose/docker-compose.prod.yml \
  --env-file .env -p trenes up -d --build
```

Traefik emite el certificado Let's Encrypt solo; hace falta DNS apuntando al
VPS (aquí `*.h1756.es` ya lo hace). Sin Traefik, usa el perfil `standalone`
con Caddy: `docker compose --profile standalone up -d` con `DOMAIN` en `.env`.

## Fiabilidad del dato (decisiones deliberadas)

- La especificación GTFS-RT avisa: **la ausencia de actualización no significa
  puntualidad** → etiquetamos `obs.` (flota), `est.` (predicción RT) o
  `programado` (sin RT); nunca deducimos llegadas reales.
- Los trip_updates de Renfe solo traen la **próxima parada**: el resto propaga
  el delay del viaje.
- El ranking distingue `observed` vs `predicted`.
- Retención: observaciones 90 días; backups 15 días; purga de `rt_trip` >12 h.

## Estado del proyecto

Ver `CHANGELOG.md`. Cobertura y limitaciones reales en
`docs/data/source-audit.md`; análisis de competidores en
`docs/research/competitive-analysis.md`; decisiones de reutilización en
`docs/research/prior-art.md`.

**Origen de los datos: Renfe Operadora** (CC-BY 4.0). Proyecto independiente,
no oficial, no afiliado ni patrocinado por Renfe.
