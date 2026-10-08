<script>
  import { onMount, onDestroy } from 'svelte';
  import { PUBLIC_API } from '../lib/api';
  import { fmtTime, fmtDelay, ageText } from '../lib/format';
  import { madridToday } from '../lib/dates';
  import LineBadge from './LineBadge.svelte';

  export let stops;                    // "cer:18000" o "cer:18000,ld:18000"
  export let initialItems = [];
  export let initialKind = 'departures';
  export let initialMeta = {};         // { date, scheduled_only, feed_ts }
  export let date = '';                // YYYY-MM-DD — vacío = hoy
  export let time = '';                // HH:MM — vacío = ahora/todo el día futuro

  let kind = initialKind;
  let items = initialItems;
  let meta = initialMeta;
  let error = null;
  let loading = false;
  let timer;
  let lineFilter = '';
  let destFilter = '';
  let sort = 'time';
  let onlySemi = false;
  let now = Date.now();

  const isToday = !date || meta.scheduled_only === false;

  const today = madridToday();
  const lineKey = (i) => i.line_info?.label || i.line;
  $: lines = [...new Set(items.map(lineKey).filter(Boolean))].sort();
  $: filtered = items
      .filter((i) => !lineFilter || lineKey(i) === lineFilter)
      .filter((i) => !destFilter ||
        ((i.destination || '') + ' ' + (i.origin || ''))
          .toLowerCase().includes(destFilter.toLowerCase()))
      .sort((a, b) => sort === 'delay'
        ? (b.delay_sec ?? -1) - (a.delay_sec ?? -1)
        : a.estimated - b.estimated);

  // frescura REAL: timestamp del feed RT en servidor, no la hora del fetch
  $: feedAges = Object.entries(meta.feed_ts || {}).map(([k, ts]) => {
    const label = k.replace('rt_trip_updates_', 'RT ').replace('rt_fleet_cer', 'flota');
    return `${label} ${ageText(ts)}`;
  });

  async function refresh(k = kind) {
    loading = true;
    try {
      const p = new URLSearchParams({ stops, kind: k, minutes: '180' });
      if (date) p.set('date', date);
      if (time) p.set('time', time);
      if (onlySemi) p.set('semidirect', 'true');
      const r = await fetch(`${PUBLIC_API}/api/v1/stations/board?${p}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      items = d.items; kind = k; meta = d; error = null; now = Date.now();
    } catch (e) {
      // nunca presentar un fallo de refresco como "no hay trenes"
      error = items.length
        ? 'Error de consulta al actualizar: se muestran los últimos datos válidos.'
        : 'Error de consulta: no se han podido cargar los trenes.';
    } finally { loading = false; }
  }

  function toggleSemi() { onlySemi = !onlySemi; refresh(); }

  function srcLabel(it, d) {
    if (it.cancelled) return { text: 'suprimido', cls: 'cancel' };
    if (meta.scheduled_only) return { text: 'programado', cls: 'nodata' };
    if (it.delay_source === 'observed')
      return { text: `${d.text} · obs.`, cls: d.cls === 'ok' ? 'ok' : d.cls };
    if (it.realtime) return { text: `${d.text} · est.`, cls: d.cls };
    return { text: 'programado', cls: 'nodata' };
  }

  function trainHref(it) {
    const base = `/tren/${it.feed}/${encodeURIComponent(it.trip_id)}`;
    return it.service_date && it.service_date !== today ? `${base}?date=${it.service_date}` : base;
  }

  onMount(() => {
    if (isToday) timer = setInterval(() => refresh(), 30000);
    const t2 = setInterval(() => (now = Date.now()), 30000);
    return () => clearInterval(t2);
  });
  onDestroy(() => clearInterval(timer));
</script>

<div class="tabs" role="tablist" aria-label="Salidas o llegadas">
  <button role="tab" aria-selected={kind === 'departures'} class:on={kind === 'departures'}
          on:click={() => refresh('departures')}>Salidas</button>
  <button role="tab" aria-selected={kind === 'arrivals'} class:on={kind === 'arrivals'}
          on:click={() => refresh('arrivals')}>Llegadas</button>
  <button class="semi-btn" class:on={onlySemi} on:click={toggleSemi}
          aria-pressed={onlySemi}
          title="Solo trenes que omiten 2+ paradas (semidirectos)">Semi</button>
  <span class="upd muted" aria-live="polite">
    {#if meta.scheduled_only}
      Horario programado {meta.date ? `· ${meta.date}` : ''}
    {:else}
      {feedAges.join(' · ') || `actualizado ${ageText(Math.floor(now / 1000))}`}
    {/if}
    {loading ? ' ⟳' : ''}
  </span>
</div>

{#if error}
  <p class="errnote" role="alert">{error}</p>
{/if}

{#if items.length}
  <div class="filters">
    {#if lines.length > 1}
      <select bind:value={lineFilter} aria-label="Filtrar por línea">
        <option value="">Todas las líneas</option>
        {#each lines as l}<option value={l}>{l}</option>{/each}
      </select>
    {/if}
    <input type="text" bind:value={destFilter} aria-label="Filtrar por destino u origen"
           placeholder={kind === 'departures' ? 'Filtrar destino…' : 'Filtrar origen…'} />
    <select bind:value={sort} aria-label="Ordenar">
      <option value="time">Por hora</option>
      <option value="delay">Por retraso</option>
    </select>
  </div>
{/if}

{#if filtered.length === 0 && !(error && !items.length)}
  <p class="muted">
    {items.length === 0
      ? `No hay trenes ${kind === 'departures' ? 'con salida' : 'con llegada'} prevista en la ventana consultada.`
      : 'Ningún tren coincide con los filtros.'}
  </p>
{:else}
  <!-- escritorio: tabla -->
  <table class="board desktop">
    <thead>
      <tr>
        <th>{kind === 'departures' ? 'Salida' : 'Llegada'}</th>
        <th>Línea</th>
        <th>{kind === 'departures' ? 'Destino' : 'Origen'}</th>
        <th>Tren</th>
        <th>Vía</th>
        <th>Estado</th>
      </tr>
    </thead>
    <tbody>
      {#each filtered as it (it.feed + it.trip_id + it.seq)}
        {@const d = fmtDelay(it.delay_sec)}
        {@const s = srcLabel(it, d)}
        <tr class:cancelled={it.cancelled}>
          <td>
            <strong>{fmtTime(it.scheduled)}</strong>
            {#if it.realtime && it.estimated !== it.scheduled}
              <br /><span class="muted est">→ {fmtTime(it.estimated)}</span>
            {/if}
          </td>
          <td><LineBadge line={it.line_info} fallback={it.line} showNucleo={false} />
            {#if it.semidirect}<span class="badge semi" title={`Omite ${it.skipped_stops} paradas`}>semi</span>{/if}</td>
          <td>{kind === 'departures' ? (it.destination || '—') : (it.origin || '—')}</td>
          <td><a href={trainHref(it)}>{it.train_number || it.trip_id}</a></td>
          <td>{it.platform || '—'}</td>
          <td><span class="badge {s.cls}">{s.text}</span></td>
        </tr>
      {/each}
    </tbody>
  </table>
  <!-- móvil: tarjetas -->
  <div class="cards">
    {#each filtered as it (it.feed + it.trip_id + it.seq)}
      {@const d = fmtDelay(it.delay_sec)}
      {@const s = srcLabel(it, d)}
      <a class="tcard" href={trainHref(it)}>
        <div class="tcard-top">
          <span class="ttime">
            {fmtTime(it.scheduled)}
            {#if it.realtime && it.estimated !== it.scheduled}
              <span class="est">→ {fmtTime(it.estimated)}</span>
            {/if}
          </span>
          <LineBadge line={it.line_info} fallback={it.line} showNucleo={false} link={false} />
          <span class="badge {s.cls}">{s.text}</span>
        </div>
        <div class="tcard-sub">
          {kind === 'departures' ? '→' : '←'} {kind === 'departures' ? (it.destination || '—') : (it.origin || '—')}
          {#if it.semidirect}<span class="badge semi">semi</span>{/if}
          {#if it.platform}<span class="via">vía {it.platform}</span>{/if}
          <span class="muted tnum">{it.train_number || ''}</span>
        </div>
      </a>
    {/each}
  </div>
  <p class="muted legend">obs. = retraso observado por Renfe · est. = estimación RT ·
    programado = sin dato en tiempo real · semi = omite paradas intermedias</p>
{/if}

<style>
  .tabs { display: flex; align-items: center; gap: .45rem; margin-bottom: .6rem; }
  .tabs button, .semi-btn {
    background: var(--card2); color: var(--muted); border: 1px solid var(--border);
    border-radius: 8px; padding: .35rem .9rem; font-size: .85rem; cursor: pointer; }
  .tabs button.on, .semi-btn.on { background: var(--accent); color: var(--accent-fg);
    border-color: transparent; font-weight: 600; }
  .upd { margin-left: auto; font-size: .72rem; text-align: right; }
  .est { font-size: .8rem; }
  .errnote { color: var(--warn); font-size: .82rem; margin: .2rem 0; }
  .filters { display: flex; gap: .5rem; margin-bottom: .6rem; flex-wrap: wrap; }
  .filters select, .filters input { width: auto; flex: 1; min-width: 110px;
                                    font-size: .85rem; padding: .4rem .6rem; }
  .legend { font-size: .75rem; margin: .6rem 0 0; }
  .cards { display: none; }
  @media (max-width: 560px) {
    table.desktop { display: none; }
    .cards { display: flex; flex-direction: column; gap: .5rem; }
    .tcard { display: block; border: 1px solid var(--border); border-radius: 10px;
             padding: .55rem .7rem; color: var(--text); }
    .tcard-top { display: flex; align-items: baseline; gap: .6rem; }
    .ttime { font-size: 1.15rem; font-weight: 700; font-variant-numeric: tabular-nums; }
    .tcard-top .badge { margin-left: auto; }
    .tcard-sub { display: flex; gap: .5rem; align-items: baseline; margin-top: .15rem;
                 font-size: .85rem; color: var(--muted); }
    .via { color: var(--accent); font-weight: 600; }
    .tnum { margin-left: auto; font-size: .75rem; }
  }
</style>
