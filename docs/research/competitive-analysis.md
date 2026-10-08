# Análisis competitivo

Fecha de auditoría: 2026-10-08. Método: descarga de HTML, inspección de bundles,
endpoints públicos y documentación de repositorios. "Observado" = verificado
directamente; "documentado" = fuente secundaria; "no verificado" = no comprobado.

## tardenfe.com

**Observado:**
- SPA React (Vite) renderizada 100 % en cliente: el HTML inicial solo dice
  «Cargando panel...». Sin SSR → SEO de páginas de datos inexistente,
  contenido no indexable, primer render depende de JS + API.
- Secciones declaradas en el shell: «Cercanías» y «Larga distancia»,
  «Histórico», «Información». Sin enlaces internos a estaciones en el HTML.
- Sin buscador visible en el HTML (ningún `<input>`/`<select>` servido).
- Analytics: Microsoft Clarity. PWA manifest presente.
- Título: «Retrasos Renfe, demoras hoy - Incidencias Cercanías Madrid»
  → foco inicial en Madrid.
- En capturas indexadas aparecen retrasos de 15 h para Cercanías: no podemos
  confirmar si son errores de dato, pero indica ausencia de control de
  anomalías visible.

**No verificado:** filtros por estación dentro de la SPA, frecuencia real de
actualización, cobertura por núcleo.

## retrasosrenfe.com

**Observado:**
- Next.js, también con renderizado mayoritariamente cliente
  («Cargando panel...» en el HTML servido).
- Rutas: `/` (Cercanías), `/larga-distancia`, `/history`,
  `/larga-distancia/history`, `/about`. Soporte bilingüe ES/EN.
- Monetización: Ko-fi. Analytics propio (analytics.retrasosrenfe.com).
- Sin buscador en el HTML servido; el modelo mental es ranking global.

**No verificado:** existencia de búsqueda por estación dentro de la app,
filtros, cobertura real de MD.

## tiempo-real.renfe.com (visor oficial)

**Observado:**
- El visor oficial sí permite seleccionar estación y ver un panel de salidas.
- Sus datos salen de documentos JSON públicos:
  `data/estaciones.geojson` (879 estaciones Cercanías con núcleo/líneas),
  `renfe-json-cutter/write/salidas/estacion/{codigo}.json`,
  `renfe-visor/flota.json`, `renfe-visor/alerts.json` (verificado 2026-10-08).
- Solo Cercanías/Rodalies. UX de catálogo-visor, no orientada a consulta rápida.

## Hueco que ocupamos

| Capacidad | Tardenfe | RetrasosRenfe | Visor Renfe | Nosotros |
|---|---|---|---|---|
| Ranking de retrasos | sí | sí | no | sí |
| URL por estación indexable | no (SPA) | no observada | sí (app) | sí (SSR) |
| Búsqueda origen→destino real | no observada | LD limitada | no | sí (seq. paradas) |
| Cercanías + AV/LD/MD unidos | parcial | sí | solo CER | sí |
| Tablero llegadas+salidas con delay y vía | no observada | no observada | salidas | sí |
| Ficha de tren con recorrido | no observada | no observada | no | sí |
| Histórico propio | «Histórico» (?) | sí | no | sí (en acumulación) |
| SSR/SEO por página | no | parcial (Next) | no | sí |

## Conclusiones de diseño

1. SSR real en todas las páginas de datos: ventaja directa de SEO y velocidad
   frente a ambas SPA.
2. El buscador es la pantalla principal; el ranking existe pero es secundario.
3. Combinar ambas redes (cer + ld) en una sola ficha de estación física cuando
   compartan nombre — ningún competidor lo hace.
4. Mostrar explícitamente frescura y «sin datos RT» para no repetir el patrón
   de retrasos fantasma.
