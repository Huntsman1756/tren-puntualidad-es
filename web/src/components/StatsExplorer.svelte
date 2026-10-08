<script>
  // Explorador de estadísticas históricas de retraso INFORMADO.
  // Nunca muestra "puntualidad real": los dos tipos de dato son
  // retraso informado por flota y predicción GTFS-RT. Las métricas
  // descriptivas solo se publican si superan el gate estadístico
  // (umbrales devueltos por la propia API y reproducibles).
  import { onMount } from 'svelte';
  import { PUBLIC_API } from '../lib/api';
  import StationField from './StationField.svelte';

  const KIND_LABEL = {
    reported: { name: 'Retraso informado', src: 'flota (visor Renfe)' },
    prediction: { name: 'Predicción', src: 'GTFS-RT trip_updates' },
  };

  let opts = null;
  let ccaaList = [];
  let provinces = [];
  // filtros combinables
  let feed = '';
  let nucleo = '';
  let line = '';
  let ccaa = '';
  let provincia = '';
  let stationG = null;
  let fromG = null, toG = null;
  let dateFrom = '', dateTo = '', hourFrom = '', hourTo = '';
  let res = null, cmp = null;
  let loading = false, cmpLoading = false, err = null;
  let tab = 'reported';

  onMount(async () => {
    try {
      const [o, g] = await Promise.all([
        fetch(`${PUBLIC_API}/api/v1/stats/options`).then((r) => r.json()),
        fetch(`${PUBLIC_API}/api/v1/geo/ccaa`).then((r) => r.json()),
      ]);
      opts = o; ccaaList = g;
      const u = new URL(location.href);
      feed = u.searchParams.get('feed') || '';
      nucleo = u.searchParams.get('nucleo') || '';
      line = u.searchParams.get('line') || '';
      ccaa = u.searchParams.get('ccaa') || '';
      provincia = u.searchParams.get('provincia') || '';
      dateFrom = u.searchParams.get('date_from') || '';
      dateTo = u.searchParams.get('date_to') || '';
      hourFrom = u.searchParams.get('hour_from') || '';
      hourTo = u.searchParams.get('hour_to') || '';
      if (ccaa) await loadProv();
      if (scopeOk) await run();
    } catch { err = 'No se pudieron cargar las opciones.'; }
  });

  $: lineas = nucleo
    ? (opts?.nucleos.find((n) => n.slug === nucleo)?.lines || []).map((l) => l.line)
    : (feed === 'ld' ? (opts?.ld_lines || []) : []);
  $: hasNuc = !!nucleo;

  function firstStop(g) {
    if (!g?.stops?.length) return null;
    const cer = g.stops.find((s) => s.feed === 'cer');
    const s = cer || g.stops[0];
    return `${s.feed}:${s.stop_id}`;
  }

  $: scopeOk = !!(feed || nucleo || line || ccaa || provincia
    || stationG || (fromG && toG));

  async function loadProv() {
    provinces = []; provincia = '';
    if (!ccaa) return;
    const r = await fetch(`${PUBLIC_API}/api/v1/geo/ccaa/${ccaa}`);
    provinces = (await r.json()).provinces || [];
  }

  function params() {
    const p = {};
    if (feed) p.feed = feed;
    if (nucleo) p.nucleo = nucleo;
    if (line) p.line = line;
    if (ccaa) p.ccaa = ccaa;
    if (provincia) p.provincia = provincia;
    const st = firstStop(stationG);
    if (st) p.station = st;
    if (fromG && toG) { p.from = firstStop(fromG); p.to = firstStop(toG); }
    if (dateFrom) p.date_from = dateFrom;
    if (dateTo) p.date_to = dateTo;
    if (hourFrom && hourTo) { p.hour_from = hourFrom; p.hour_to = hourTo; }
    return p;
  }

  async function run() {
    loading = true; err = null; res = null; cmp = null;
    const p = params();
    const u = new URL(location.href);
    u.search = new URLSearchParams(p).toString();
    history.replaceState(null, '', u);
    try {
      const r = await fetch(`${PUBLIC_API}/api/v1/stats/delays?${new URLSearchParams(p)}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `error ${r.status}`);
      res = d;
      if (nucleo || feed === 'ld') loadCompare();
    } catch (e) { err = e.message; }
    loading = false;
  }

  async function loadCompare() {
    cmpLoading = true;
    try {
      const p = { by: 'line', ...params() };
      delete p.station; delete p.from; delete p.to;
      const r = await fetch(`${PUBLIC_API}/api/v1/stats/compare?${new URLSearchParams(p)}`);
      cmp = await r.json();
    } catch { /* comparativa es opcional */ }
    cmpLoading = false;
  }

  function reset() {
    feed = nucleo = line = ccaa = provincia = '';
    stationG = fromG = toG = null;
    dateFrom = dateTo = hourFrom = hourTo = '';
    res = cmp = null; err = null;
    provinces = [];
    history.replaceState(null, '', location.pathname);
  }

  const fmtMin = (s) => s == null ? '—'
    : `${s < 0 ? '−' : '+'}${Math.abs(Math.round(s / 60))} min`;
  const fmtDay = (d) => new Date(d + 'T00:00:00').toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' });
  const maxN = (h) => Math.max(1, ...(h || []).map((b) => b.n));

  function gateText(g) {
    if (!g) return '';
    if (g.pass) return 'muestra suficiente';
    const f = Object.entries(g.checks).filter(([, v]) => !v.pass)
      .map(([k]) => ({ min_instances: 'n circulaciones', min_days: 'días observados',
                      min_coverage_pct: 'cobertura' }[k] || k));
    return `muestra insuficiente (${f.join(', ')})`;
  }
</script>

<div class="statsx">
  {#if err}<p class="card err" role="alert">{err}</p>{/if}

  <form class="card filtros" on:submit|preventDefault={run} aria-label="Filtros de estadísticas">
    <div class="frow">
      <label>Núcleo
        <select bind:value={nucleo} on:change={() => { line = ''; feed = ''; }}>
          <option value="">— todos —</option>
          {#each opts?.nucleos || [] as n}
            <option value={n.slug}>{n.name} ({n.stations} est.)</option>
          {/each}
        </select>
      </label>
      <label>Línea
        <select bind:value={line} disabled={!lineas.length}>
          <option value="">— todas —</option>
          {#each lineas as l}<option value={l}>{l}</option>{/each}
        </select>
      </label>
      <label>Red
        <select bind:value={feed}>
          <option value="">— todas —</option>
          <option value="cer">Cercanías/Rodalies</option>
          <option value="ld">AV · Larga/Media distancia</option>
        </select>
      </label>
    </div>
    <div class="frow">
      <label>Comunidad autónoma
        <select bind:value={ccaa} on:change={loadProv}>
          <option value="">— todas —</option>
          {#each ccaaList as c}<option value={c.slug}>{c.name}</option>{/each}
        </select>
      </label>
      <label>Provincia
        <select bind:value={provincia} disabled={!provinces.length}>
          <option value="">— todas —</option>
          {#each provinces as p}<option value={p.slug}>{p.name}</option>{/each}
        </select>
      </label>
    </div>
    <div class="frow">
      <div class="fld"><StationField label="Estación" inputId="stats-station"
        placeholder="p. ej. Atocha" bind:value={stationG} on:select={() => {}} /></div>
    </div>
    <div class="frow">
      <div class="fld"><StationField label="Trayecto: origen" inputId="stats-from"
        placeholder="Origen" bind:value={fromG} on:select={() => {}} /></div>
      <div class="fld"><StationField label="Trayecto: destino" inputId="stats-to"
        placeholder="Destino" bind:value={toG} on:select={() => {}} /></div>
    </div>
    <div class="frow">
      <label>Desde <input type="date" bind:value={dateFrom} /></label>
      <label>Hasta <input type="date" bind:value={dateTo} /></label>
      <label>De <input type="time" bind:value={hourFrom} /></label>
      <label>a <input type="time" bind:value={hourTo} /></label>
    </div>
    <div class="frow btns">
      <button type="submit" class="go" disabled={loading || !scopeOk}>
        {loading ? 'Calculando…' : 'Ver estadísticas'}</button>
      <button type="button" class="ghost" on:click={reset}>Limpiar</button>
    </div>
  </form>

  {#if res}
    <div class="card scope-info">
      <strong>Ámbito:</strong>
      {Object.entries(res.scope).map(([k, v]) => `${k}=${v}`).join(' · ') || '—'}
      <span class="muted"> · ventana {res.window.from} → {res.window.to}
        {#if res.window.hour} ({res.window.hour[0]}–{res.window.hour[1]}){/if}
        · {res.days_monitored} días monitorizados</span>
      {#if res.links?.station}
        · <a href={res.links.station}>ver estación</a>{/if}
      {#if res.links?.journey}
        · <a href={res.links.journey}>ver trayecto</a>{/if}
    </div>

    <div class="tabs" role="tablist" aria-label="Tipo de dato">
      {#each Object.keys(KIND_LABEL) as k}
        <button role="tab" aria-selected={tab === k} class="tab"
          class:active={tab === k} on:click={() => (tab = k)}>
          {KIND_LABEL[k].name}
          <small>({KIND_LABEL[k].src})</small>
        </button>
      {/each}
    </div>

    {#each ['reported', 'prediction'] as k}
      {#if tab === k && res.kinds[k]}
        {@const kk = res.kinds[k]}
        <div class="card kindcard">
          <p class="semnote">{KIND_LABEL[k].name} — fuente: <code>{kk.source}</code>.
            Esto <strong>no es</strong> puntualidad real ni llegada efectiva.</p>
          <div class="kpis">
            <div class="kpi"><b>{kk.with_data}</b><span>con datos</span></div>
            <div class="kpi"><b>{kk.scheduled}</b><span>programadas</span></div>
            <div class="kpi"><b>{kk.coverage_pct == null ? '—' : kk.coverage_pct + '%'}</b>
              <span>cobertura (días cerrados)</span></div>
            <div class="kpi"><b>{kk.days_observed}</b><span>días con datos</span></div>
          </div>
          <p class="gate" class:ok={kk.gate_descriptive.pass}
             class:blocked={!kk.gate_descriptive.pass}>
            Gate descriptivo: {gateText(kk.gate_descriptive)}</p>
          {#if kk.gate_descriptive.pass}
            <div class="metrics">
              <span>mediana <b>{fmtMin(kk.delay_median_sec)}</b></span>
              <span>P90 <b>{fmtMin(kk.delay_p90_sec)}</b></span>
              <span>media <b>{fmtMin(kk.delay_mean_sec)}</b></span>
            </div>
            <div class="hist" role="img"
                 aria-label="Distribución de retrasos informados">
              {#each kk.histogram as b}
                <div class="hrow">
                  <span class="hlab">{b.label}</span>
                  <span class="hbar"><span style="width:{Math.round(100 * b.n / maxN(kk.histogram))}%"></span></span>
                  <span class="hn">{b.n}</span>
                </div>
              {/each}
            </div>
          {:else}
            <p class="muted">La distribución no se publica hasta superar el gate
              descriptivo. Con datos desde {kk.capture_since?.cer || '—'}.</p>
          {/if}
        </div>
      {/if}
    {/each}

    {#if res.by_day?.length}
      <div class="card">
        <h3>Cobertura por día</h3>
        <div class="days">
          {#each res.by_day as d}
            {@const wd = Object.values(d.with_data || {}).reduce((a, b) => a + b, 0)}
            <div class="dcol" title="{d.day} · {d.feed} · {wd}/{d.scheduled} con datos">
              <span class="dbar"><span class="fill"
                style="height:{Math.round(100 * Math.min(1, wd / d.scheduled))}%"></span></span>
              <span class="dlab">{fmtDay(d.day)}</span>
            </div>
          {/each}
        </div>
        <p class="muted small">Altura = circulaciones con dato RT / programadas
          ese día. Un día sin barra no estaba monitorizado.</p>
      </div>
    {/if}
  {/if}

  {#if hasNuc || feed === 'ld'}
    <div class="card cmp">
      <h3>Comparativa por línea {cmp ? '' : (cmpLoading ? '…' : '')}</h3>
      {#if cmp && !cmp.enabled}
        <p class="gate blocked">{cmp.reason}</p>
      {/if}
      {#if cmp}
        <table class="board">
          <thead><tr><th>Línea</th><th>con datos</th><th>programadas</th>
            <th>cobertura</th><th>mediana</th><th>P90</th><th>gate</th></tr></thead>
          <tbody>
            {#each cmp.units as u}
              <tr class:faded={!u.gate.pass}>
                <td>{u.label}</td>
                <td>{u.with_data}</td><td>{u.scheduled}</td>
                <td>{u.coverage_pct == null ? '—' : u.coverage_pct + '%'}</td>
                <td>{cmp.enabled && u.gate.pass ? fmtMin(u.delay_median_sec) : '—'}</td>
                <td>{cmp.enabled && u.gate.pass ? fmtMin(u.delay_p90_sec) : '—'}</td>
                <td>{u.gate.pass ? '✓' : '—'}</td>
              </tr>
            {/each}
          </tbody>
        </table>
        {#if !cmp.enabled}
          <p class="muted small">Las medianas/P90 por línea permanecen ocultas
            mientras la cobertura no permita una comparación defendible.</p>
        {/if}
      {/if}
    </div>
  {/if}

  <details class="card met">
    <summary>Metodología y criterios estadísticos</summary>
    {#if opts}
      <ul>
        <li><strong>Qué se mide:</strong> retraso <em>informado</em> por las fuentes
          RT de Renfe (flota del visor o predicciones GTFS-RT). No existe feed de
          llegada efectiva; estas cifras no son «puntualidad real».</li>
        <li><strong>Unidad:</strong> instancia de circulación
          (feed + trip_id + día de servicio). El retraso tomado es el último
          informado dentro del ámbito en esa circulación.</li>
        <li><strong>Denominador:</strong> circulaciones programadas capturadas en
          el snapshot diario (inmutable una vez cerrado el día: una recarga GTFS
          no altera días pasados).</li>
        <li><strong>Cobertura:</strong> circulaciones con dato / programadas,
          solo sobre días cerrados y desde el inicio efectivo de la captura
          tipificada por fuente — los días anteriores no cuentan como pérdidas.</li>
        <li><strong>Gate descriptivo:</strong> n ≥ {opts.gate.descriptive.min_instances},
          días ≥ {opts.gate.descriptive.min_days}, cobertura ≥
          {opts.gate.descriptive.min_coverage_pct}%.</li>
        <li><strong>Gate comparativo:</strong> n ≥ {opts.gate.comparative.min_instances},
          días ≥ {opts.gate.comparative.min_days}, cobertura ≥
          {opts.gate.comparative.min_coverage_pct}% en ≥{opts.gate.comparative.min_units}
          unidades.</li>
        <li>Los umbrales viajan en la propia respuesta API
          (<code>/api/v1/stats/delays</code>) — el juicio es reproducible.</li>
      </ul>
    {/if}
  </details>
</div>

<style>
  .statsx { display: flex; flex-direction: column; gap: .8rem; }
  .filtros .frow { display: flex; gap: .6rem; flex-wrap: wrap; margin-bottom: .55rem; }
  .filtros label { display: flex; flex-direction: column; gap: .2rem;
    font-size: .82rem; color: var(--muted); min-width: 9rem; flex: 1; }
  .filtros select, .filtros input { padding: .45rem .5rem; border-radius: 8px;
    border: 1px solid var(--border); background: var(--card2); color: var(--text);
    font-size: .9rem; width: 100%; }
  .fld { flex: 1; min-width: 12rem; }
  .btns { gap: .5rem; }
  button.go { background: var(--accent); color: var(--accent-fg); border: 0;
    border-radius: 8px; padding: .55rem 1.1rem; font-weight: 600; cursor: pointer; }
  button.go:disabled { opacity: .5; cursor: default; }
  button.ghost { background: none; border: 1px solid var(--border);
    color: var(--muted); border-radius: 8px; padding: .55rem 1rem; cursor: pointer; }
  .err { color: var(--bad); }
  .scope-info { font-size: .88rem; }
  .tabs { display: flex; gap: .4rem; }
  .tab { flex: 1; padding: .55rem; border: 1px solid var(--border);
    background: var(--card); color: var(--muted); border-radius: 10px;
    cursor: pointer; font-size: .9rem; }
  .tab small { display: block; font-size: .72rem; }
  .tab.active { color: var(--accent); border-color: var(--accent); font-weight: 600; }
  .semnote { font-size: .82rem; color: var(--muted); margin-top: 0; }
  .kpis { display: flex; gap: .5rem; flex-wrap: wrap; margin: .5rem 0; }
  .kpi { flex: 1; min-width: 7rem; background: var(--card2); border-radius: 10px;
    padding: .55rem .7rem; text-align: center; }
  .kpi b { display: block; font-size: 1.25rem; }
  .kpi span { font-size: .72rem; color: var(--muted); }
  .gate { font-size: .84rem; padding: .4rem .6rem; border-radius: 8px;
    background: var(--nd-bg); }
  .gate.ok { background: var(--ok-bg); color: var(--ok); }
  .gate.blocked { background: var(--warn-bg); color: var(--warn); }
  .metrics { display: flex; gap: 1.2rem; margin: .4rem 0 .7rem; font-size: .9rem; }
  .hist { display: flex; flex-direction: column; gap: .25rem; }
  .hrow { display: grid; grid-template-columns: 8.5rem 1fr 2.5rem;
    align-items: center; gap: .5rem; font-size: .78rem; }
  .hlab { color: var(--muted); }
  .hbar { background: var(--card2); border-radius: 4px; height: .9rem;
    overflow: hidden; }
  .hbar span { display: block; height: 100%; background: var(--accent); }
  .hn { text-align: right; }
  .days { display: flex; gap: 4px; align-items: flex-end; min-height: 5.5rem;
    overflow-x: auto; }
  .dcol { display: flex; flex-direction: column; align-items: center;
    min-width: 1.6rem; }
  .dbar { width: 1.1rem; height: 4.5rem; background: var(--card2);
    border-radius: 4px; display: flex; align-items: flex-end; overflow: hidden; }
  .dbar .fill { width: 100%; background: var(--ok); }
  .dlab { font-size: .62rem; color: var(--muted); margin-top: .2rem;
    transform: rotate(-45deg); transform-origin: top left; white-space: nowrap; }
  .cmp table { font-size: .85rem; }
  .faded { opacity: .55; }
  .small { font-size: .78rem; }
  .met summary { cursor: pointer; font-weight: 600; }
  .met ul { font-size: .85rem; }
  @media (max-width: 560px) {
    .hrow { grid-template-columns: 6.5rem 1fr 2rem; }
    .metrics { gap: .8rem; font-size: .82rem; }
  }
</style>
