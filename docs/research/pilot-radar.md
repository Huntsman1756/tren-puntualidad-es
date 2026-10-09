# Piloto RadarDeTrenes — GO/NO_GO para enriquecimiento LD

Fecha del análisis: 2026-10-09. Datos: `rt_ext_ld` de la BD local de
desarrollo (131 filas capturadas con el adaptador real) + contraste de
paneles en vivo (`scripts/pilot_adif.py`, 5 estaciones, salida en
`pilot_tmp/pilot_report.json`).

## Qué aporta sobre nuestro GTFS-RT

| campo | cobertura (131 filas) |
|---|---|
| `platform` (vía real de la parada actual) | 121/131 (92 %) |
| `rollingStock` (unidad física) | 131/131 (100 %) |
| `nextStationCode` + `nextStationArrival` (ETA próxima parada) | 131/131 |
| `delayMinutes` | 131/131 |

Frescura del proveedor: `updatedAt` de la respuesta ≈ tiempo real
(cache-control `max-age=15`); en las filas observadas `provider_ts` y
`observed_at` difieren ~20-130 s (ciclo de sondeo propio).

## Conciliación con nuestro GTFS (identidad = número + fecha de servicio)

- `launchingDate`: presente en las respuestas de **panel** por estación;
  en el endpoint `/fleet?type=ld` no lo hemos observado, así que la fecha
  se resuelve por cobertura (`coverage`/`coverage_multi`/`civil`).
- **128/131 (98 %)**: la pareja `(train_number, service_date)` casa con
  algún `trip_id` en trips+service_days.
- **3/131** sin número en nuestro GTFS LD (`identity_src='civil'`): sin
  enriquecimiento verificable.
- **23/131 (18 %)**: la pareja casa con **>1 `trip_id`** (servicios con
  etapas múltiples: 00438→3, 00621→5, 00622→6, 00631→4…). En estos la vía
  o la ETA pueden corresponder a otra etapa del mismo número comercial:
  se marcan `instances`>1, `ambiguous=true` y `verified=false` en la API.

## Contraste de paneles (`board-renfe`)

- 5 estaciones, todas respondiendo con `operator: RF` exclusivamente —
  **sin cobertura Ouigo/Iryo** en lo observado.
- `plannedTime`: idéntico a nuestro GTFS en 113/113 casos comparables
  (max diff 120 s en 1 caso de Sants).
- `delayMinutes`: concuerda en 57/72 comparaciones; en 15 difiere
  (ej. +720 s en Atocha/Sants) — el delay del proveedor no siempre
  coincide con nuestro RT; se muestra etiquetado, nunca mezclado.
- Panel de Chamartín/Sants incluye Cercanías (`trainCode` 5 dígitos que
  no casan con nuestros trips LD — esperado, son servicios CER).

## Riesgos y condiciones

- Fuente no oficial: intermediario independiente sobre datos Renfe/ADIF.
  Sin SLA ni términos de uso claros → mantener `RADAR_ENABLED` OFF en
  producción y seguir midiendo.
- Fallo prolongado del proveedor: datos previos quedan con `stale` en la
  API (15 min) y el collector registra el error sin tumbar el ciclo.
- Campo `platform` ausente en ~8 % (p. ej. valores "0" filtrados).

## Veredicto: GO condicionado

La fuente es real, fresca y concilia bien por (número, fecha) en ~82 % de
los casos con instancia única. Con la ambigüedad ya marcada
(`instances`/`ambiguous`, esta iteración), el enriquecimiento es seguro
de mostrar con su etiqueta.

- **GO** para mantener el piloto listo y eventualmente activarlo:
  activación = `RADAR_ENABLED=1`, reversible al instante.
- **Sin activación por arrastre**: requiere decisión del responsable.
- Condiciones para producción: (1) ejecutar el piloto una semana en el
  VPS y confirmar que `ambiguous` baja o se mantiene ~18 % aceptable;
  (2) verificar las condiciones de uso de radardetrenes.com (UA ya
  identificativa); (3) si falta vía en fichas clave, considerar panel de
  estación como respaldo (lleva `launchingDate`, identidad más fuerte).

No se afirma cobertura de Ouigo/Iryo (no observada) ni que el retraso del
proveedor sea más fiable que el GTFS-RT (conflictos medidos arriba).
