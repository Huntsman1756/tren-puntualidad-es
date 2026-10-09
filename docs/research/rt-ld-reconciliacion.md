# Conciliación RT ↔ GTFS estático en larga distancia

**Fecha**: 2026-10-09 · **Script**: `scripts/diag_ld_rt.py` (reproducible:
`DATABASE_URL=... python scripts/diag_ld_rt.py [fecha]`)

## Medida (BD de desarrollo, 2026-10-09)

| Métrica | Valor |
| --- | --- |
| Viajes LD con servicio hoy | 1599 |
| `rt_trip` feed ld | ~1360 filas |
| `rt_stop_update` viajes ld | 239 |
| Casan por `trip_id` **exacto** | 850 (53 %) |
| Sin match pero con RT del mismo `train_number` | 322 |
| Sin ningún RT | 366 trenes (~23 %) |

Clasificación por número: 731 OK · 283 solo-RT · 366 sin-RT.

## Causa real (no es un desfase de formato)

El `trip_id` LD codifica `NNNNN{etapa}vYYYY-MM-DD` y tanto el estático
como el RT usan el mismo formato. Lo que ocurre:

1. **Instancias por día**: `0316312026-10-07` (ayer) y `0316312026-10-08`
   (hoy) coexisten en `rt_trip`. El RT conserva instancias del día
   anterior ~24-48 h; esas filas no deben mapearse al servicio de hoy.
2. **Ventana de publicación**: una instancia solo aparece en RT mientras
   está activa. Consultada a las 16:00, la AVE 03163 de las 07:00 ya no
   tiene fila (llegó a las 09:56) → los tramos a esa hora salen
   `unknown`, y a las 16:00 los trenes aún en marcha sí casan (`risky`
   real con +26 min observado).
3. **Cobertura real**: los regionales 38xxx no figuran en el feed RT de
   LD; el `sin-rt` (~23 %) es ausencia genuina, no error de unión.

## Regla determinista propuesta

- Clave de instancia: **(prefijo de 6 dígitos + etapa, fecha embebida)** —
  idéntica en ambas fuentes. El match exacto por `trip_id` es la unión
  correcta y queda verificado: las filas `-10-07` NO deben casar con la
  instancia `-10-08` (son servicios de días distintos).
- No casar por `train_number` solo: `031631/2/3` son etapas del mismo
  tren físico (ida/vuelta); el número comercial no desambigua la etapa.
- Métrica continua: % exact-match por día en `meta` (p.ej.
  `rt_ld_match`); si baja de ~40 % avisar, no reconciliar a ciegas.
- Único hueco razonable: un tren en marcha tras medianoche cuyo estático
  se recargó ya con el día siguiente puede llevar el id de ayer en RT;
  se resuelve solo con el match exacto porque el estático conserva
  también la instancia fechada de ayer (misma `trip_id`).

Conclusión: el planificador es correcto casando por `trip_id`; el
estado `unknown` es ausencia verificada, no fallo de unión.
