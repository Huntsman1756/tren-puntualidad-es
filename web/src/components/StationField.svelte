<script>
  import { createEventDispatcher } from 'svelte';
  import { PUBLIC_API, stopsKey } from '../lib/api';
  import { favStations } from '../lib/favs';

  export let label = 'Estación';
  export let placeholder = 'Nombre de estación';
  export let value = null;          // StationGroup | null
  export let autoFocus = false;
  export let inputId = 'station-field';

  const dispatch = createEventDispatcher();
  let q = value?.name || '';
  let results = [];
  let open = false;
  let active = -1;
  let timer;
  let listEl;
  let favs = favStations();

  $: if (value && !q) q = value.name;

  function pick(g) {
    value = g;
    q = g.name;
    open = false;
    dispatch('select', g);
  }

  function onInput() {
    if (value && q !== value.name) { value = null; dispatch('select', null); }
    clearTimeout(timer);
    if (q.trim().length < 2) { results = []; open = !value && favs.length > 0; return; }
    timer = setTimeout(async () => {
      try {
        const r = await fetch(
          `${PUBLIC_API}/api/v1/stations/search?q=${encodeURIComponent(q.trim())}`);
        results = await r.json();
        open = true; active = -1;
      } catch { /* mantener resultados anteriores */ }
    }, 160);
  }

  function onKey(e) {
    if (!open) return;
    const n = displayItems.length;
    if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % n; scrollActive(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); active = (active - 1 + n) % n; scrollActive(); }
    if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(displayItems[active]); }
    if (e.key === 'Escape') open = false;
  }

  function scrollActive() {
    listEl?.children[active]?.scrollIntoView({ block: 'nearest' });
  }

  $: displayItems = q.trim().length < 2
    ? favs.map((f) => ({ name: f.name, _key: f.key, networks: null }))
    : results;

  function pickItem(it) {
    if (it._key) {
      // favorito: reconstruir grupo mínimo
      const stops = it._key.split(',').map((p) => {
        const [feed, stop_id] = p.split(':');
        return { feed, stop_id };
      });
      return pick({ name: it.name, stops });
    }
    pick(it);
  }
</script>

<div class="field">
  <label class="sr-only" for={inputId}>{label}</label>
  <div class="wrap-input">
    <input id={inputId} type="text" role="combobox" aria-expanded={open}
      aria-controls={inputId + '-list'} aria-autocomplete="list"
      {placeholder} autocomplete="off" bind:value={q}
      autofocus={autoFocus}
      on:input={onInput} on:keydown={onKey}
      on:focus={() => { if (!q.trim() && favs.length) open = true; }}
      on:blur={() => setTimeout(() => (open = false), 150)} />
    {#if value}<span class="ok-mark" aria-hidden="true">✓</span>{/if}
  </div>
  {#if open && displayItems.length}
    <ul class="list" role="listbox" id={inputId + '-list'} bind:this={listEl}>
      {#if q.trim().length < 2}<li class="fav-hint" aria-hidden="true">Tus favoritas</li>{/if}
      {#each displayItems as it, i}
        <li role="option" aria-selected={i === active}
            class:active={i === active}
            on:mousedown|preventDefault={() => pickItem(it)}>
          <span class="st-name">{it.name}
            {#if it.provincia}<span class="st-prov">{it.provincia}</span>{/if}</span>
          {#if it.networks}<span class="st-net">{it.networks.join(' + ')}</span>{/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .field { position: relative; }
  .wrap-input { position: relative; }
  .ok-mark { position: absolute; right: .7rem; top: 50%; transform: translateY(-50%);
             color: var(--ok); font-weight: 700; }
  .list { position: absolute; z-index: 30; left: 0; right: 0; margin: 4px 0 0;
          list-style: none; padding: 0; background: var(--card);
          border: 1px solid var(--border); border-radius: 10px;
          max-height: 260px; overflow-y: auto; box-shadow: 0 8px 24px rgba(0,0,0,.35); }
  .list li { padding: .6rem .8rem; cursor: pointer; display: flex;
             justify-content: space-between; gap: .6rem; align-items: baseline; }
  .list li.active, .list li:hover { background: var(--accent-dim); }
  .st-net { font-size: .72rem; color: var(--muted); text-align: right; flex-shrink: 0; }
  .st-prov { font-size: .72rem; color: var(--muted); margin-left: .4rem; }
  .fav-hint { font-size: .72rem; color: var(--muted); cursor: default;
              text-transform: uppercase; letter-spacing: .05em; padding-bottom: .3rem !important; }
</style>
