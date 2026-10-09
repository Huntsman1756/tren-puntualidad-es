# Changelog

## [0.3.8] — 2026-10-09
### Incidencias cuando el feed oficial no se actualiza
- Verificado: el feed GTFS-RT de avisos de Renfe (y el del visor) no cambia
  desde el 07/10 09:32, mientras Renfe publica incidencias en WhatsApp/X (sin
  datos abiertos). `/incidencias` informa ahora `content_stale` y la web lo
  avisa con enlaces a los canales oficiales.
- Nuevo `/api/v1/anomalias`: **posibles incidencias inferidas** de retrasos en
  tiempo real por línea (≥3 trenes con +15 min, o ≥2 y al menos la mitad de
  los monitorizados), con evidencia (trenes, máximo, mediana) y avisos
  oficiales asociados. Siempre etiquetadas como inferidas, nunca como aviso
  oficial. Detectó en vivo la avería de Parla (C4) y la de Zarzaquemada (C5)
  del 09/10.
### Frontend
- Portada: «Ahora en tu red» (posibles incidencias compactas, aviso de feed
  congelado), trayectos guardados antes, planificador más compacto, chips de
  núcleo con leyenda.
- Listas de retrasos con columnas fijas (antes el número de tren se desplazaba
  según el nombre del núcleo) y tarjetas de dos líneas en móvil sin truncar el
  destino. `/trayecto` con columnas alineadas.
- Posibles incidencias también en núcleo, línea e incidencias; contraste,
  foco visible, tablas con scroll en pantallas pequeñas.
### QA
- `e2e/xbrowser.mjs`: Chromium, Firefox y WebKit × escritorio, tableta y
  móvil (Pixel 7, iPhone 13) sobre 17 páginas: errores, desbordes, solapes,
  **columnas desalineadas** y textos truncados; autotest de los detectores con
  páginas defectuosas (`--selftest`).

## [0.3.7] — 2026-10-09
- Histórico: los retrasos implausibles (fuera de −60…+600 min, p. ej. el
  `retrasoMin = -1438` del visor en trenes que cruzan medianoche) ya no
  anclan la fecha de servicio ni generan observaciones (se seguían
  asignando al día siguiente). Defensa adicional: nunca se escribe una
  observación con fecha de servicio futura.
- Las observaciones contaminadas ya escritas se movieron a
  `observations_quarantine` (no se borran).

## [0.3.6] — 2026-10-09 — consolidación

### Estadísticas (integradas, aún sin publicar: STATS_PUBLIC)
- Fusionada la rama de estadísticas (`stats/v0.4.0-local`, 1a0eb95 + ed285d2):
  `/estadisticas` (explorador + nueva vista por días) sobre la v0.3.5.
- Identidad canónica `line_route` también en estadísticas y comparativas:
  núcleo por slug oficial y línea por familia (`c4` = C4+C4a+C4b) o variante.
- Días representativos: se excluyen de las métricas con gate y de las
  comparativas el día en curso, días antes/del inicio de captura, snapshots
  retrospectivos (`late`), días sin horario capturado y días con captura
  incompleta (`capture_health`: inicio >04:00, fin <23:30 o hueco >30 min).
  Cada exclusión se publica con su motivo. Predicción y retraso informado
  siguen separados.
- `/api/v1/stats/daily`: serie por día con gate diario; las unidades que no
  superan el gate comparativo nunca devuelven mediana/P90.
- La web explica la insuficiencia de muestra y muestra «en validación» si la
  API de estadísticas está desactivada.

### Collector
- Arranque rápido: con la BD poblada el sondeo RT empieza al instante y la
  recarga estática, líneas, snapshots y geocodificación van en segundo plano
  (BD vacía: carga inicial bloqueante, como antes).
- Corregido bloqueo indefinido tras reinicios: un backend huérfano del
  contenedor anterior (COPY a medias) bloqueaba el `ALTER TABLE` de arranque.
  Ahora se terminan conexiones huérfanas propias (`application_name`),
  keepalives TCP, migraciones sin locks si no hay cambios y `lock_timeout`.
- `capture_health`: latido diario por feed/fuente (sondeos, primer/último,
  mayor hueco).
- Número de tren: los trip_id CER usan cualquier letra (`1080V20414C4b`), no
  solo `J`; antes la mayoría de trenes salía sin número (se mostraba el
  trip_id y desbordaba la columna).

### Backup
- El backup diario no se verificaba (había un volcado de 20 bytes). Nuevo
  `scripts/backup_loop.sh`: espera a la BD, `pg_dump -Fc` verificado
  (tamaño, `pg_restore --list`, tablas clave), movimiento atómico, reintentos,
  03:30 Europe/Madrid sin deriva, rotación (14 diarios, 5 previos a despliegue)
  y `last-ok`/`backup-status.log`.
- `scripts/backup_verify.sh`: prueba de restauración real en contenedor
  desechable con recuentos y comparación con la BD viva.

### API y web
- Limitador: `X-Forwarded-For` solo se acepta de proxies autorizados
  (`TRUSTED_PROXIES`); el SSR interno sin XFF queda exento
  (`INTERNAL_NETWORKS`). Corrige que cualquiera pudiera elegir su clave.
- `/estado`: la frescura de avisos usa nuestra última descarga (antes mostraba
  «47 h» porque medía cuándo Renfe editó sus avisos).
- `/lineas` es ahora un directorio buscable (C1 → todos sus núcleos) y
  `/nucleos` muestra el estado en vivo de cada núcleo.
- Distintivos de línea que no desbordan en tablas (núcleo en segunda línea).
- e2e: enlaces de trayecto con `from`/`to`; nuevas pruebas de directorio,
  núcleos, desbordes en móvil y estadísticas.

## [0.3.5] — 2026-10-09

### Mapa por núcleo (condicionado a datos válidos)
- `/mapa/{núcleo}?linea=` con Leaflet 1.9.4 (BSD-2) y teselas OpenStreetMap
  con atribución. Enlazado desde núcleos y líneas.
- Se cargan `shapes.txt` y `trips.shape_id` del GTFS CER. Cada shape se
  valida contra las estaciones reales de sus viajes (≥90 % a <300 m); solo
  las válidas se dibujan (67/144 hoy). Las incompletas se listan como
  excluidas: nunca se inventan geometrías.
- Posiciones tal como las publica Renfe (visor y GTFS-RT), una por tren (la
  más reciente), con antigüedad: ≤2 min destacada, 2–10 min atenuada, más
  antiguas ocultas y contadas. Sin interpolación; no se presentan como GPS.
- API `/api/v1/mapa/{núcleo}`; tabla `shape_quality` auditable.

## [0.3.4] — 2026-10-09

### Identidad inequívoca de líneas
- Nueva tabla `line_route`: cada `route_id` GTFS → operador, núcleo, feed,
  código comercial (grafía oficial, C4A ≡ C4a) y familia (C4a/C4b → C4).
- Núcleo desde el prefijo oficial del `route_id` **contrastado** con el
  núcleo que el visor de Renfe asigna a las paradas (`station_nucleo`);
  estados `verified | prefix_only | conflict | unmapped`. Nunca por
  provincia ni por short_name. Auditoría: `/api/v1/lineas-audit`.
- `line_info` (código + núcleo + URL) en tableros, trayectos, ficha de tren,
  nº de tren, ranking y push.
- Páginas `/nucleos`, `/nucleos/{n}`, `/lineas`, `/lineas/{n}/{l}` (familia o
  variante): estaciones por variante, trenes en circulación, avisos e
  identificadores GTFS.
- Ranking en vivo filtrable por núcleo y línea (`/delays/ranking`) y nuevo
  agregado por línea (`/delays/lines`): trenes con dato RT, con retraso
  informado ≥ umbral, retraso máximo y cobertura (con dato / programados
  circulando ahora). Etiquetado como foto en vivo, no puntualidad histórica.
- `flota.json`: retrasos implausibles (p. ej. −1438 min) se ignoran.

### Incidencias oficiales
- `/incidencias` (web y API) con filtros por núcleo, línea, estación, tipo y
  periodo (actuales, vigentes, próximas, retiradas 7 días).
- Normalización GTFS-RT completa: `activePeriod`, `informedEntity`
  (agency/route/route_type/trip/stop), traducciones, url, cause, effect y
  severityLevel solo si existen.
- Alcance oficial respetado: estación ≠ línea; avisos de línea que citan una
  estación se anotan (inferido del texto) para no difundirlos a toda la línea.
- Clasificación explícita con motivo: accesibilidad, obras programadas,
  servicio interrumpido, servicio alternativo, otras.
- Salud de la fuente separada del contenido (`alerts_fetch_ok/err`): fuente
  sana sin avisos ≠ caída ≠ desconocida; AV/LD sin feed de avisos.
- Histórico `alerts_seen` (primera/última vez visto, retención 90 días).
- Avisos en portada (núcleos y trayectos guardados), línea, estación y trayecto.

### Planificador y fechas
- Fecha por defecto en Europe/Madrid (antes `toISOString`, UTC).
- Atajos Hoy / Mañana / Fin de semana / Elegir fecha; cambiar de día no
  arrastra la hora actual (otro día sin hora = día completo).
- Fecha y hora se conservan entre portada, estación, trayecto, línea y tren;
  la búsqueda de estación desde portada respeta fecha y hora.
- Ficha de tren por instancia: `/tren/{feed}/{id}?date=` muestra el horario
  de ese día, solo programado si no es hoy, e indica si no circula.
- `/journeys/plan` con estados: ok, sin directos en la franja, fuera de
  cobertura, requiere transbordo, transbordo entre redes; errores de consulta
  diferenciados en la web. Cobertura válida por feed (`/meta/coverage`).

### Web Push: múltiples reglas
- `push_devices` + `push_rules`: un navegador, varias reglas independientes
  (CRUD en `/api/v1/push/rules`). Credencial de posesión: token aleatorio
  entregado al registrar, solo se guarda su SHA-256.
- Borrar una regla no afecta a las demás; el navegador se da de baja al
  borrar la última. Migración automática de `push_subs` (reclamable una vez
  con el endpoint). `/push/subscribe` antiguo añade regla, nunca sobrescribe.
- Dedup y frecuencia por regla; `tag` por regla en la notificación.

### Navegación y otros
- Menú principal (Horarios, Estaciones, Líneas, Núcleos, Incidencias,
  Retrasos, Favoritos) con menú móvil, migas, enlace de salto y estados
  vacíos/errores explícitos. Nuevas `/horarios` y `/favoritos` (incl. gestión
  de avisos).
- Limitador de la API: clave por IP real (último X-Forwarded-For) y sin
  límite para el SSR interno — antes todo el tráfico compartía una IP.
- `geo/*`: agrupación de estaciones O(n) con caché (sitemap 44 s → <1 s).
- Tests: 137 en verde + 7 que requieren API levantada (líneas homónimas, alertas por routeId/stopId, fechas futuras,
  medianoche, transbordos no soportados, push multi-regla E2E) +
  Playwright escritorio/móvil (`e2e/v034.mjs`).

### Captura histórica (base de v0.4 — estadísticas NO publicadas)
Integrado del trabajo paralelo de v0.4. La API `/api/v1/stats/*` solo se monta con
`STATS_PUBLIC=1` hasta que supere su control de calidad y cobertura.

#### Identidad histórica corregida
- `service_date` ya no se infiere de `now - delay`: se resuelve contra
  GTFS (trips + calendario + stop_times) con anclas de parada; si hay
  ambigüedad queda NULL (nunca se inventa una fecha).
- Identidad (feed, trip_id, service_date) estable ante cambios de día,
  retrasos extremos y recargas GTFS. La deduplicación normaliza fechas.

#### Snapshot de programación histórica
- Nuevas tablas `sched_capture`, `circulation`, `circulation_stop`:
  cada día de servicio se captura una vez; los días cerrados son
  inmutables ante recargas GTFS (marcados `late` si se capturan a
  posteriori). Los denominadores históricos ya no cambian a posteriori.

#### Cobertura desde el inicio real de captura
- `meta.capture_start_<feed>_<fuente>` marca el inicio efectivo de la
  captura tipificada; la migración lo siembra desde el primer dato
  ya tipificado existente. Las métricas nunca cuentan días no
  monitorizados.

#### Núcleos verificables + stats API (backend)
- `geo_station` gana `nucleo`/`nucleo_code`/`lineas` desde el GeoJSON
  oficial del visor Renfe; `route_core` asigna cada ruta CER a su
  núcleo por mayoría de paradas con evidencia (share/matched/total).
- `/api/v1/stats/{options,delays,compare}`: distribuciones de retraso
  informado con gate estadístico público; las comparativas quedan
  desactivadas mientras la cobertura no sea defendible.

#### Tests
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
