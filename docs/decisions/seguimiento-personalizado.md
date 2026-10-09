# Diseño MVP — seguimiento personalizado de trenes (P4, no implementado)

Objetivo: convertir los favoritos ya existentes en "seguir un tren concreto":
el usuario elige número de tren + fecha (instancia), y la web le muestra su
estado vivo con avisos por Web Push cuando cambia algo relevante.

## Estado del arte propio

- Favoritos actuales: estación / línea / trayecto persistidos en la BD
  (fav_journeys, fav_stations) + suscripción Web Push (VAPID) ya operativa.
- Ficha de tren `/tren/{feed}/{id}?date=` con posición, retraso por parada,
  y bloque `ext` (Radar, opcional).
- Avisos push ya existen para "tu línea tiene incidencia" (avisos oficiales).

## MVP propuesto (prioridad 1 → 3)

### 1. Seguir instancia de tren

- En `/tren/{feed}/{trip_id}?date=YYYY-MM-DD`, botón "Seguir este tren".
- Persiste `(feed, trip_id, service_date)` en una tabla `fav_trip`
  (o extendiendo fav_journeys con `kind='train'`), junto a la suscripción
  push existente.
- La ficha muestra: trayecto completo, próxima estación, último estado RT
  y su edad, y si los datos no son recientes lo dice (`stale`).

### 2. Notificaciones de seguimiento

Eventos que disparan push si están verificados (margen configurable):

- Cambio de vía (solo si la fuente lo aporta: Radar `platform` + GTFS-RT).
- Retraso que cruza umbral (p. ej. pasa de <5 min a ≥10 min en la parada
  del usuario o en la próxima).
- Aviso oficial que toca la línea del tren seguido (reutiliza hilos).
- "Datos caducados": si el feed lleva >X min sin actualizar y el usuario
  tiene un tren seguido en curso, se indica en la notificación.

### 3. Compartir ficha

- URL de la instancia ya es compartible (`/tren/ld/…?date=`); añadir botón
  "Compartir" con Web Share API / copiar enlace.

## Datos y límites honestos

- "Próxima estación" sale de rt_stop_update/fleet; si RT no informa,
  se muestra el horario programado y se etiqueta como tal.
- Nada de ETA propia: solo la que publica la fuente (y `ext.next_eta`
  marcada `stale`/`ambiguous` cuando proceda).
- Sin historial propio del usuario en servidor: los trenes seguidos pueden
  vivir solo en el dispositivo (localStorage) con la suscripción push
  apuntando a la instancia; decidir en la implementación si hace falta
  tabla. Preferible: `fav_trip` en BD para que funcione el push.

## Excluido del MVP

- App móvil nativa, importación de billetes (PDF/email), cuenta de usuario,
  inferencia de "vas a perder el enlace" (requiere planner + fiabilidad RT
  aún no demostrada), seguimiento multimodo.

## Esquema tentativo (si se implementa)

```sql
fav_trip(subscription_id, feed, trip_id, service_date, created_at,
         last_known_delay, last_notified)
```

Cada ciclo del collector (o del worker push existente) recalcula el estado
de los trenes seguidos no finalizados y encola notificaciones con dedupe
`(subscription, trip, evento, ventana)`.

## QA

- Un tren seguido que se borra del feed pasa a "sin datos" y se notifica
  una vez, no en bucle.
- El usuario puede dejar de seguir desde la propia notificación (URL de
  baja firmada) y desde Favoritos.
