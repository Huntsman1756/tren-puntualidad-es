# Changelog

## [0.3.1] — 2026-10-08

### Modelo territorial de estaciones
- Nueva tabla `geo_station` persistente (independiente de recargas GTFS;
  las paradas retiradas se desactivan, no se borran).
- Conciliación conservadora en cascada: catálogo oficial Renfe (join exacto
  por CÓDIGO, 1924/2147) → Cartociudad/IGN (geocodificador oficial, 222)
  → nombre único de población catalogada → vecino <10km con guarda
  anti-frontera → cadena <8km. Todo lo demás queda sin clasificar.
- Tabla INE embebida: 52 provincias, 19 CCAA, slugs canónicos estables.
- Corregido: `_haversine_m` devolvía 0 sin coordenadas y permitía fusionar
  estaciones homónimas. Ahora devuelve `None` y no se fusiona nunca.

### API
- `GET /geo/ccaa`, `/geo/ccaa/{slug}`, `/geo/provincia/{slug}`,
  `/geo/estaciones`, `/geo/coverage` (contadores reales, sin duplicar feeds).
- `stations/search?ccaa=&provincia=` + provincia/población en resultados.
- `delays/ranking?ccaa=&provincia=` (por localización RT del tren).
- Detalle de estación incluye `geo` (población, provincia, CCAA, fuente).

### Web
- `/estaciones`: explorador accesible en cascada CCAA → provincia →
  estación, con búsqueda por nombre/población.
- `/comunidades/{slug}` y `/provincias/{slug}` (solo territorios verificados).
- Ficha de estación muestra población, provincia y comunidad con enlaces.
- Autocompletado de origen/destino muestra la provincia para homónimos.
- Sitemap incluye páginas territoriales verificadas.
- Fix: canonical/og:url usaba `localhost` detrás del proxy; ahora usa
  `x-forwarded-host`/`PUBLIC_SITE_URL`.

### Verificación
- 75 tests (13 nuevos de territorio: join exacto, ambigüedad fronteriza,
  homónimos cer/ld, persistencia tras recarga, paradas sin coordenadas).
- Casos reales validados: Orduña→Bizkaia (exclave), Malgrat/Calella→Barcelona
  (no Girona), Vic/Manlleu→Barcelona, Ripoll→Girona, Villabona de
  Asturias→Asturias (trampa de nombre).
- Cobertura: 1924 catalogo + 222 cartociudad + 1 inferida = 2147;
  0 sin clasificar en España (14 LD extranjeras sin provincia).


## [0.3.0] — 2026-10-08

- PWA: manifest + iconos + service worker (push, click, fetch passthrough).
- Web Push VAPID: suscripciones anónimas por estación o trayecto con
  umbral de retraso, franja horaria y días. Sin cuentas ni correo.
- Deduplicación por tren/día/escalón de retraso; límite de frecuencia;
  baja inmediata y borrado de endpoints caducados.
- Nunca se notifica sobre datos no confirmados por tiempo real.
- /privacidad con política real.


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
