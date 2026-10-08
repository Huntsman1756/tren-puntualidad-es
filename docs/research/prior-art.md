# Prior art y decisiones de reutilización

Auditoría 2026-10-08 vía GitHub API y lectura de código fuente.

## Candidatos

### eskerda/renfe-skill — MIT

- Qué aporta: cliente GTFS estático + GTFS-RT para Cercanías/Rodalies con
  búsqueda origen-destino, alertas y retrasos. Módulos `gtfs_static.py`,
  `gtfs_rt.py`, `train_type.py`, CLI. ~1 star, último push 2026-05.
- Mantenimiento: reciente, poco adoptado, sin suite de tests visible.
- Reutilización: **conceptos, no dependencia**. Nuestra ingesta ya implementa
  la misma función con COPY masivo y normalización propia. Su heurística de
  clasificación de tipos de tren (`train_type.py`) se consultó como referencia
  pero **no se adoptó**: usamos `route_short_name`/feed oficial en su lugar.
- Riesgo si se integrara: doble stack de parsing, formato de salida propio.

### cmilanf/home-assistant-renfe-tiempo-real — MIT

- Qué aporta: documenta los endpoints públicos del visor oficial
  (`tiempo-real.renfe.com`): `data/estaciones.geojson`,
  `renfe-json-cutter/write/salidas/estacion/{code}.json`,
  `renfe-visor/flota.json`, `renfe-visor/alerts.json`. Normaliza quirks:
  board 404 cuando no hay servicio, timestamps locales naive, TTLs.
- **Reutilizado el conocimiento** (verificado en vivo 2026-10-08):
  `flota.json` se incorpora como sondeo de retraso observado por tren;
  `salidas/estacion/{code}` queda documentado como fuente de contraste
  por estación bajo demanda.
- No integramos el componente (es una integración Home Assistant, aiohttp,
  sin piezas web reutilizables).

### 0x10-z/renfe-enhora — sin licencia

- Astro + ficheros por estación. Sin LICENSE en raíz → **no se reutiliza
  código**. Solo inspiración estructural (ya convergente con lo nuestro).

### jordimariezcu/renfe-live — sin licencia

- Monitor de larga distancia con histórico. Sin LICENSE → no se reutiliza
  código. Su enfoque (captura periódica + retención) confirma nuestra decisión
  de observaciones deduplicadas.

## Librerías evaluadas

- Parsers GTFS-RT: los feeds `.json` de Renfe evitan depender de
  `gtfs-realtime-bindings`/protobuf. Se usa JSON directo con validación
  defensiva (menos dependencias, mismo contenido — verificado comparando
  entidades `.pb` vs `.json` en G0).
- gtfs-validator (MobilityData): no integrada; la ingesta hace validación
  funcional (join RT↔estático, rangos, dedupe). Pendiente: ejecución
  periódica opcional en CI.
- Astro + Svelte + @astrojs/node: stack adoptado (SSR real, islas mínimas).

## Decisión

No hacer fork de ningún proyecto: ninguno cubre buscador por estación +
trayecto + doble red + histórico con persistencia; todos son o herramientas
CLI, o integraciones HA, o sitios de una sola red. Reutilizamos sus hallazgos
documentados sobre los endpoints y sus quirks, con atribución aquí y en el
README.

## Auditoría v0.3.4 (2026-10-09): líneas, incidencias, mapa

### Estándares y herramientas consultadas

- **GTFS-RT Service Alerts** (gtfs.org): `activePeriod` sin rango = vigente
  mientras se publique; varios rangos = vigente en todos; campos de una misma
  `informedEntity` se combinan con AND y varias entidades con OR. Enumeraciones
  `cause`/`effect` (incl. `ACCESSIBILITY_ISSUE`) adoptadas tal cual, sin
  severidades propias. Aplicado en `api/api/incidents.py`.
- **OneBusAway Wayfinder / Waystation** (Open Transit Software Foundation,
  SvelteKit): muestran avisos de servicio en la parada y en la ruta, con
  textos multilingües. Tomamos el patrón de UI (aviso en contexto con su
  alcance), no el código: nuestro stack ya es Astro/Svelte y los avisos de
  Renfe requieren normalización propia.
- **GTFS-RT Inspector** (public-transport, JS) y **gtfs_realtime_viz** (MBTA,
  Elixir): visualizadores de feeds. Confirman el enfoque de mostrar la
  frescura de la posición (`timestamp`) junto al punto; candidatos a
  referencia para el mapa, no a dependencia.

### Hallazgos sobre los feeds de Renfe (verificados en vivo)

- `route_id` CER = `NN` (núcleo) + `T` + 4 dígitos + código comercial
  (`10T0011C4`). Contraste con `estaciones.geojson` del visor (NUCLEO por
  CODIGO_ESTACION): 15 núcleos; el prefijo coincide 1:1 salvo Rodalies
  (GTFS 51 ↔ visor 50, 1010/1010 paradas). 244 rutas con viajes verificadas,
  0 conflictos.
- `alerts.json`: 68 avisos, todos con `descriptionText` en un idioma; ninguno
  con `headerText`, `url`, `cause`, `effect` ni `severityLevel`. 44 de alcance
  línea (`routeId`) y 24 de estación (`stopId`). Muchos avisos de línea
  describen una sola estación (ascensores): se anotan las estaciones citadas
  en el texto como inferencia, sin alterar el alcance oficial.
- 27 `routeId` de Rodalies citados en avisos no existen en el GTFS publicado
  (coincide con quejas públicas sobre RG1/RT2 en datos.gob.es): se identifican
  por formato y se marcan `route_id_format`.
- `flota.json` publica `nucleo` por tren y a veces `retrasoMin` absurdos
  (`-1438` en trenes que cruzan medianoche): fuera de [-60, 600] min se trata
  como sin dato.
- **Mapa**: el GTFS CER incluye `shapes.txt` (144 shapes `NN_LÍNEA[_INV]`,
  segmentos medianos ~50 m: geometría real) y `transfers.txt` (transbordos
  oficiales con tiempo mínimo, útil para un futuro planificador con
  transbordo). Algunas shapes parecen parciales (10_C1 suma 9,8 km frente a
  un recorrido de ~40 km): antes de dibujarlas hay que validar cada shape
  contra la secuencia de paradas. El GTFS AV/LD no trae shapes.
