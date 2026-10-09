# Piloto técnico: datos ADIF (RadarDeTrenes + info.adif.es)

Fecha: 2026-10-09. Alcance: evaluar si datos ADIF (vías, estados, operadores)
pueden enriquecer AVE/LD sin tocar el collector. Referencia funcional:
trenecitos.es. Evidencia reproducible: `scripts/pilot_adif.py`,
`scripts/probe_adif_signalr.py`, `e2e/probe_adif_ws.mjs`, capturas en
`pilot_tmp/`.

## Veredicto

- **Adaptador opcional sobre RadarDeTrenes: GO** — aporta vía/andén de LD
  (120/120 trenes con plataforma real frente a nuestro 0/127), material
  rodante y ETA de próxima parada, todo con identidad tren+fecha
  (`trainCode`+`launchingDate`). Debe ser opcional, cacheado y nunca
  bloquear nuestra API.
- **Ouigo/Iryo vía API: NO_GO** — `board-renfe` solo publica `RF`
  (Ouigo 13:42 Atocha→BCN ausente pese a estar dentro de la ventana).
- **ADIF server-side (`info.adif.es` SignalR): NO_GO** — 403 Akamai desde
  desarrollo **y desde el VPS de producción** (verificado 2026-10-09).
- **Llegadas efectivas: NO_GO** — `timeType=REAL`/`circulationState` nunca
  poblados en 192 items; `arrived` es estimación conservadora.

## Qué existe y qué se verificó en vivo

### 1. RadarDeTrenes API (`radardetrenes.com/api/v1`) — verificada

Servicio real, sano (`/healthz` ok, v4.10.1, uptime 63 h), sin auth,
100 req/s/IP, CORS `*`, `Cache-Control` honesto (5 s flota, 15 s tableros),
ETag/304, errores RFC 9457, OpenAPI 3.1, SSE funcional en `/fleet/stream`.

| Endpoint | Resultado del piloto |
|---|---|
| `/fleet?type=ld,commuter,all` | 365 trenes (120 LD + 245 CER). GPS, delay, `nextStationArrival` (ETA), `rollingStock` (unidad real, p.ej. `120059`), `platform` |
| `/stations/{code}/board-renfe` | Próximas 20 salidas + 20 llegadas. `source: renfe_official` |
| `/trains/{id}/stops` | Itinerario con `arrivalPlatform`/`departurePlatform` poblados **solo en la parada actual/siguiente**; delays uniformes por tren (propagados) |
| `/trains/{id}/status` | `live`/`finished`/`not_found`; `arrived` = estimación conservadora (no es llegada efectiva observada) |
| `/stats/station/{code}`, `/stats/train/{code}` | Estadísticas propias de retraso (misma naturaleza estimada que la nuestra) |

**Cobertura de operadores en `board-renfe`: solo `RF`.** Verificación
decisiva: Ouigo sale de Atocha a las 13:42 (horario oficial Ouigo) y la
ventana observada cubría 12:27–14:35 sin ningún item no-RF. Mismo resultado
en Chamartín, Sants, Delicias y Joaquín Sorolla (32-40 items cada una,
todos `operator: "RF"`). Sus propios docs lo confirman: los tableros con
Ouigo/Iryo son los que sirven *desde ADIF* a su frontend, no este endpoint.

### 2. ADIF directo: `wss://info.adif.es/InfoStation` (SignalR) — protocolo documentado, acceso bloqueado

Documentado en el proyecto open-source EnVía
(`LafuenteColoradoJose/envia`, Angular/Ionic) y corroborado por la CSP de
radardetrenes.com (`connect-src wss://info.adif.es` — su frontend conecta
desde el navegador del usuario):

- Websocket directo a `wss://info.adif.es/InfoStation`
  (skipNegotiation funciona; sin cookies ni auth).
- `invoke JoinInfo("PRO-ECM-{codigoEstacion}")` + `GetLastMessage(...)`.
- El servidor empuja `ReceiveMessage` con `{station_settings, trains[]}`.
- Campos por tren observados en el código cliente: `platform`,
  `company` (incluye OUIGO e IRYO — EnVía los filtra explícitamente),
  `arrival_time`/`departure_time` (ISO estimados), `class_stop`
  (origin/intermediate/destination/alighting_only), `commercial_id[]`
  (producto + número), `destinations[]`/`origins[]`, `arrivals_access`,
  `departures_access`.
- El esquema `Departure` de RadarDeTrenes (que replica campos ADIF)
  documenta además: `timeType: SCHEDULED|ESTIMATED|REAL`,
  `circulationState`, `announceState`, `platformReliability`,
  `nextOnPlatform`, `sectors`, `trainLength`, `composition`,
  `accessOpeningTime` — presentes en el esquema pero vacíos en todas
  nuestras muestras de `board-renfe` (192 items), por lo que el
  enriquecimiento ADIF solo se apreciaría accediendo a la fuente directa.

**Bloqueo:** `info.adif.es` y `www.adif.es` responden `403 Access Denied`
(Akamai edge) a toda petición desde la IP de desarrollo — con curl, con
`websockets` Python y con Chromium real vía Playwright. Es bloqueo por
reputación de IP, no por protocolo. **Verificado además desde el VPS de
producción (h1756-vps1, 2026-10-09): `GET /`, `POST /InfoStation/negotiate`
y `www.adif.es` responden 403.** Akamai cierra el acceso server-side.

⇒ **NO_GO para consumo server-side de ADIF.** La única vía restante sería
la conexión navegador→ADIF desde el cliente (como hacen Trenecitos, EnVía
y la web de RadarDeTrenes); queda fuera del alcance — no convertirla en
dependencia de producción sin comprobar permisos, CORS y condiciones.

Referencias de protocolo adicionales: `LafuenteColoradoJose/envia`
(Angular/Ionic) y `mariomnts/pantallas-estaciones` (Vue, **GPL-3.0** —
solo como documentación del payload, no código), que revela además
`station_settings.platforms` y una marca de "ADIF envía la información
con retraso" (señal de frescura degradada replicable si algún día se
consume la fuente).

La política de privacidad de Trenecitos confirma que su app consulta ADIF
directamente desde el dispositivo: "ADIF — información de retrasos y vías
en tiempo real… sin intermediarios propios".

## Contraste cuantitativo (evidencia del piloto)

`scripts/pilot_adif.py` cruzó `board-renfe` de 5 estaciones con
`stop_times`+`service_days`+`rt_trip`/`rt_stop_update` locales (día
2026-10-09, ~12:35–13:00 CEST):

| Estación | Items | Match por trainCode | Hora programada idéntica |
|---|---|---|---|
| 60000 Atocha | 40 | 40/40 | 40/40 (Δ=0 s) |
| 17000 Chamartín | 40 | 7/40¹ | 7/7 |
| 71801 Sants | 40 | 14/40¹ | 14/14 |
| 04040 Delicias | 40 | 31/40¹ | 31/31 |
| 03216 J. Sorolla | 32 | 32/32 | 32/32 |

¹ Los no casados son trenes Cercanías cuyo `train_number` era NULL en la
BD de desarrollo. Causa verificada: el collector local se compiló desde
`main`, que no contiene la corrección de `extract_train_number` aplicada en
v0.3.6 (`^\d{4}[A-Z](\d{4,6})` reconoce `1080V19852C1` → `19852`).
`main` es ancestro estricto de `release/v0.3.9`: el fix nunca se fusionó
de vuelta. Producción no está afectada; el artefacto solo se reproduce en
stacks construidos desde `main`. Nota operativa: conviene integrar el
commit de la release en `main` antes de que v0.4.x se construya desde allí.

- `plannedTime` coincide con nuestro `stop_times` al segundo en todos los
  pares casados: la programación ADIF/Renfe y nuestro GTFS son coherentes.
- `delayMinutes` vs nuestro `rt_*` (59 pares con ambos valores): mayoría
  ≤±2 min; diferencias típicas estimación↔estimación (p.ej. radar +2 min
  vs nuestro −14 min en un Alvia — outliers puntuales a vigilar; nuestro
  feed propaga delay de viaje, el suyo refleja el tablero).
- `launchingDate` da la fecha de servicio directamente — elimina la
  inferencia heurística de `resolve_service_dates` para esta fuente.

## Qué aportaría y qué NO

**Aporta (si se accede a ADIF directo):**
- Vía/andén para AV/LD/MD (hoy: 0/127 vehículos LD con plataforma; CER ya
  la tenemos por flota/labels). RadarDeTrenes ya la expone en `/fleet`
  (120/120 LD con valor real) y `/trains/{id}/stops`.
- Estados operativos (`circulationState`, `announceState`,
  `timeType=REAL`) — candidato a primera señal de paso efectivo.
- Ouigo/Iryo en tableros (ADIF sí los publica; el campo `company` existe).
- `launchingDate` = día de servicio sin ambigüedad.

**NO aporta:**
- Llegada efectiva confirmada: `board-renfe` no publica REAL en muestras;
  `arrived` de radar es estimación. Hipótesis pendiente de verificar en
  el SignalR directo (observar transición de `timeType`/`circulationState`
  al pasar un tren por una estación durante varios minutos).
- Más granularidad de retraso: los delays por parada de
  `/trains/{id}/stops` son uniformes (propagación, igual que la nuestra).
- Fiabilidad garantizada: RadarDeTrenes es un proyecto personal; su API
  podría desaparecer. La fuente primaria (ADIF) es estable pero protegida.

## Legal y atribución

- Datos Renfe GTFS/GTFS-RT: CC-BY 4.0 (ya atribuido).
- ADIF `info.adif.es`: servicio operativo público sin ToS de API publicados;
  reutilización informativa con cita de fuente (Ley 37/2007) — mismo
  encaje que el tablero web de ADIF. No hay auth ni elusión de controles:
  el websocket es el mismo que sirve a la web pública de ADIF.
- Si se consume la API de RadarDeTrenes: atribuir Renfe + ADIF + RadarDeTrenes
  (condición de su servicio). Respetar su caché (no sondear más rápido que
  `Cache-Control`), usar SSE en vez de polling para flota, User-Agent
  identificativo.

## Propuesta mínima de integración (reversible, por fases)

**Fase 0 (ejecutada 2026-10-09):**
1. `info.adif.es` desde el VPS de producción → **403 Akamai**: acceso
   server-side descartado. Gate cerrado; no se persiste código para
   eludirlo.
2. `extract_train_number`: ya corregido en `release/v0.3.9` (v0.3.6). El
   desfase pendiente es `main` ← merge del hotfix, fuera del piloto.

**Fase 1 (adaptador independiente, opt-in por config):**
- `collector/adif_board.py`: conector SignalR por estación (topic
  `PRO-ECM-{code}`) o sondeo de `board-renfe` de radar como respaldo.
  Tabla nueva `adif_stop_event` (o `rt_board` con `source='adif'`):
  `(station, train_code, service_date, kind dep/arr, planned_ts,
  delay_min, platform, state, timeType, provider_ts, observed_at)`.
- No mezclar en `_board` existente: exponer como campos
  `adif_platform`/`adif_state` en la respuesta, etiquetados por fuente.
  Nunca presentar `timeType`/`circulationState` como "llegada real" sin
  verificar la semántica en Fase 0.

**Fase 2 (seguimiento de viaje, si Fase 0 confirma valor):**
- Endpoint `/tren/{feed}/{id}` enriquecido con vía ADIF y estados;
  notificación push "cambio de vía" usando PushSub existente;
  enlace compartible `/tren/ld/{trainCode}-{fecha}`.
- Radar `/fleet` como sondeo de material rodante (rollingStock) si se
  quiere mostrar modelo de tren — valor secundario.

**Reversibilidad:** todo detrás de `ADIF_ENABLED` (default off) + tabla
propia; quitar = dejar de sondear. Sin dependencia dura de RadarDeTrenes:
es respaldo, no fuente primaria.

## Limitaciones conocidas

- `board-renfe` se limita a 20+20 items — suficiente como tablero, no
  como histórico.
- Delays `board-renfe` son estimaciones del tablero (no mejores ni peores
  que GTFS-RT; una segunda señal para triangulación, no verdad).
- Akamai puede bloquear el VPS igual que bloqueó esta IP — el piloto no
  puede garantizar la viabilidad server-side hasta la Fase 0.
- Los estados (`circulationState` etc.) no se observaron poblados en
  ninguna muestra vía API; su existencia real solo se confirmará en el
  SignalR directo.
