# Changelog

## [0.2.0] — 2026-10-08

### API
- `date`/`time`/`hours` en `/journeys` y `/stations/*/board`; los servicios que
  cruzan medianoche se incluyen desde el service-day anterior.
- Sin datos RT en fechas ≠ hoy (los trip_id de Cercanías se repiten a diario).
- `GET /api/v1/meta/coverage`: rango real de fechas del GTFS por feed.
- `trip_flags`: semidirectos inferidos por patrón de paradas (subsecuencia del
  modal con ≥2 paradas interiores omitidas). No es una marca oficial de CIVIS.
- Filtro `semidirect=true` en journeys y tableros.
- Agrupación de estaciones por nombre normalizado **y** distancia <1,5 km.
- `/trains/by-number/{n}?date=` para fechas concretas.
- Tableros devuelven `feed_ts` (timestamp real del feed) y `scheduled_only`.

### Web
- Rediseño completo: planner con pestañas Trayecto/Estación/Nº de tren.
- Selector de fecha y hora en trayectos y estaciones.
- Favoritos de trayecto (nombre, días, franja, ida/vuelta, ordenación,
  export/import JSON). Migración transparente desde v1.
- Tarjetas de tren en móvil; tabla completa en escritorio.
- Tema claro/oscuro persistente.
- Frescura mostrada por timestamp real del feed, no del fetch del navegador.
- Errores de refresco distinguidos de "sin trenes".
- Badge `semi`/`omite N` en tableros, trayectos y ficha de tren.
- manifest.webmanifest (base PWA).

## [0.1.0] — 2026-10-08
- MVP inicial: estaciones, tableros, trayectos, ranking de retrasos, flota.

## [0.1.0] - 2026-10-08

Primera versión verificable.

- Ingesta GTFS estático CER + AV/LD/MD (recarga diaria y por firma remota).
- Sondeo GTFS-RT (trip_updates, vehicle_positions, alerts) + flota.json del visor.
- API pública versionada `/api/v1` con búsqueda de estaciones, tablero
  combinado multi-red, trayectos directos, ficha de tren, ranking de retrasos,
  avisos y estado de frescura.
- Web Astro SSR + Svelte: buscador con autocompletado y favoritos,
  página de estación, trayecto, tren, ranking, /estado y /fuentes.
- Histórico: tabla `observations` con deduplicación (retención 90 días).
- Despliegue docker-compose; prod sobre Traefik/Coolify, standalone con Caddy.
- Tests offline (36) + integración + CI (lint, build, docker, secretos).
