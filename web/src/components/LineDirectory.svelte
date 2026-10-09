<script>
  // Directorio de líneas de Cercanías/Rodalies: buscador por código o núcleo.
  // Se renderiza completo en el servidor con el `q` de la URL (funciona sin JS:
  // el formulario es GET ?q=); con JS el filtro es instantáneo.
  import LineBadge from './LineBadge.svelte';

  export let groups = [];     // [{code, items:[{key, nucleo, brand, line, url, variants, bus, hay}]}]
  export let initialQ = '';
  export let total = 0;       // líneas (núcleo × código) en el directorio completo

  let q = initialQ;

  const norm = (s) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();

  $: needle = norm(q);
  $: shown = needle
    ? groups
        .map((g) => ({ code: g.code, items: g.items.filter((it) => it.hay.includes(needle)) }))
        .filter((g) => g.items.length)
    : groups;
  $: shownCount = shown.reduce((a, g) => a + g.items.length, 0);
  $: shownNucleos = new Set(shown.flatMap((g) => g.items.map((it) => it.key.split(':')[0]))).size;

  // mantiene ?q= en la URL (enlazable) sin recargar
  function sync() {
    try {
      const u = new URL(location.href);
      if (q.trim()) u.searchParams.set('q', q.trim()); else u.searchParams.delete('q');
      history.replaceState(null, '', u);
    } catch { /* sin historial */ }
  }
</script>

<form class="search card tight" role="search" method="get" action="/lineas"
      on:submit={(e) => { e.preventDefault(); sync(); }}>
  <label for="linea-q">Buscar línea o núcleo</label>
  <div class="row">
    <input id="linea-q" name="q" type="search" bind:value={q} on:input={sync}
      autocomplete="off" placeholder="p. ej. C1, C4, R2, Madrid" />
    <button type="submit">Buscar</button>
  </div>
  <p class="muted small" aria-live="polite">
    {#if needle}
      {shownCount} {shownCount === 1 ? 'línea' : 'líneas'} en {shownNucleos}
      {shownNucleos === 1 ? 'núcleo' : 'núcleos'} para «{q.trim()}».
    {:else}
      {total} líneas de Cercanías y Rodalies, agrupadas por código.
    {/if}
  </p>
</form>

<div class="dir">
  {#each shown as g (g.code)}
    <section class="grp" aria-labelledby={`g-${g.code}`}>
      <h2 id={`g-${g.code}`}>{g.code}
        <small class="muted">{g.items.length} {g.items.length === 1 ? 'núcleo' : 'núcleos'}</small></h2>
      <ul class="rows">
        {#each g.items as it (it.key)}
          <li>
            <a class="row" href={it.url}>
              <LineBadge line={it.line} showNucleo={false} link={false} />
              <span class="nm">{it.brand} {it.nucleo}</span>
              {#if it.variants}<span class="var">variantes: {it.variants}</span>{/if}
              {#if it.bus}<span class="var">bus</span>{/if}
            </a>
          </li>
        {/each}
      </ul>
    </section>
  {:else}
    <p class="card muted">No hay líneas que coincidan con «{q.trim()}». Prueba con el código
      (C1, R2…) o con el nombre del núcleo.</p>
  {/each}
</div>

<style>
  .search label { display: block; font-weight: 600; margin-bottom: .3rem; }
  .search .row { display: flex; gap: .5rem; }
  .search input { flex: 1; min-width: 0; padding: .55rem .7rem; border-radius: 8px;
    border: 1px solid var(--border); background: var(--card2); color: var(--text); font-size: 1rem; }
  .search button { padding: .5rem .95rem; border-radius: 8px; border: 0; background: var(--accent);
    color: var(--accent-fg); font-weight: 600; cursor: pointer; min-height: 40px; }
  .small { font-size: .8rem; margin: .4rem 0 0; }
  .dir { display: flex; flex-direction: column; gap: .8rem; }
  .grp h2 { font-size: 1rem; margin: .2rem 0 .4rem; display: flex; gap: .6rem; align-items: baseline; }
  .grp h2 small { font-size: .78rem; font-weight: 400; }
  .rows { list-style: none; margin: 0; padding: 0; border: 1px solid var(--border);
    border-radius: 12px; background: var(--card); overflow: hidden; }
  .rows li + li { border-top: 1px solid var(--border); }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: .35rem .7rem;
    padding: .55rem .7rem; min-height: 44px; color: var(--text); min-width: 0; }
  .row:hover { background: var(--card2); }
  .nm { flex: 1 1 10rem; min-width: 0; overflow-wrap: anywhere; }
  .var { font-size: .76rem; color: var(--muted); }
</style>
