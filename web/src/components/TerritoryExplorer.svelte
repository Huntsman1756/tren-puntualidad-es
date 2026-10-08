<script>
  import { onMount } from 'svelte';
  import { PUBLIC_API } from '../lib/api';

  let ccaaList = [];
  let ccaa = '';
  let provinces = [];
  let prov = '';
  let stations = { total: 0, items: [] };
  let q = '';
  let page = 1;
  let loading = false;
  let err = null;

  onMount(async () => {
    try {
      const r = await fetch(`${PUBLIC_API}/api/v1/geo/ccaa`);
      ccaaList = await r.json();
    } catch { err = 'No se pudo cargar el catálogo territorial.'; }
  });

  async function loadProv() {
    provinces = []; prov = ''; stations = { total: 0, items: [] };
    if (!ccaa) return;
    const r = await fetch(`${PUBLIC_API}/api/v1/geo/ccaa/${ccaa}`);
    const d = await r.json();
    provinces = d.provinces;
    await loadStations();
  }

  async function loadStations() {
    if (!ccaa && !prov && !q.trim()) { stations = { total: 0, items: [] }; return; }
    loading = true; err = null;
    try {
      const p = new URLSearchParams({ page: String(page), size: '60' });
      if (ccaa) p.set('ccaa', ccaa);
      if (prov) p.set('provincia', prov);
      if (q.trim()) p.set('q', q.trim());
      const r = await fetch(`${PUBLIC_API}/api/v1/geo/estaciones?${p}`);
      stations = await r.json();
    } catch { err = 'Error cargando estaciones.'; }
    finally { loading = false; }
  }

  function debounceQ() { page = 1; clearTimeout(window._tq); window._tq = setTimeout(loadStations, 250); }

  function pages() { return Math.ceil(stations.total / 60); }
</script>

<div class="expl">
  <div class="row">
    <label>Comunidad
      <select bind:value={ccaa} on:change={() => { page = 1; loadProv(); }}
              aria-label="Comunidad autónoma">
        <option value="">Todas</option>
        {#each ccaaList as c}<option value={c.slug}>{c.name} ({c.stations})</option>{/each}
      </select>
    </label>
    <label>Provincia
      <select bind:value={prov} on:change={() => { page = 1; loadStations(); }}
              disabled={!ccaa} aria-label="Provincia">
        <option value="">Todas</option>
        {#each provinces as p}<option value={p.slug}>{p.name} ({p.stations})</option>{/each}
      </select>
    </label>
    <label class="f">Buscar
      <input type="text" placeholder="Nombre o población…" bind:value={q}
             on:input={debounceQ} aria-label="Buscar estación por nombre o población" />
    </label>
  </div>

  {#if err}<p class="muted" role="alert">{err}</p>{/if}

  {#if stations.total}
    <p class="muted count">{stations.total} estaciones</p>
    <ul class="stlist" aria-label="Estaciones">
      {#each stations.items as st}
        <li>
          <a href="/estacion/{st.feeds.map(f => f + ':' + st.stop_id).join(',')}">
            <span class="n">{st.name}</span>
            <span class="loc">
              {#if st.poblacion && st.poblacion !== st.name}{st.poblacion} · {/if}
              {st.provincia || '—'}{st.ccaa ? ` · ${st.ccaa}` : ''}
            </span>
            <span class="feeds">{st.feeds.join(' + ')}</span>
          </a>
        </li>
      {/each}
    </ul>
    {#if pages() > 1}
      <div class="pager">
        <button disabled={page <= 1} on:click={() => { page--; loadStations(); }}>← Anterior</button>
        <span>{page} / {pages()}</span>
        <button disabled={page >= pages()} on:click={() => { page++; loadStations(); }}>Siguiente →</button>
      </div>
    {/if}
  {:else if (ccaa || prov || q) && !loading}
    <p class="muted">Sin resultados con esos filtros.</p>
  {:else}
    <p class="muted">Elige una comunidad autónoma para explorar sus estaciones,
      o busca directamente por nombre o población.</p>
  {/if}
</div>

<style>
  .row { display: flex; gap: .7rem; flex-wrap: wrap; }
  .row label { display: flex; flex-direction: column; gap: .25rem;
               font-size: .78rem; color: var(--muted); flex: 1; min-width: 140px; }
  .row .f { flex: 2; }
  .count { font-size: .78rem; margin: .6rem 0 .3rem; }
  .stlist { list-style: none; margin: 0; padding: 0; display: flex;
            flex-direction: column; }
  .stlist a { display: flex; gap: .6rem; align-items: baseline;
              padding: .5rem .3rem; border-top: 1px solid var(--border);
              color: var(--text); flex-wrap: wrap; }
  .stlist a:hover { background: var(--card2); }
  .n { font-weight: 600; min-width: 0; }
  .loc { flex: 1; color: var(--muted); font-size: .82rem; }
  .feeds { font-size: .7rem; color: var(--accent); text-transform: uppercase; }
  .pager { display: flex; gap: 1rem; align-items: center; justify-content: center;
           margin-top: .8rem; }
  .pager button { border: 1px solid var(--border); background: var(--card2);
                  color: var(--text); border-radius: 8px; padding: .4rem .9rem;
                  cursor: pointer; }
  .pager button:disabled { opacity: .4; cursor: default; }
</style>
