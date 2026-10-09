#!/usr/bin/env bash
# Prueba de restauración de un backup (se ejecuta en el host, no dentro de compose).
#
#   sudo bash scripts/backup_verify.sh [dumpfile]
#
# Sin argumentos: usa el daily-*.dump más reciente del volumen de backups; si no
# hay ninguno, el renfe_*.sql.gz más reciente (pre-deploy).
# Argumento: nombre dentro del volumen (p. ej. daily-20261008-033000.dump), ruta
# absoluta dentro de /backups, o un fichero del host (se monta de solo lectura).
#
# Levanta un contenedor desechable (tmpfs, sin red), restaura en una BD nueva,
# comprueba conteos y frescura, y compara stops con la BD viva.
# Siempre elimina el contenedor al salir. Código de salida != 0 ante cualquier fallo.
set -euo pipefail
# No-op en Linux; evita que Git Bash reescriba rutas /backups al llamar a docker.
export MSYS_NO_PATHCONV=1

VOLUME="${BACKUP_VOLUME:-trenes_backups}"
LIVE_DB="${DB_CONTAINER:-trenes-db-1}"
IMAGE="postgres:16-alpine"
CTR="trenes-restoretest-$$"
T_USER="renfe"
T_DB="restoretest"
T_PASS="rt-$$-$(date +%s)"    # contraseña de un contenedor desechable

cleanup() {
  docker rm -f "$CTR" >/dev/null 2>&1 || true
}
trap cleanup EXIT

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# Lista el fichero más reciente que case con el patrón $1 dentro del volumen.
pick_latest() {
  docker run --rm --network none -v "$VOLUME:/backups:ro" --entrypoint sh "$IMAGE" \
    -c "ls -t /backups/$1 2>/dev/null | head -n 1; true"
}

docker volume inspect "$VOLUME" >/dev/null 2>&1 || die "no existe el volumen $VOLUME"

# --- 1. Elegir el fichero ----------------------------------------------------
HOST_MOUNT=()
if [ $# -ge 1 ]; then
  arg="$1"
  if [ -f "$arg" ]; then
    abs_dir=$(cd "$(dirname "$arg")" && pwd)
    HOST_MOUNT=(-v "$abs_dir:/restore:ro")
    FILE="/restore/$(basename "$arg")"
  else
    case "$arg" in
      /*) FILE="$arg" ;;
      *) FILE="/backups/$arg" ;;
    esac
  fi
else
  FILE=$(pick_latest 'daily-*.dump') || true
  if [ -z "$FILE" ]; then
    FILE=$(pick_latest 'renfe_*.sql.gz') || true
  fi
  [ -n "$FILE" ] || die "no hay daily-*.dump ni renfe_*.sql.gz en $VOLUME"
fi
echo "==> Backup a restaurar: $FILE"

# --- 2. Contenedor desechable -----------------------------------------------
echo "==> Arrancando contenedor desechable $CTR (tmpfs, sin red)"
docker run -d --name "$CTR" --network none \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER="$T_USER" -e POSTGRES_PASSWORD="$T_PASS" -e POSTGRES_DB="$T_DB" \
  -v "$VOLUME:/backups:ro" ${HOST_MOUNT[@]+"${HOST_MOUNT[@]}"} \
  "$IMAGE" >/dev/null

# Esperamos por TCP interno: durante el initdb el servidor temporal solo escucha
# por socket, así que no hay falso positivo antes del arranque definitivo.
ready=0
for _ in $(seq 1 120); do
  if docker exec "$CTR" pg_isready -h 127.0.0.1 -U "$T_USER" -d "$T_DB" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
[ "$ready" = 1 ] || die "Postgres no estuvo listo en el contenedor desechable"

# --- 3. Restaurar -----------------------------------------------------------
case "$FILE" in
  *.dump)
    echo "==> pg_restore (formato custom) en $T_DB"
    docker exec "$CTR" pg_restore -U "$T_USER" -d "$T_DB" \
      --no-owner --no-privileges "$FILE" \
      || die "pg_restore falló" ;;
  *.sql.gz)
    echo "==> gunzip | psql (SQL plano) en $T_DB"
    docker exec "$CTR" sh -c \
      "gunzip -c '$FILE' | psql -X -v ON_ERROR_STOP=1 -q -U $T_USER -d $T_DB" \
      >/dev/null || die "restauración SQL falló" ;;
  *)
    die "formato no reconocido (esperado .dump o .sql.gz): $FILE" ;;
esac

# --- 4. Comprobaciones ------------------------------------------------------
q() {
  docker exec "$CTR" psql -X -At -U "$T_USER" -d "$T_DB" -c "$1"
}

STOPS=$(q "select count(*) from stops") || die "consulta a stops falló"
ROUTES=$(q "select count(*) from routes") || die "consulta a routes falló"
TRIPS=$(q "select count(*) from trips") || die "consulta a trips falló"
OBS=$(q "select count(*) from observations") || die "consulta a observations falló"
MAX_OBS=$(q "select coalesce(max(observed_at),0) from observations") \
  || die "consulta a max(observed_at) falló"

for pair in "stops:$STOPS" "routes:$ROUTES" "trips:$TRIPS" "observations:$OBS"; do
  name="${pair%%:*}"
  val="${pair#*:}"
  [ "$val" -gt 0 ] 2>/dev/null || die "tabla $name vacía o inválida (valor: '$val')"
done

NOW=$(date +%s)
AGE_H=$(( (NOW - MAX_OBS) / 3600 ))

echo "==> Conteos restaurados"
echo "    stops=$STOPS routes=$ROUTES trips=$TRIPS observations=$OBS"
echo "    max(observed_at) hace ${AGE_H} h (epoch UTC)"

# --- 5. Comparación con la BD viva -----------------------------------------
echo "==> Comparando stops con la BD viva ($LIVE_DB)"
LIVE_STOPS=$(docker exec "$LIVE_DB" psql -X -At \
  -U "${POSTGRES_USER:-renfe}" -d "${POSTGRES_DB:-renfe}" \
  -c "select count(*) from stops" 2>/dev/null) || LIVE_STOPS=""
if [ -z "$LIVE_STOPS" ] || [ "$LIVE_STOPS" -le 0 ] 2>/dev/null; then
  echo "WARN: no se pudo leer stops de la BD viva ($LIVE_DB); comparación omitida"
else
  if [ "$STOPS" -ge "$LIVE_STOPS" ]; then
    diff_n=$(( STOPS - LIVE_STOPS ))
  else
    diff_n=$(( LIVE_STOPS - STOPS ))
  fi
  # Diferencia > 5 % (en enteros): diff*100 > live*5
  if [ $(( diff_n * 100 )) -gt $(( LIVE_STOPS * 5 )) ]; then
    echo "WARN: stops backup=$STOPS vs vivo=$LIVE_STOPS (diferencia > 5 %)"
  else
    echo "    stops backup=$STOPS vs vivo=$LIVE_STOPS (dentro del 5 %)"
  fi
fi

echo "RESTORE OK"
