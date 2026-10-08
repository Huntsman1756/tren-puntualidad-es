# Changelog

## [0.4.0] — 2026-10-09

### Sección /estadisticas
- Nueva página `/estadisticas` con filtros combinables: núcleo de
  Cercanías (clasificación verificable del GeoJSON oficial del visor
  Renfe), línea, comunidad autónoma, provincia, estación, trayecto,
  fechas y franja horaria.
- Muestra por tipo de dato — siempre separados y etiquetados:
  **retraso informado** (flota) y **predicción** (GTFS-RT). Ninguno es
  puntualidad real ni llegada efectiva.
- Distribución por tramos, mediana, P90, media, circulaciones con dato,
  circulaciones programadas (snapshot histórico) y cobertura por día.
- Gate estadístico reproducible (umbrales públicos en
  `/api/v1/stats/options`): las métricas descriptivas solo se publican
  si n, días observados y cobertura son defendibles; la comparativa
  por línea/estación permanece desactivada hasta que ≥2 unidades
  superen el gate comparativo.
- Enlaces directos a la ficha de estación y al trayecto original;
  metodología accesible desde la propia página y /fuentes.

### QA
- e2e (Playwright, escritorio + móvil) cubre /estadisticas: filtros,
  gate visible, comparativa y metodología.
- Tests de integración con frecuencias de sondeo variables, cambios de
  horario, recargas GTFS, días múltiples y observaciones perdidas.

## [0.3.4] — 2026-10-09

### Identidad histórica corregida
- `service_date` ya no se infiere de `now - delay`: se resuelve contra
  GTFS (trips + calendario + stop_times) con anclas de parada; si hay
  ambigüedad queda NULL (nunca se inventa una fecha).
- Identidad (feed, trip_id, service_date) estable ante cambios de día,
  retrasos extremos y recargas GTFS. La deduplicación normaliza fechas.

### Snapshot de programación histórica
- Nuevas tablas `sched_capture`, `circulation`, `circulation_stop`:
  cada día de servicio se captura una vez; los días cerrados son
  inmutables ante recargas GTFS (marcados `late` si se capturan a
  posteriori). Los denominadores históricos ya no cambian a posteriori.

### Cobertura desde el inicio real de captura
- `meta.capture_start_<feed>_<fuente>` marca el inicio efectivo de la
  captura tipificada; la migración lo siembra desde el primer dato
  ya tipificado existente. Las métricas nunca cuentan días no
  monitorizados.

### Núcleos verificables + stats API (backend)
- `geo_station` gana `nucleo`/`nucleo_code`/`lineas` desde el GeoJSON
  oficial del visor Renfe; `route_core` asigna cada ruta CER a su
  núcleo por mayoría de paradas con evidencia (share/matched/total).
- `/api/v1/stats/{options,delays,compare}`: distribuciones de retraso
  informado con gate estadístico público; las comparativas quedan
  desactivadas mientras la cobertura no sea defendible.

### Tests
- `tests/test_collector_db.py`: integración real sobre PostgreSQL
  (ingestas sucesivas, dedup, cambio de día, recarga GTFS, snapshot,
  cobertura, gates). CI corre la suite con Postgres 16.

## [0.3.3] — 2026-10-08

### Modelo histórico corregido (metodología honesta)
- `observations` ahora tipifica cada registro: `source` (trip_update |
  fleet | legacy), `kind` (prediction | reported | legacy),
  `service_date` y `provider_ts` (timestamp del feed) vs `observed_at`
  (hora de recogida propia).
- `service_date` = día de servicio GTFS (feed, trip_id, service_date):
  los trip_id de Cercanías que se repiten a diario ya no se confunden,
  y los servicios que cruzan medianoche conservan su día de servicio.
- Registros anteriores quedan como `legacy` (sin service_date recuperable):
  se conservan pero no entran en las métricas nuevas.
- Flota ya no escribe observación en cada poll (~45 s): deduplica por
  instancia, solo cuando cambia el retraso informado.

### Puntualidad honesta
- `/stations/{feed}/{stop_id}/punctuality` calcula por INSTANCIA DE
  CIRCULACIÓN (no por registro): un tren con muchas actualizaciones no
  pesa más que otro.
- Denominador = circulaciones programadas (trip × día activo), no
  trip_id únicos.
- `semantics: "reported_delay"` — es retraso informado por el feed,
  no llegada efectiva. La web lo etiqueta así.
- `reported_sources` separa flota (informado) vs predicción (estimado).

### deploy.sh
- `pg_dump` con `pipefail` + verificación de archivo (existe, gzip
  íntegro, contiene `COPY public.stops`) — un backup vacío ya no
  puede darse por bueno.


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
