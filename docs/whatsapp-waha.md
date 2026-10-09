# Avisos oficiales por WhatsApp Channels (WAHA) — piloto experimental

Estado: **experimental y desactivado por defecto**. Solo se activa si el collector
tiene `WAHA_URL` definido y se levanta el servicio `waha` incluyendo `infra/compose/docker-compose.waha.yml` con `--profile waha`.

## Propósito

Leer los avisos que Renfe publica en el canal oficial de WhatsApp "Renfe Cercanías Madrid"
y convertirlos en avisos oficiales de la web (`/api/v1/avisos-oficiales`), agrupados por hilo
(incidencia y sus actualizaciones). La vía manual (`scripts/aviso.sh`) sigue siendo el respaldo.

## Riesgos

- **Integración no oficial.** WAHA (`devlikeapro/waha`, core Apache-2.0) vincula una cuenta de
  WhatsApp como dispositivo secundario. Meta/WhatsApp no la aprueba y sus términos de uso
  pueden prohibirla.
- **Restricción de la cuenta.** WhatsApp puede limitar o bloquear la cuenta vinculada.
  Por eso se usa una **cuenta de pruebas** con número separado, nunca el número personal del responsable.
- **Ventana corta.** El endpoint de vista previa de canal solo devuelve mensajes recientes;
  hay que sondear con frecuencia (`POLL_WAHA`, por defecto 120 s) para no perder actualizaciones.
- **Problemas upstream.** La lectura de canales depende del motor (`GOWS` por defecto; `WEBJS`
  como alternativa). Algunos motores o versiones han tenido incidencias con canales: si no llegan
  mensajes, probar el otro motor antes de dar el piloto por fallido.
- **Edición y borrado.** WAHA no ofrece un canal fiable para enterarnos de mensajes de canal
  editados o borrados (no hay evento de edición en la vista previa). Limitación documentada:
  conservamos el texto tal como se capturó y nunca borramos un aviso porque deje de
  aparecer en la vista previa.

## Contrato JSON verificado (documentación WAHA + código core)

`GET /api/{session}/channels/{invite}/messages/preview` devuelve una **lista** cuyos
elementos envuelven el mensaje real:

```json
[
  {
    "reactions": {"👍": 10},
    "viewCount": 0,
    "message": {
      "id": "false_123@newsletter_AAAA",
      "timestamp": 1666943582,
      "body": "texto",
      "media": {}
    }
  }
]
```

- El adaptador (`collector/notices.py::_msg_fields`) acepta ese envoltorio **y** el
  mensaje plano que devuelve `GET /api/{session}/chats/{channelId}/messages`
  (`{id, timestamp, body, hasMedia, ...}`, mismo objeto que el evento `message`).
- `timestamp` puede venir en segundos o milisegundos (se normaliza a s).
- Un mensaje sin `body`/`text` (multimedia sin pie) se descarta contando el motivo;
  cualquier estructura no reconocida marca el canal como **degradado**
  (`meta: whatsapp_degraded_{canal}`), nunca como captura sana.
- El endpoint existe en el controlador del repositorio público `core`
  (`previewChannelMessages` lanza `NotImplementedByEngineError` en la base, no
  `AvailableInPlusVersion`) y la tabla de funciones lo marca disponible en WEBJS, WPP,
  NOWEB y GOWS. Las funciones de *búsqueda* de canales son Plus; el preview aparece
  junto a ellas en la documentación, así que la disponibilidad real en la imagen Core
  **debe confirmarse en el piloto** antes de dar por buena la integración.
- `GET /api/{session}/chats/{channelId}/messages` (canal seguido) es alternativa
  compatible con WEBJS/WPP/NOWEB pero **no con GOWS** (issue #433).

## Qué registra el ciclo (meta)

| clave | contenido |
|---|---|
| `whatsapp_session_status` | estado de la sesión (`WORKING`, `STOPPED`, …) |
| `whatsapp_session_degraded` | epoch del último ciclo con sesión no operativa |
| `whatsapp_fetch_ok_{canal}` | epoch de la última descarga HTTP correcta |
| `whatsapp_fetch_err_{canal}` / `_errmsg_` | último fallo y mensaje (sin secretos) |
| `whatsapp_degraded_{canal}` | motivo de degradación con respuesta 200 |
| `whatsapp_last_msg_{canal}` | `posted_at` más reciente visto (frescura del contenido) |
| `whatsapp_stats_{canal}` | JSON acumulado: received, inserted, duplicated, discarded{motivo}, fetch_errors, degraded, late, latency_max, unclassified (en `_all`) |

La API expone este estado en `/api/v1/incidencias` → `sources.whatsapp`
(`ok|stale|down|degraded|disabled`), siempre como degradación de ESA fuente.

## Cadencia y retención

- `POLL_WAHA` (por defecto 120 s) separa sondeos reales; el loop del collector sigue
  procesando pendientes cada minuto aunque no toque sondear.
- Textos de avisos: se conservan íntegros (son publicaciones oficiales públicas, no
  datos personales). Los contadores de meta no guardan contenido; los descartes solo
  retienen el motivo, nunca el cuerpo. No se guarda ningún mensaje de conversaciones
  privadas: solo se leen los canales de la allowlist `WAHA_CHANNELS`.
- Los avisos capturados no se borran aunque desaparezcan de la vista previa; un hilo
  sin novedades pasa a `sin_actualizar` (nunca se presume resuelto).

## Requisitos previos

1. Una **cuenta de pruebas** de WhatsApp (número distinto al personal) con un teléfono dedicado.
2. El enlace de invitación del canal "Renfe Cercanías Madrid". El código de invitación es el último
   segmento de la ruta: en `https://whatsapp.com/channel/<code>`, `<code>` es el valor a usar.
3. Acceso SSH al VPS (`h1756-vps1`) con `sudo`.

## Piloto paso a paso (VPS)

1. Definir el token de WAHA en el `.env` del proyecto (mismo valor que se usará en el collector):
   ```
   WAHA_API_KEY=<valor largo y aleatorio>
   ```
2. Levantar solo el servicio WAHA (sin puertos publicados; los deploys normales no lo tocan).
   La imagen va fijada por tag+digest en el compose (`WAHA_IMAGE`, defecto
   `gows-2026.9.2@sha256:1ae3c6a…`). Los tags de WAHA son por motor
   (`gows-|noweb-|chrome-|latest-`); no existe tag plano de versión.
   Para probar otra, exportar `WAHA_IMAGE=<motor>-<versión>` antes:
   ```
   sudo docker compose -p trenes -f docker-compose.yml -f infra/compose/docker-compose.prod.yml -f infra/compose/docker-compose.waha.yml --profile waha up -d waha
   ```
3. Arrancar la sesión y obtener el QR. El puerto de WAHA no se publica en el host, así que las
   llamadas se hacen desde la red del proyecto (`trenes_default`) con un contenedor auxiliar:
   ```
   waha() { sudo docker run --rm --network trenes_default -e K="$WAHA_API_KEY" curlimages/curl -fsS -H "X-Api-Key: $K" "$@"; }
   waha -X POST http://waha:3000/api/sessions/start -H "Content-Type: application/json" -d '{"name":"default"}'
   ```
   Guardar el QR como imagen (el contenedor auxiliar escribe en stdout; redirigir al host):
   ```
   waha "http://waha:3000/api/default/auth/qr?format=image" > qr.png
   ```
4. Escanear el QR **desde el teléfono de pruebas**: WhatsApp > Ajustes > Dispositivos vinculados >
   Vincular un dispositivo. Comprobar que la sesión queda en estado `WORKING`
   (`GET /api/sessions/default` con la cabecera `X-Api-Key`).
5. Seguir el canal desde la cuenta de pruebas y verificar la lectura.
   Canal oficial de Cercanías Madrid (extraído 2026-10-09 de la página oficial
   "¡Estamos en WhatsApp!" de grupo.renfe.com — verificar el enlace
   visualmente antes de seguirlo):
   `https://www.whatsapp.com/channel/0029VasTM4dBvvseSQb3Pk1B`
   ```
   waha "http://waha:3000/api/default/channels/0029VasTM4dBvvseSQb3Pk1B/messages/preview?limit=5"
   ```
   Debe devolver mensajes recientes del canal. Si devuelve error o lista vacía, revisar el motor
   (`WHATSAPP_DEFAULT_ENGINE`) antes de seguir.
6. Activar la ingesta en el collector. En el `.env`:
   ```
   WAHA_URL=http://waha:3000
   WAHA_SESSION=default
   WAHA_CHANNELS=0029VasTM4dBvvseSQb3Pk1B:cercanias-madrid:madrid
   POLL_WAHA=120
   ```
   Redesplegar el collector con el script habitual: `sudo bash scripts/deploy.sh`.
   Comprobar en los logs: `sudo docker compose -p trenes logs -f collector`.

## Criterios de aceptación

- Un aviso de incidencia y su posterior actualización llegan ambos a `/api/v1/avisos-oficiales`.
- No hay duplicados: la clave única `source + channel + external_id` impide insertar dos veces el mismo mensaje
  aunque se sondee repetidamente.
- Los mensajes quedan agrupados en el hilo correcto (la actualización aparece bajo la incidencia, no como hilo nuevo).
- `curl localhost:8000/api/v1/avisos-oficiales` responde con datos y el collector no acumula errores `WAHA ... fallo al leer canal`.
- `/api/v1/incidencias` → `sources.whatsapp.status == "ok"` y `whatsapp_last_msg_{canal}`
  avanza cuando el canal publica. Si `status` es `degraded` o `down`, el piloto no
  está capturando aunque el HTTP responda.
- Una cuenta vinculada (sesión `WORKING`) **no** equivale a captura funcionando: el
  gate es ver un aviso real del canal interpretado y agrupado en su hilo.

## Rollback

1. Quitar `WAHA_URL` del `.env` (la ingesta queda desactivada) y redesplegar el collector.
2. Parar el servicio WAHA:
   ```
   sudo docker compose -p trenes -f docker-compose.yml -f infra/compose/docker-compose.prod.yml -f infra/compose/docker-compose.waha.yml --profile waha stop waha
   ```
3. Desvincular el dispositivo desde el teléfono de pruebas: WhatsApp > Dispositivos vinculados > cerrar sesión.
4. Opcional: borrar el volumen de sesiones (`waha_sessions`) si no se va a reintentar.

Los avisos ya ingeridos se conservan; el rollback no los borra.

## Vía manual (respaldo)

Si WAHA no funciona, copiar el texto del aviso y darlo de alta a mano:

```
ADMIN_TOKEN=... scripts/aviso.sh --canal cercanias-madrid --nucleo madrid \
  --hora "2026-10-09T10:50:00+02:00" < mensaje.txt
```

`ADMIN_TOKEN` es el mismo valor configurado en el `.env` del VPS. `BASE` apunta por defecto a
`https://trenes.h1756.es`.
