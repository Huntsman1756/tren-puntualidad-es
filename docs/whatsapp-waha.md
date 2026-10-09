# Avisos oficiales por WhatsApp Channels (WAHA) — piloto experimental

Estado: **experimental y desactivado por defecto**. Solo se activa si el collector
tiene `WAHA_URL` definido y se levanta el servicio `waha` con `--profile waha`.

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
- **Versión sin fijar.** La imagen está en `latest` durante el piloto; fijar un tag probado al terminar.

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
2. Levantar solo el servicio WAHA (sin puertos publicados; los deploys normales no lo tocan):
   ```
   sudo docker compose -p trenes -f docker-compose.yml -f infra/compose/docker-compose.prod.yml --profile waha up -d waha
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
5. Seguir el canal desde la cuenta de pruebas y verificar la lectura:
   ```
   waha "http://waha:3000/api/default/channels/<invite>/messages/preview?limit=5"
   ```
   Debe devolver mensajes recientes del canal. Si devuelve error o lista vacía, revisar el motor
   (`WHATSAPP_DEFAULT_ENGINE`) antes de seguir.
6. Activar la ingesta en el collector. En el `.env`:
   ```
   WAHA_URL=http://waha:3000
   WAHA_SESSION=default
   WAHA_CHANNELS=<invite>:cercanias-madrid:madrid
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

## Rollback

1. Quitar `WAHA_URL` del `.env` (la ingesta queda desactivada) y redesplegar el collector.
2. Parar el servicio WAHA:
   ```
   sudo docker compose -p trenes -f docker-compose.yml -f infra/compose/docker-compose.prod.yml --profile waha stop waha
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
