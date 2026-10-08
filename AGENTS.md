# Notas del proyecto

## Comandos

- Stack completo: `docker compose up -d --build` (db + collector + api + web + caddy)
- Solo datos para desarrollo: `docker compose up -d db collector api`
- Frontend local: `cd web && npm install && npm run dev` (necesita API en :8000 o PUBLIC_API_URL)
- Logs collector: `docker compose logs -f collector`
- API docs: http://localhost:8000/docs

## Convenciones

- `feed` ∈ {`cer`, `ld`}; todas las PKs de tablas GTFS son compuestas `(feed, ...)`.
- `stop_times.arr/dep` son segundos desde medianoche local Europe/Madrid (pueden superar 86400).
- Tiempos RT en epoch UTC (segundos).
- El GTFS de LD usa `trip_id` con fecha (`0393212026-10-07`) → recarga diaria obligatoria.
- CSVs GTFS llevan padding: recortar siempre con `.strip()`. Encoding UTF-8 (fallback cp1252).
- Número de tren: CER `...J<dígitos>C..`; LD 5 primeros dígitos del trip_id.
- Plataforma: se extrae del label del vehículo `PLATF.(n)`.

## Verificación

- `docker compose exec api python -c ...` o `curl localhost:8000/api/meta/status` para frescura.
- Board de prueba: `curl "localhost:8000/api/stations/cer/17000/board"` (Atocha varía; buscar con /api/stations/search).
- Build web: `cd web && npm run build`.
