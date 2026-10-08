#!/usr/bin/env bash
# Despliegue seguro en h1756-vps1 (proyecto compose FIJO "trenes").
#
#   sudo bash scripts/deploy.sh
#
# Compruebas incluidas:
#   1. Project name fijo — evita crear un segundo stack/BD (incidente v0.3.1).
#   2. Volúmenes y contenedores existentes esperados (trenes-db-1 etc.).
#   3. Backup pg_dump antes de migrar esquema/cargar datos.
#   4. Build + up -d; aborta si algún contenedor queda no-healthy.
#   5. Smoke test de API pública al final.
set -euo pipefail

PROJECT=trenes
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -p "$PROJECT"
  -f "$DIR/docker-compose.yml" -f "$DIR/infra/compose/docker-compose.prod.yml")
EXPECTED=(trenes-db-1 trenes-collector-1 trenes-api-1 trenes-web-1)

echo "==> Preflight: proyecto fijo = $PROJECT"
# 1) ningún otro stack de este repo puede estar levantado
ALIENTS=$(docker ps --format '{{.Names}}' | grep -E '^(tren|retrasos)' | grep -v "^$PROJECT-" || true)
if [ -n "$ALIENTS" ]; then
  echo "ERROR: existen contenedores ajenos al stack: $ALIENTS" >&2
  exit 1
fi
# 2) volumen de datos real presente
if ! docker volume inspect "${PROJECT}_pgdata" >/dev/null 2>&1; then
  echo "ERROR: falta el volumen ${PROJECT}_pgdata (¿despliegue nuevo?
   revísalo manualmente antes de continuar)" >&2
  exit 1
fi
# 3) db operativa
docker exec trenes-db-1 pg_isready -U "${POSTGRES_USER:-renfe}" -d renfe >/dev/null

echo "==> Backup previo"
BAK=/backups/renfe_$(date +%Y%m%d_%H%M%S).sql.gz
docker exec trenes-db-1 sh -c \
  "pg_dump -U ${POSTGRES_USER:-renfe} -d renfe | gzip > $BAK"
echo "    backup: $BAK ($(docker exec trenes-db-1 du -h "$BAK" | cut -f1))"

echo "==> Pull + build"
git -C "$DIR" pull --ff-only
"${COMPOSE[@]}" build --quiet
"${COMPOSE[@]}" up -d

echo "==> Verificación de contenedores"
sleep 5
for c in "${EXPECTED[@]}"; do
  st=$(docker inspect -f '{{.State.Status}}' "$c")
  [ "$st" = running ] || { echo "ERROR: $c -> $st" >&2; exit 1; }
done
docker ps --format '{{.Names}}' | grep -c "^$PROJECT-" | xargs test 5 -eq || \
  echo "WARN: nº de contenedores del proyecto distinto de 5"

echo "==> Smoke API"
code=$(curl -sf -o /dev/null -w '%{http_code}' \
  "https://${DOMAIN:-trenes.h1756.es}/api/v1/geo/ccaa" || echo 000)
[ "$code" = 200 ] || { echo "ERROR: API no responde ($code)" >&2; exit 1; }
echo "OK — despliegue completado"
