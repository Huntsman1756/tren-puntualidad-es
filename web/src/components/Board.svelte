<script>
  import { onMount, onDestroy } from 'svelte';
  import { PUBLIC_API } from '../lib/api';
  import { fmtTime, fmtDelay } from '../lib/format';

  export let stops;                    // "cer:18000" o "cer:18000,ld:18000"
  export let initialItems = [];
  export let initialKind = 'departures';

  let kind = initialKind;
  let items = initialItems;
  let updatedAt = Date.now();
  let loading = false;
  let timer;
  let lineFilter = '';
  let destFilter = '';
  let sort = 'time';

  $: lines = [...new Set(items.map((i) => i.line).filter(Boolean))].sort();
  $: filtered = items
      .filter((i) => !lineFilter || i.line === lineFilter)
      .filter((i) => !destFilter ||
        ((i.destination || '') + ' ' + (i.origin || ''))
          .toLowerCase().includes(destFilter.toLowerCase()))
      .sort((a, b) => sort === 'delay'
        ? (b.delay_sec ?? -1) - (a.delay_sec ?? -1)
        : a.estimated - b.estimated);

  async function refresh(k = kind) {
    loading = true;
    try {
      const r = await fetch(
        `${PUBLIC_API}/api/v1/stations/board?stops=${encodeURIComponent(stops)}&kind=${k}&minutes=180`);
      const d = await r.json();
      items = d.items; kind = k; updatedAt = Date.now();
    } finally { loading = false; }
  }

  function srcLabel(it, d) {
    if (it.cancelled) return { text: 'suprimido', cls: 'cancel' };
    if (it.delay_source === 'observed') return { text: `${d.text} · obs.`, cls: d.cls === 'ok' ? 'ok' : d.cls };
    if (it.realtime) return { text: `${d.text} · est.`, cls: d.cls };
    return { text: 'programado', cls: 'nodata' };
  }

  onMount(() => { timer = setInterval(() => refresh(), 30000); });
  onDestroy(() => clearInterval(timer));
</script>

<div class="tabs" role="tablist">
  <button role="tab" aria-selected={kind === 'departures'} class:on={kind === 'departures'}
          on:click={() => refresh('departures')}>Salidas</button>
  <button role="tab" aria-selected={kind === 'arrivals'} class:on={kind === 'arrivals'}
          on:click={() => refresh('arrivals')}>Llegadas</button>
  <span class="upd muted" aria-live="polite">
    {loading ? 'actualizando…' : `actualizado ${new Date(updatedAt).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`}
  </span>
</div>

{#if items.length}
  <div class="filters">
    {#if lines.length > 1}
      <select bind:value={lineFilter} aria-label="Filtrar por línea">
        <option value="">Todas las líneas</option>
        {#each lines as l}<option value={l}>{l}</option>{/each}
      </select>
    {/if}
    <input type="text" bind:value={destFilter} aria-label="Filtrar por destino u origen"
           placeholder={kind === 'departures' ? 'Filtrar destino…' : 'Filtrar origen…'} />
    <select bind:value={sort} aria-label="Ordenar">
      <option value="time">Por hora</option>
      <option value="delay">Por retraso</option>
    </select>
  </div>
{/if}

{#if filtered.length === 0}
  <p class="muted">
    {items.length === 0
      ? `No hay trenes ${kind === 'departures' ? 'con salida' : 'con llegada'} prevista en las próximas 3 horas.`
      : 'Ningún tren coincide con los filtros.'}
  </p>
{:else}
  <table class="board">
    <thead>
      <tr>
        <th>{kind === 'departures' ? 'Salida' : 'Llegada'}</th>
        <th>Línea</th>
        <th>{kind === 'departures' ? 'Destino' : 'Origen'}</th>
        <th class="hide-sm">Tren</th>
        <th>Vía</th>
        <th>Estado</th>
      </tr>
    </thead>
    <tbody>
      {#each filtered as it (it.feed + it.trip_id)}
        {@const d = fmtDelay(it.delay_sec)}
        {@const s = srcLabel(it, d)}
        <tr class:cancelled={it.cancelled}>
          <td>
            <strong>{fmtTime(it.scheduled)}</strong>
            {#if it.realtime && it.estimated !== it.scheduled}
              <br /><span class="muted est">→ {fmtTime(it.estimated)}</span>
            {/if}
          </td>
          <td>{#if it.line}<span class="line-tag">{it.line}</span>{/if}</td>
          <td>{kind === 'departures' ? (it.destination || '—') : (it.origin || '—')}</td>
          <td class="hide-sm">
            <a href="/tren/{it.feed}/{encodeURIComponent(it.trip_id)}">{it.train_number || it.trip_id}</a>
          </td>
          <td>{it.platform || '—'}</td>
          <td><span class="badge {s.cls}">{s.text}</span></td>
        </tr>
      {/each}
    </tbody>
  </table>
  <p class="muted legend">obs. = retraso observado por Renfe · est. = estimación · programado = sin dato en tiempo real</p>
{/if}

<style>
  .tabs { display: flex; align-items: center; gap: .5rem; margin-bottom: .6rem; }
  .tabs button { background: #11181f; color: var(--muted); border: 1px solid var(--border);
                 border-radius: 8px; padding: .35rem .9rem; font-size: .85rem; cursor: pointer; }
  .tabs button.on { background: var(--accent); color: #06101c; border-color: transparent; font-weight: 600; }
  .upd { margin-left: auto; font-size: .75rem; }
  .est { font-size: .8rem; }
  .filters { display: flex; gap: .5rem; margin-bottom: .6rem; flex-wrap: wrap; }
  .filters select, .filters input { width: auto; flex: 1; min-width: 120px;
                                    font-size: .85rem; padding: .4rem .6rem; }
  .legend { font-size: .75rem; margin: .6rem 0 0; }
</style>
