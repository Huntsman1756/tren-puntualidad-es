#!/usr/bin/env bash
# Alta manual de un aviso oficial (copiado a mano de WhatsApp Channels de Renfe).
#
#   ADMIN_TOKEN=... scripts/aviso.sh [--canal cercanias-madrid] [--nucleo madrid] \
#       [--hora "2026-10-09T10:50:00+02:00"] < mensaje.txt
#   ADMIN_TOKEN=... scripts/aviso.sh [opciones] texto del aviso...
#
# Variables: ADMIN_TOKEN (obligatoria), BASE (por defecto https://trenes.h1756.es).
# El JSON se construye con python3 (json.dumps) para no romper con comillas ni saltos.
set -euo pipefail

CANAL="cercanias-madrid"
NUCLEO="madrid"
HORA=""

while [ $# -gt 0 ]; do
  case "$1" in
    --canal)  CANAL="${2:?falta valor para --canal}"; shift 2 ;;
    --nucleo) NUCLEO="${2:?falta valor para --nucleo}"; shift 2 ;;
    --hora)   HORA="${2:?falta valor para --hora}"; shift 2 ;;
    -h|--help)
      sed -n '2,8p' "$0"; exit 0 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

: "${ADMIN_TOKEN:?defina ADMIN_TOKEN}"
BASE="${BASE:-https://trenes.h1756.es}"

if [ $# -gt 0 ]; then
  TEXTO="$*"
else
  TEXTO="$(cat)"
fi
if [ -z "${TEXTO// /}" ]; then
  echo "ERROR: aviso vacío (pásalo como argumentos o por stdin)" >&2
  exit 1
fi

PAYLOAD="$(TEXTO="$TEXTO" CANAL="$CANAL" NUCLEO="$NUCLEO" HORA="$HORA" python3 -c '
import json, os
d = {"text": os.environ["TEXTO"], "channel": os.environ["CANAL"], "nucleo": os.environ["NUCLEO"]}
if os.environ["HORA"]:
    d["posted_at"] = os.environ["HORA"]
print(json.dumps(d, ensure_ascii=False))
')"

curl -fsS -X POST "$BASE/api/v1/admin/avisos" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary "$PAYLOAD"
echo
