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
