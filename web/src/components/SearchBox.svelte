<script>
  import { PUBLIC_API } from '../lib/api';

  export let placeholder = '¿Qué estación quieres consultar?';
  export let onSelect = null;
  export let autoFocus = false;

  let q = '';
  let results = [];
  let open = false;
  let timer;
  let active = -1;

  async function search() {
    clearTimeout(timer);
    if (q.trim().length < 2) { results = []; open = false; return; }
    timer = setTimeout(async () => {
      try {
        const r = await fetch(`${PUBLIC_API}/api/stations/search?q=${encodeURIComponent(q.trim())}`);
        results = await r.json();
        open = true; active = -1;
      } catch { results = []; }
    }, 200);
  }

  function pick(s) {
    open = false;
    if (onSelect) { onSelect(s); return; }
    window.location.href = `/estacion/${s.feed}/${s.stop_id}`;
  }

  function onKey(e) {
    if (!open) return;
    if (e.key === 'ArrowDown') { active = Math.min(active + 1, results.length - 1); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); e.preventDefault(); }
    else if (e.key === 'Enter' && active >= 0) pick(results[active]);
    else if (e.key === 'Escape') open = false;
  }
</script>

<div class="search">
  <input type="text" bind:value={q} on:input={search} on:keydown={onKey}
         on:focus={() => results.length && (open = true)}
         {placeholder} autocomplete="off" autofocus={autoFocus || null} />
  {#if open && results.length}
    <ul>
      {#each results as s, i}
        <li>
          <button class:active={i === active} on:mousedown|preventDefault={() => pick(s)}>
            <span class="name">{s.name}</span>
            <span class="net">{s.network}</span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .search { position: relative; }
  ul { position: absolute; z-index: 20; left: 0; right: 0; margin: .3rem 0 0; padding: 0;
       list-style: none; background: var(--card); border: 1px solid var(--border);
       border-radius: 10px; overflow: hidden; box-shadow: 0 8px 30px #0008; }
  button { display: flex; justify-content: space-between; gap: .8rem; width: 100%;
           padding: .65rem .8rem; background: none; border: 0; color: var(--text);
           font-size: .95rem; cursor: pointer; text-align: left; }
  button:hover, button.active { background: #242f3b; }
  .net { color: var(--muted); font-size: .78rem; white-space: nowrap; }
</style>
