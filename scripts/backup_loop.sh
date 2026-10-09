#!/bin/sh
# Bucle de backup diario de Postgres (servicio "backup" de docker-compose.prod.yml).
#
# Se ejecuta DENTRO de postgres:16-alpine (busybox sh): solo POSIX + pipefail.
# Montado de solo lectura en /backup_loop.sh y lanzado con "sh /backup_loop.sh".
#
# Comportamiento:
#   - Espera a que Postgres acepte conexiones (pg_isready) antes de cada intento.
#   - Vuelca con pg_dump -Fc a un temporal y lo VERIFICA (tamaño > 1 MiB,
#     pg_restore --list legible y con las tablas stops y observations).
#     Solo entonces lo mueve atómicamente a daily-YYYYmmdd-HHMMSS.dump.
#   - Reintenta cada 15 min (máx. 4 reintentos) si falla; luego espera al siguiente ciclo.
#   - Programación: una vez al día a las 03:30 Europe/Madrid, calculada desde el
#     reloj de pared (sin deriva). Al arrancar solo vuelca si /backups/last-ok
#     falta o tiene más de 26 h.
#   - Rotación tras cada éxito: 14 daily-*.dump, 5 renfe_*.sql.gz, 5 predeploy-*,
#     y elimina renfe-*.dump.gz (legado) de más de 30 días.
#
# Variables de control (por defecto de producción; sobreescribibles para pruebas):
#   BACKUP_DIR   directorio de backups          (def. /backups)
#   RETRY_SLEEP  segundos entre reintentos      (def. 900)
#   BACKUP_ONCE  si vale 1, ejecuta un ciclo y sale (pruebas)

set -o pipefail
set -u

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETRY_SLEEP="${RETRY_SLEEP:-900}"
BACKUP_ONCE="${BACKUP_ONCE:-0}"
MIN_BYTES=1048576          # 1 MiB
MAX_RETRIES=4
STATUS_LOG="$BACKUP_DIR/backup-status.log"
LAST_OK="$BACKUP_DIR/last-ok"
TMP_DUMP="$BACKUP_DIR/.tmp-daily.dump"
TARGET_SECS=$((3 * 3600 + 30 * 60))   # 03:30
MAX_AGE_SECS=$((26 * 3600))

log() {
  echo "[backup] $*"
}

now_stamp() {
  TZ=Europe/Madrid date '+%Y-%m-%d %H:%M:%S'
}

# busybox/ash interpreta "08" como octal: quitamos el cero inicial.
to_int() {
  case "$1" in
    0?) echo "${1#0}" ;;
    *) echo "$1" ;;
  esac
}

# Segundos que faltan hasta la próxima 03:30 hora de Madrid (siempre > 0).
secs_until_target() {
  h=$(to_int "$(TZ=Europe/Madrid date +%H)")
  m=$(to_int "$(TZ=Europe/Madrid date +%M)")
  s=$(to_int "$(TZ=Europe/Madrid date +%S)")
  now_s=$((h * 3600 + m * 60 + s))
  wait_s=$((TARGET_SECS - now_s))
  if [ "$wait_s" -le 0 ]; then
    wait_s=$((wait_s + 86400))
  fi
  echo "$wait_s"
}

# Espera (sin límite) a que Postgres responda; reintenta cada 10 s.
wait_db() {
  until pg_isready >/dev/null 2>&1; do
    sleep 10
  done
}

fail() {
  echo "$(now_stamp) FAILED $*" >> "$STATUS_LOG"
  log "FALLO: $*"
  rm -f "$TMP_DUMP"
}

# Un intento completo: volcado + verificación + movimiento atómico.
# Devuelve 0 si OK, 1 si falla (ya registrado en el log de estado).
attempt_backup() {
  ts=$(TZ=Europe/Madrid date +%Y%m%d-%H%M%S)
  final="$BACKUP_DIR/daily-$ts.dump"
  rm -f "$TMP_DUMP"

  # Custom format ya viene comprimido.
  if ! pg_dump -Fc -f "$TMP_DUMP"; then
    fail "pg_dump devolvió error"
    return 1
  fi

  size=$(wc -c < "$TMP_DUMP" | tr -d ' ')
  if [ "$size" -lt "$MIN_BYTES" ]; then
    fail "dump demasiado pequeño (${size} bytes < ${MIN_BYTES})"
    return 1
  fi

  list_file="$BACKUP_DIR/.tmp-list.txt"
  if ! pg_restore --list "$TMP_DUMP" > "$list_file" 2>/dev/null; then
    rm -f "$list_file"
    fail "pg_restore --list no puede leer el dump"
    return 1
  fi
  if ! grep -q 'TABLE DATA public stops' "$list_file"; then
    rm -f "$list_file"
    fail "el dump no contiene TABLE DATA public stops"
    return 1
  fi
  if ! grep -q 'TABLE DATA public observations' "$list_file"; then
    rm -f "$list_file"
    fail "el dump no contiene TABLE DATA public observations"
    return 1
  fi
  rm -f "$list_file"

  # mv dentro del mismo volumen es atómico: nunca hay un daily-*.dump a medias.
  if ! mv "$TMP_DUMP" "$final"; then
    fail "no se pudo mover el dump a $final"
    return 1
  fi

  bytes=$(wc -c < "$final" | tr -d ' ')
  echo "$(now_stamp) OK $(basename "$final") $bytes" >> "$STATUS_LOG"
  date +%s > "$LAST_OK"
  log "OK: $final ($bytes bytes)"
  return 0
}

# Rotación. Se llama solo tras un éxito, así el fichero recién creado
# siempre es el más nuevo y nunca se borra.
#   $1 = patrón glob (sin directorio)   $2 = nº de ficheros a conservar
keep_newest() {
  # shellcheck disable=SC2012
  ls -t "$BACKUP_DIR"/$1 2>/dev/null | tail -n +$(( $2 + 1 )) | xargs -r rm -f
}

rotate() {
  keep_newest 'daily-*.dump' 14
  keep_newest 'renfe_*.sql.gz' 5
  keep_newest 'predeploy-*' 5
  find "$BACKUP_DIR" -maxdepth 1 -name 'renfe-*.dump.gz' -mtime +30 -exec rm -f {} \; 2>/dev/null
  return 0
}

# Ciclo completo con reintentos: espera a la BD antes de cada intento.
run_cycle() {
  retries=0
  while :; do
    wait_db
    if attempt_backup; then
      rotate
      return 0
    fi
    if [ "$retries" -ge "$MAX_RETRIES" ]; then
      log "Sin éxito tras $MAX_RETRIES reintentos; se espera al siguiente ciclo"
      return 1
    fi
    retries=$((retries + 1))
    log "Reintento $retries/$MAX_RETRIES en ${RETRY_SLEEP} s"
    sleep "$RETRY_SLEEP"
  done
}

# ¿Hace falta un volcado al arrancar? Solo si no hay last-ok o es antiguo.
needs_startup_dump() {
  last=0
  if [ -f "$LAST_OK" ]; then
    last=$(cat "$LAST_OK" 2>/dev/null)
  fi
  case "$last" in
    ''|*[!0-9]*) last=0 ;;
  esac
  now=$(date +%s)
  [ $(( now - last )) -gt "$MAX_AGE_SECS" ]
}

# --- main ---------------------------------------------------------------
mkdir -p "$BACKUP_DIR"

if [ ! -f /usr/share/zoneinfo/Europe/Madrid ]; then
  log "AVISO: falta la base de zonas horarias; la hora puede ser UTC"
fi

if [ "$BACKUP_ONCE" = "1" ]; then
  # Modo prueba: un ciclo completo y salir.
  run_cycle
  exit $?
fi

if needs_startup_dump; then
  log "Arranque: last-ok ausente o > 26 h; volcado inmediato"
  run_cycle || true
else
  log "Arranque: last-ok reciente; no se vuelca al arrancar"
fi

while :; do
  wait_s=$(secs_until_target)
  log "Próximo backup a las 03:30 Madrid, en ${wait_s} s"
  sleep "$wait_s"
  run_cycle || true
done
