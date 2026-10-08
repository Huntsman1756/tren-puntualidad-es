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
- E2E navegador (escritorio + móvil): `cd e2e && BASE=<url web> node v034.mjs`.
- Identidad de líneas: `curl localhost:8000/api/v1/lineas-audit` (route_id → núcleo, evidencia).
- Incidencias: `curl localhost:8000/api/v1/incidencias/audit`.

- `docker compose exec api python -c ...` o `curl localhost:8000/api/meta/status` para frescura.
- Board de prueba: `curl "localhost:8000/api/stations/cer/17000/board"` (Atocha varía; buscar con /api/stations/search).
- Build web: `cd web && npm run build`.
