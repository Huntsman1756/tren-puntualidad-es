<script>
  import { onMount, onDestroy } from 'svelte';
  import { PUBLIC_API } from '../lib/api';
  import { fmtTime, fmtDelay } from '../lib/format';

  export let feed;
  export let stopId;
  export let initialItems = [];
  export let initialKind = 'departures';

  let kind = initialKind;
  let items = initialItems;
  let updatedAt = Date.now();
  let loading = false;
  let timer;

  async function refresh(k = kind) {
    loading = true;
    try {
      const r = await fetch(
        `${PUBLIC_API}/api/stations/${feed}/${stopId}/board?kind=${k}&minutes=180`);
      const d = await r.json();
      items = d.items; kind = k; updatedAt = Date.now();
    } finally { loading = false; }
  }

  onMount(() => { timer = setInterval(() => refresh(), 30000); });
  onDestroy(() => clearInterval(timer));
</script>

<div class="tabs">
  <button class:on={kind === 'departures'} on:click={() => refresh('departures')}>Salidas</button>
  <button class:on={kind === 'arrivals'} on:click={() => refresh('arrivals')}>Llegadas</button>
  <span class="upd muted">
    {loading ? 'actualizando…' : `actualizado ${new Date(updatedAt).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`}
  </span>
</div>

{#if items.length === 0}
  <p class="muted">No hay trenes {kind === 'departures' ? 'con salida' : 'con llegada'} prevista en las próximas 3 horas.</p>
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
      {#each items as it (it.trip_id)}
        {@const d = fmtDelay(it.delay_sec)}
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
          <td>
            {#if it.cancelled}
              <span class="badge cancel">suprimido</span>
            {:else}
              <span class="badge {d.cls}">{d.text}</span>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
  .tabs { display: flex; align-items: center; gap: .5rem; margin-bottom: .6rem; }
  .tabs button { background: #11181f; color: var(--muted); border: 1px solid var(--border);
                 border-radius: 8px; padding: .35rem .9rem; font-size: .85rem; cursor: pointer; }
  .tabs button.on { background: var(--accent); color: #06101c; border-color: transparent; font-weight: 600; }
  .upd { margin-left: auto; font-size: .75rem; }
  .est { font-size: .8rem; }
</style>
