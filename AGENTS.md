# Notas del proyecto

## Comandos

- Stack completo: `docker compose --profile standalone up -d --build` (incluye Caddy)
- Solo datos para desarrollo: `docker compose up -d db collector api`
- Frontend local: `cd web && npm install && npm run dev` (necesita API en :8000 o PUBLIC_API_URL)
- Logs collector: `docker compose logs -f collector`
- API docs: http://localhost:8000/docs (API v1 bajo /api/v1)

## Convenciones

- `feed` ∈ {`cer`, `ld`}; todas las PKs de tablas GTFS son compuestas `(feed, ...)`.
- `stop_times.arr/dep` son segundos desde medianoche local Europe/Madrid (pueden superar 86400).
- Tiempos RT en epoch UTC (segundos).
- El GTFS de LD usa `trip_id` con fecha (`0393212026-10-07`) → recarga diaria obligatoria.
- CSVs GTFS llevan padding: recortar siempre con `.strip()`. Encoding UTF-8 (fallback cp1252).
- Número de tren: CER `...J<dígitos>C..`; LD 5 primeros dígitos del trip_id.
- Plataforma: se extrae del label del vehículo `PLATF.(n)`.
- Línea = (núcleo, código). Núcleo SOLO desde `line_route` (prefijo de route_id
  contrastado con el visor oficial); nunca por provincia ni short_name.
  Rodalies: prefijo GTFS 51 = NUCLEO 50. Tabla NUCLEOS duplicada en
  `collector/lines.py` y `api/common.py` (un test exige que coincidan).
- Fechas en la web: siempre `lib/dates.ts` (Europe/Madrid), nunca `toISOString()` para "hoy".

## Verificación

- Tests: `TEST_DATABASE_URL=postgresql+psycopg://renfe:renfe@localhost:55433/renfe_test pytest -q`
  (BD AISLADA y desechable: `docker run -d --name rrfa-testpg -e POSTGRES_USER=renfe
  -e POSTGRES_PASSWORD=renfe -e POSTGRES_DB=renfe_test -p 127.0.0.1:55433:5432
  --tmpfs /var/lib/postgresql/data postgres:16-alpine`). Los tests BORRAN tablas: nunca
  apuntar TEST_DATABASE_URL a una BD con datos.
- Backup: diario 03:30 (`scripts/backup_loop.sh`, servicio backup); prueba de restauración:
  `sudo bash scripts/backup_verify.sh` en el VPS (debe imprimir `RESTORE OK`).
- Estadísticas: `/api/v1/stats/*` solo con `STATS_PUBLIC=1`. Días representativos según
  `capture_health` + `sched_capture` (ver stats.py `REPRESENTATIVITY`).
- E2E navegador (escritorio + móvil): `cd e2e && BASE=<url web> node v034.mjs`.
  BROWSER=chromium|firefox|webkit; `node proxy.mjs` da same-origin local.
- Los 7 skips de la suite son tests/test_integration_api.py: necesitan una
  API real corriendo en :8000 (integración viva, no TestClient).
- Identidad de líneas: `curl localhost:8000/api/v1/lineas-audit` (route_id → núcleo, evidencia).
- Incidencias: `curl localhost:8000/api/v1/incidencias/audit`.

- `docker compose exec api python -c ...` o `curl localhost:8000/api/meta/status` para frescura.
- Board de prueba: `curl "localhost:8000/api/stations/cer/17000/board"` (Atocha varía; buscar con /api/stations/search).
- Build web: `cd web && npm run build`.

## Avisos oficiales (WhatsApp/manual)

- Alta manual: `ADMIN_TOKEN=... scripts/aviso.sh [--canal cercanias-madrid] [--nucleo madrid] < mensaje.txt`
  (POST `/api/v1/admin/avisos`; `BASE` por defecto `https://trenes.h1756.es`).
- Ingesta WAHA (experimental): variables `WAHA_URL`, `WAHA_API_KEY`, `WAHA_SESSION`,
  `WAHA_CHANNELS`, `POLL_WAHA` (ver `.env.example`). Desactivada si `WAHA_URL` está vacío.
- Servicio `waha` en `infra/compose/docker-compose.prod.yml` con `profiles: ["waha"]`:
  NO lo arrancan los deploys; solo `--profile waha` explícito.
- Usar SIEMPRE una cuenta de WhatsApp de pruebas, nunca la personal (integración no oficial,
  riesgo de restricción de la cuenta). Guía completa: `docs/whatsapp-waha.md`.

## Planificador con transbordo (wip/transbordos)

- `gtfs_transfer`: import verbatim de `transfers.txt` (solo existe en CER;
  21 filas, la mayoría route→route con `min_transfer_time` en Valencia Nord).
- `transfer_link`: catálogo CURADO de enlaces entre estaciones físicas
  distintas (`collector/transfer_links.py`, sembrado en `wait_and_create`).
  Reglas: solo enlaces verificados con fuente; dirigidos (min_secs puede ser
  asimétrico); NUNCA inferir por proximidad geográfica.
- Planner: `api/api/planner.py::find_transfers` — T1 → arista → T2 acotado,
  slack por defecto cer→cer 5′, cer→ld/ld→ld 15′, ld→cer 10′. Enlaces
  tipo leg0 (caminar al origen) y leg3 (caminar al destino) incluidos.
  Estado nuevo `transfer_only`; `transfers` siempre se devuelve en /plan.
- Riesgo: `ok` (margen ≥1,5×slack con datos suficientes — nunca
  garantizado), `tight` (ajustado), `risky` (RT estimado incumple slack —
  se muestra, nunca se oculta), `unknown` (hoy y falta RT o desactualizado
  >15 min en alguno de los dos tramos).
- Precedencia GTFS en transfers.txt: por pareja (T1,T2) solo aplican las
  reglas del nivel más específico presente (trip > route > stop); un
  transfer_type=3 aplicable prohíbe la conexión incluidos los enlaces
  manuales; tipos 4/5 = continuidad a bordo / apeo en la misma parada.

## Adaptador RadarDeTrenes (opcional)

- `RADAR_ENABLED=1` activa `radar_loop` (POLL_RADAR, defecto 60 s).
- Tabla `rt_ext_ld(train_number, service_date, …)`: plataforma, material
  rodante, ETA próxima parada, delay_min, provider_ts, observed_at,
  source='radar'. Identidad estricta (número+fecha); métricas en
  meta['radar_stats']. La API lo expone como `ext` en /trains/ld/{id} con
  flag `stale` (>15 min sin dato del proveedor). Nunca sustituye al dato
  Renfe; si cae, la API sigue igual.
- NO cubre Ouigo/Iryo (la API pública solo devuelve operador RF).

## Pilotos y scripts

- `scripts/pilot_adif.py`: contraste RadarDeTrenes↔BD local (lee solo). `DATABASE_URL` por env.
- `scripts/probe_adif_signalr.py` / `e2e/probe_adif_ws.mjs`: sonda `wss://info.adif.es/InfoStation`
  (topic `PRO-ECM-{codigo}`). Akamai bloquea IPs de datacenter — probar desde VPS.
- Informe GO/NO_GO: `docs/research/pilot-adif.md`; diseño transbordos: `docs/decisions/planificador-transbordo.md`.
