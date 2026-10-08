# Changelog

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
