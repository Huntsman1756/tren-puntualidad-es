<script>
  import { onMount } from 'svelte';
  import { favStations, toggleFavStation, history } from '../lib/favs';
  let stations = [];
  let hist = [];
  let ready = false;
  onMount(() => { stations = favStations(); hist = history(); ready = true; });
  function remove(s) { toggleFavStation(s); stations = favStations(); }
</script>

{#if !ready}
  <p class="muted">Cargando…</p>
{:else}
  {#if stations.length}
    <ul class="list">
      {#each stations as s (s.key)}
        <li><a href={`/estacion/${s.key}`}>{s.name}</a>
          <button class="tool danger" on:click={() => remove(s)} aria-label={`Quitar ${s.name}`}>✕</button></li>
      {/each}
    </ul>
  {:else}
    <p class="muted">Sin estaciones favoritas. Pulsa ☆ en la página de una estación para
      guardarla.</p>
  {/if}
  {#if hist.length}
    <h3>Consultadas recientemente</h3>
    <ul class="list">
      {#each hist as h (h.key)}<li><a href={`/estacion/${h.key}`}>{h.name}</a></li>{/each}
    </ul>
  {/if}
{/if}

<style>
  .list { list-style: none; padding: 0; margin: 0; }
  .list li { display: flex; justify-content: space-between; align-items: center;
             padding: .45rem 0; border-top: 1px solid var(--border); min-height: 40px; }
  h3 { font-size: .85rem; color: var(--muted); margin: .9rem 0 .3rem; }
  .tool { font-size: .78rem; color: var(--muted); border: 1px solid var(--border);
          background: transparent; border-radius: 7px; padding: .25rem .6rem; cursor: pointer; }
  .tool.danger:hover { color: var(--bad); border-color: var(--bad); }
</style>
