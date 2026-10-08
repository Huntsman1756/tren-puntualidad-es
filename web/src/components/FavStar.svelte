<script>
  import { onMount } from 'svelte';
  import { favStations, toggleFavStation, isFavStation } from '../lib/favs';
  export let k;
  export let name;
  let fav = false;
  onMount(() => { fav = isFavStation(k); });
  function toggle() {
    fav = toggleFavStation({ key: k, name });
  }
</script>
<button class="star" class:on={fav} on:click={toggle}
        aria-pressed={fav}
        aria-label={fav ? `Quitar ${name} de favoritos` : `Añadir ${name} a favoritos`}
        title={fav ? 'Quitar de favoritos' : 'Guardar en favoritos'}>
  {fav ? '★' : '☆'}
</button>
<style>
  .star { background: none; border: 0; font-size: 1.4rem; cursor: pointer;
          color: var(--muted); padding: 0 .2rem; }
  .star.on { color: var(--warn); }
  .star:hover { color: var(--warn); }
</style>
