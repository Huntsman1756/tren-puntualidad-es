# Diseño: planificador con un transbordo

Fecha: 2026-10-09. Estado: IMPLEMENTADO en wip/transbordos (api/api/planner.py + modelos gtfs_transfer/transfer_link + UI). Contexto: `journeys`
ya resuelve origen→destino directo; aquí se añade un único transbordo.

## Prior art: OpenTripPlanner 2

OTP2 resuelve el caso general con **RAPTOR** (rondas por número de
transbordos, criterio de coste generalizado + nº de transbordos) más un
post-proceso que elige el mejor punto de transbordo dentro de cada camino.
Soporta `transfers.txt` completo: `transfer_type=2` con
`min_transfer_time` se trata como *constrained transfer* que invalida el
trasbordo si no se cumple el mínimo (PR #3830, feature flag
`MinimumTransferTimeIsDefinitive`).

**No desplegamos OTP**: stack Java, requiere construir un grafo por cada
recarga del GTFS (nuestro LD se recarga a diario) y mantener un segundo
sistema. Para ≤1 transbordo no hace falta un motor de rutas general: la
enumeración acotada es más simple, más transparente y reutiliza nuestro
modelo `stop_times`/`service_days`/`rt_*` sin capa intermedia.

Qué sí tomamos de OTP: (a) la tabla de transbordos como grafo de aristas
`from_stop→to_stop` con slack, (b) `min_transfer_time` como restricción
dura, (c) nº de transbordos como criterio de ordenación (directo > 1
transbordo), (d) separar la búsqueda del post-proceso de elección de
punto de transbordo.

## Datos disponibles

| Pieza | Estado |
|---|---|
| `transfers.txt` | **Solo existe en GTFS CER** (21 filas: 15 reglas route→route misma estación con `min_transfer_time=480 s`; 3 pares inter-estación: Embajadores↔Atocha Cercanías 19 min, Pte. Alcocer↔Villaverde Alto 18 min, Montcada i Reixac-Manresa↔Montcada i Reixac 5 min). **LD no lo trae.** Hoy no se importa — añadir a `static_load`. |
| Misma parada `(feed, stop_id)` | trivial: misma estación lógica |
| Estación física multi-stop | regla existente: mismo nombre normalizado + <1,5 km (`_geo_items`). Chamartín/Sants/Delicias comparten `stop_id` entre feeds. **Atocha NO**: `cer:18000` "Madrid-Atocha Cercanías" ≠ `ld:60000` "Madrid-Puerta de Atocha-A. Grandes" (124 m, nombres distintos → la regla actual no los fusiona). Valencia Nord `cer/ld:65000` ↔ Joaquín Sorolla `ld:03216` (~800 m, estaciones distintas — transferencia a pie opcional). |
| RT | `rt_trip`/`rt_stop_update` para estimar llegada del primer tramo y salida del segundo |
| Día de servicio | `service_days`; LD `trip_id` ya lleva fecha |

## Algoritmo propuesto (una petición, SQL + Python)

Entrada: `from` (grupo de paradas), `to`, `t0`, `date`, `max_wait`
(defecto 90 min CER / 4 h LD).

1. Resolver origen/destino a conjuntos de paradas con la regla de estación
   física actual + tabla de equivalencias manual.
2. **Primer tramo**: trips con parada en origen y `dep ≥ t0` (misma query
   que `_board`), ordenados por salida.
3. Para cada T1, sus paradas posteriores X con `arr` (programado y, si hay
   RT, estimado). Candidatos a intercambiador = X más las paradas
   alcanzables por una arista de transferencia (tabla `transfer_edge`).
4. **Segundo tramo**: para cada Y candidato, trips T2 con parada en Y y
   `dep ∈ [arr_X + slack, arr_X + max_wait]` que además paran en destino
   (verificación por `stop_times` del mismo trip, seq posterior).
5. Resultado: ordenar por llegada efectiva (programada o estimada),
   deduplicar por (T1,T2), etiquetar cada conexión:
   - `holgada` (buffer ≥ slack×1.5), `justa` (≥ slack), `arriesgada`
     (RT estima buffer < slack — nunca ocultar, siempre avisar),
     `programada` (sin RT).
6. Transbordo entre días: permitir T2 del día siguiente (LD lleva fecha;
   CER repite — `service_days` ya lo modela).

Coste estimado: ~cientos de T1 por día en una estación media × decenas de
paradas posteriores → miles de candidatos, todos filtrables con el índice
existente `ix_stop_times_stop(feed, stop_id, dep)`. Empezar on-demand en
la API; si se degrada, materializar candidatos en el collector.

## Slack mínimo por defecto (cuando no hay `min_transfer_time`)

- Misma parada CER↔CER: 5 min.
- CER→LD en la misma estación (p.ej. Chamartín): 15 min (control de
  acceso de LD/AVE puede cerrar ~5–10 min antes).
- LD↔LD misma estación: 15 min.
- Atocha `cer:18000`↔`ld:60000`: 20 min (124 m + control AV).
- Aristas `transfers.txt`: su `min_transfer_time` exacto.
- Walking transfer entre estaciones distintas (Nord↔J.Sorolla):
  distancia haversine ÷ 80 m/min + 5 min de margen, etiquetado `a pie`.

Tabla `station_alias` manual mínima para los casos que la regla de
nombre+distancia no cubre (Atocha CER↔LD es el nodo crítico nacional).

## Presentación honesta (coherente con el proyecto)

- Nunca mezclar fuentes silenciosamente: cada tiempo lleva su etiqueta
  (programado | estimado | observado-flota | ADIF si llega del piloto).
- Una conexión con RT desfavorable se muestra como `arriesgada`, no se
  elimina: la ausencia de feed no implica imposibilidad.
- Si ADIF del piloto aporta vía del segundo tramo, mostrarla etiquetada.
- Página propia indexable `/trayecto/{origen}/{destino}` ampliando la
  `/trayecto` actual; el transbordo se desglosa con hora, slack y vía
  conocida de cada tramo.

## Riesgos

- Falsos transbordos por nombre: mitigado con regla nombre+geo + alias
  manual auditables (nada de inferencia silenciosa).
- CER→LD real: el GTFS CER es el que trae `transfers.txt`; los cruces
  CER→AVE se apoyan en equivalencias de estación, no en datos oficiales
  de enlace — mostrar slack generoso y marca "cambio de estación".
- Recarga diaria LD: los `trip_id` cambian con fecha — la búsqueda para
  `date≠hoy` usa solo horario (como ya hace `_board` con
  `scheduled_only`).

## Implementación (2026-10-09, wip/transbordos)

- `api/api/planner.py::find_transfers`: enumeración acotada
  (≤40 T1 × paradas posteriores ≤60, una query por feed para T2),
  ~0,1 s por consulta sobre la BD real.
- `gtfs_transfer`: import verbatim de `transfers.txt` (solo CER publica;
  LD no lo trae). Se respeta `transfer_type=3` (prohibido) y los filtros
  `from_route/to_route/from_trip/to_trip`.
- `transfer_link`: sustituye a la `station_alias` propuesta — aristas
  dirigidas y verificadas (Atocha Cercanías↔Puerta de Atocha con 15′/10′;
  12 complejos/enlaces adicionales: Málaga, Valencia Nord↔J.Sorolla,
  Figueres↔Vilafant, Bilbao, León, Oviedo, Vigo, Barcelona). Cada arista
  lleva `source` auditable; sin inferencia geográfica.
- Además de T1→enlace→T2: leg0 (el enlace ES el primer tramo: caminar
  desde el origen hasta el intercambiador, p.ej. Atocha Cercanías→a pie→
  Puerta de Atocha) y leg3 (bajar del tren en X y caminar hasta el
  destino real). Esto es lo que permite cer:18000→ld:71801 sin tren previo.
- Slack por defecto como en el diseño (cer→cer 5′, cer→ld 15′, ld→ld 15′,
  ld→cer 10′); etiqueta `risk` ∈ ok/tight/risky (RT desfavorable se
  muestra, nunca se oculta).
- `/journeys/plan`: nuevo estado `transfer_only` (no hay directo pero sí
  transbordo); `transfers[]` se adjunta siempre (parámetro `transfers=0`
  lo desactiva; `max_wait` en minutos, 10–480).
- UI: sección "Con un transbordo" en /trayecto con distintivo de margen
  y leyenda honesta (prog./est. por tramo).
- Pendiente del diseño: página propia indexable por trayecto.
