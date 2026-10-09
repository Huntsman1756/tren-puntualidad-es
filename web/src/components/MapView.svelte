<script>
  // Mapa de núcleo: trazados validados + estaciones + posiciones publicadas.
  // Leaflet se carga solo en el navegador. Sin interpolación: cada tren se
  // pinta donde Renfe lo publicó, con su antigüedad.
  import { onMount, onDestroy } from 'svelte';
  import 'leaflet/dist/leaflet.css';
  import { PUBLIC_API } from '../lib/api';
  import { ageText, fmtDelay } from '../lib/format';

  export let nucleo;
  export let linea = '';
  export let initial = null;

  let el;
  let map, L, trainLayer;
  let data = initial;
  let error = '';
  let timer;

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function drawTrains() {
    if (!map || !data || !trainLayer) return;
    trainLayer.clearLayers();
    for (const t of data.trains) {
      const d = fmtDelay(t.delay_sec);
      const fresh = t.freshness === 'fresh';
      const m = L.circleMarker([t.lat, t.lon], {
        radius: fresh ? 8 : 6, weight: 2,
        color: fresh ? '#0b63ce' : '#7a8794', fillColor: fresh ? '#4da3ff' : '#c9d1d9',
        fillOpacity: fresh ? .95 : .55, dashArray: fresh ? null : '3 3',
      });
      const code = t.line_info?.code ?? '';
      m.bindPopup(`<strong>${esc(code)} · tren ${esc(t.train_number || t.trip_id)}</strong><br>`
        + `${t.delay_sec != null ? esc(d.text) + ' · ' : ''}posición ${ageText(t.ts)}`
        + ` (${t.source === 'visor' ? 'visor Renfe' : 'GTFS-RT'})<br>`
        + `<a href="/tren/cer/${encodeURIComponent(t.trip_id)}">Ver recorrido</a>`);
      m.bindTooltip(`${code} ${t.train_number || ''}`.trim());
      trainLayer.addLayer(m);
    }
  }

  async function refresh() {
    try {
      const q = linea ? `?linea=${encodeURIComponent(linea)}` : '';
      const r = await fetch(`${PUBLIC_API}/api/v1/mapa/${nucleo}${q}`);
      if (!r.ok) throw new Error(String(r.status));
      data = await r.json();
      error = '';
    } catch {
      error = 'Error de consulta al actualizar posiciones; se muestran las últimas válidas.';
      return;
    }
    drawTrains();
  }

  onMount(async () => {
    L = (await import('leaflet')).default;
    map = L.map(el, { scrollWheelZoom: false, preferCanvas: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · datos Renfe (CC-BY 4.0)',
    }).addTo(map);
    if (!data) await refresh();
    const bounds = [];
    for (const s of data?.shapes || []) {
      L.polyline(s.coords, { color: s.color && s.color !== 'FFFFFF' ? `#${s.color}` : '#0b63ce',
                             weight: 4, opacity: .85 }).bindTooltip(s.line || '').addTo(map);
    }
    for (const s of data?.stations || []) {
      bounds.push([s.lat, s.lon]);
      L.circleMarker([s.lat, s.lon], { radius: 3.5, color: '#1b2430', weight: 1,
                                       fillColor: '#fff', fillOpacity: 1 })
        .bindTooltip(s.name).on('click', () => { location.href = `/estacion/${s.key}`; })
        .addTo(map);
    }
    if (bounds.length) map.fitBounds(bounds, { padding: [20, 20] });
    else map.setView([40.4, -3.7], 6);
    trainLayer = L.layerGroup().addTo(map);
    drawTrains();
    timer = setInterval(refresh, 30000);
  });
  onDestroy(() => { clearInterval(timer); map?.remove(); });
</script>

<div class="map" bind:this={el} role="region" aria-label="Mapa del núcleo"></div>
{#if error}<p class="err" role="alert">{error}</p>{/if}
{#if data}
  <p class="legend muted">
    <span class="dot fresh"></span> posición publicada hace ≤2 min ·
    <span class="dot recent"></span> hace 2–10 min ·
    {data.trains.length} trenes con posición{data.stale_hidden ? `, ${data.stale_hidden} con posición antigua (no se muestran)` : ''}.
  </p>
{/if}

<style>
  .map { height: 62vh; min-height: 340px; border-radius: 12px; border: 1px solid var(--border);
         z-index: 0; }
  .legend { font-size: .78rem; margin: .5rem 0 0; }
  .dot { display: inline-block; width: .7rem; height: .7rem; border-radius: 50%;
         vertical-align: middle; border: 2px solid #0b63ce; background: #4da3ff; }
  .dot.recent { border: 2px dashed #7a8794; background: #c9d1d9; }
  .err { color: var(--warn); font-size: .82rem; }
</style>
