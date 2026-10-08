<script>
  import { onMount } from 'svelte';
  import { PUBLIC_API, stopsKey } from '../lib/api';
  import { getFavorites, toggleFavorite, isFavorite, pushHistory } from '../lib/favs';

  export let placeholder = 'Busca una estación o un nº de tren';
  export let onSelect = null;
  export let autoFocus = false;

  let q = '';
  let results = [];
  let trainNum = null;
  let open = false;
  let timer;
  let active = -1;
  let favs = [];
  let listEl;

  onMount(() => { favs = getFavorites(); });

  $: isTrainQuery = /^\d{4,6}$/.test(q.trim());

  async function search() {
    clearTimeout(timer);
    if (q.trim().length < 2) { results = []; open = favs.length > 0; return; }
    timer = setTimeout(async () => {
      try {
        const r = await fetch(`${PUBLIC_API}/api/v1/stations/search?q=${encodeURIComponent(q.trim())}`);
        results = await r.json();
        trainNum = isTrainQuery ? q.trim() : null;
        open = true; active = -1;
      } catch { results = []; }
    }, 180);
  }

  function pick(g) {
    open = false;
    pushHistory({ name: g.name, key: stopsKey(g.stops) });
    if (onSelect) { onSelect(g); return; }
    window.location.href = `/estacion/${stopsKey(g.stops)}`;
  }

  function pickTrain() {
    window.location.href = `/tren-numero/${trainNum}`;
  }

  function onKey(e) {
    if (!open) return;
    const n = results.length + (trainNum ? 1 : 0);
    if (e.key === 'ArrowDown') { active = Math.min(active + 1, n - 1); e.preventDefault(); scrollActive(); }
    else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); e.preventDefault(); scrollActive(); }
    else if (e.key === 'Enter') {
      if (active >= 0 && active < results.length) pick(results[active]);
      else if (trainNum && active === results.length) pickTrain();
    }
    else if (e.key === 'Escape') open = false;
  }

  function scrollActive() {
    requestAnimationFrame(() => {
      listEl?.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
    });
  }

  function fav(g, e) {
    e.stopPropagation();
    toggleFavorite({ name: g.name, key: stopsKey(g.stops) });
    favs = getFavorites();
  }
</script>

<div class="search" role="combobox" aria-expanded={open} aria-haspopup="listbox">
  <input type="text" bind:value={q} on:input={search} on:keydown={onKey}
         on:focus={() => { if (!q && favs.length) open = true; else if (results.length) open = true; }}
         {placeholder} autocomplete="off" role="searchbox"
         aria-label={placeholder} aria-activedescendant={active >= 0 ? `opt-${active}` : undefined}
         autofocus={autoFocus || null} />
  {#if open}
    <ul role="listbox" bind:this={listEl}>
      {#if !q && favs.length}
        <li class="hdr" aria-hidden="true">Favoritas</li>
        {#each favs as f}
          <li role="option">
            <button on:mousedown|preventDefault={() => { open = false; window.location.href = `/estacion/${f.key}`; }}>
              <span class="name">★ {f.name}</span>
            </button>
          </li>
        {/each}
      {/if}
      {#each results as g, i}
        <li role="option" id={`opt-${i}`} aria-selected={i === active}>
          <button class:active={i === active} on:mousedown|preventDefault={() => pick(g)}>
            <span class="name">{g.name}</span>
            <span class="right">
              <span class="net">{(g.networks || []).join(' + ')}</span>
              <span class="star" role="button" tabindex="-1" aria-label="marcar favorita"
                    on:mousedown|preventDefault={(e) => fav(g, e)}>
                {isFavorite(stopsKey(g.stops)) ? '★' : '☆'}
              </span>
            </span>
          </button>
        </li>
      {/each}
      {#if trainNum}
        <li role="option" id={`opt-${results.length}`} aria-selected={active === results.length}>
          <button class:active={active === results.length} on:mousedown|preventDefault={pickTrain}>
            <span class="name">Tren nº {trainNum}</span>
            <span class="net">buscar tren</span>
          </button>
        </li>
      {/if}
      {#if q.trim().length >= 2 && !results.length && !trainNum}
        <li class="hdr">Sin resultados</li>
      {/if}
    </ul>
  {/if}
</div>

<style>
  .search { position: relative; }
  ul { position: absolute; z-index: 20; left: 0; right: 0; margin: .3rem 0 0; padding: 0;
       list-style: none; background: var(--card); border: 1px solid var(--border);
       border-radius: 10px; overflow: hidden; box-shadow: 0 8px 30px #0008;
       max-height: 60vh; overflow-y: auto; }
  .hdr { padding: .5rem .8rem .2rem; color: var(--muted); font-size: .75rem;
         text-transform: uppercase; letter-spacing: .05em; }
  button { display: flex; justify-content: space-between; gap: .8rem; width: 100%;
           padding: .65rem .8rem; background: none; border: 0; color: var(--text);
           font-size: .95rem; cursor: pointer; text-align: left; }
  button:hover, button.active { background: #242f3b; }
  .right { display: flex; gap: .6rem; align-items: center; }
  .net { color: var(--muted); font-size: .78rem; white-space: nowrap; }
  .star { color: var(--warn); font-size: 1.05rem; cursor: pointer; padding: 0 .2rem; }
</style>
