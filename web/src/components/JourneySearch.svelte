<script>
  import SearchBox from './SearchBox.svelte';
  import { stopsKey } from '../lib/api';

  let from = null;
  let to = null;

  function go() {
    if (from && to) {
      window.location.href =
        `/trayecto?from=${encodeURIComponent(stopsKey(from.stops))}&to=${encodeURIComponent(stopsKey(to.stops))}`;
    }
  }
</script>

<div class="journey">
  <SearchBox placeholder="Origen" onSelect={(s) => { from = s; if (to) go(); }} />
  {#if from}
    <p class="sel">De: <strong>{from.name}</strong></p>
  {/if}
  <SearchBox placeholder="Destino" onSelect={(s) => { to = s; go(); }} />
  {#if to}
    <p class="sel">A: <strong>{to.name}</strong></p>
  {/if}
</div>

<style>
  .journey { display: grid; gap: .6rem; }
  .sel { margin: 0; font-size: .9rem; }
</style>
