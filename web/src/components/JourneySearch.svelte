<script>
  import SearchBox from './SearchBox.svelte';

  let from = null;
  let to = null;

  function go() {
    if (from && to) {
      window.location.href = `/trayecto?from=${from.feed}:${from.stop_id}&to=${to.feed}:${to.stop_id}`;
    }
  }
</script>

<div class="journey">
  <SearchBox placeholder="Origen" onSelect={(s) => { from = s; go(); }} />
  {#if from}
    <p class="sel">De: <strong>{from.name}</strong> <span class="muted">({from.network})</span></p>
  {/if}
  <SearchBox placeholder="Destino" onSelect={(s) => { to = s; go(); }} />
  {#if to}
    <p class="sel">A: <strong>{to.name}</strong> <span class="muted">({to.network})</span></p>
  {/if}
  {#if from && to && from.feed !== to.feed}
    <p class="warn">El origen y el destino pertenecen a redes distintas; de momento no se pueden combinar.</p>
  {/if}
</div>

<style>
  .journey { display: grid; gap: .6rem; }
  .sel { margin: 0; font-size: .9rem; }
  .warn { color: var(--warn); font-size: .85rem; margin: 0; }
</style>
