<script>
  // Explorador de estadísticas históricas de retraso INFORMADO.
  // Nunca muestra "puntualidad real": los dos tipos de dato (retraso informado
  // por flota y predicción GTFS-RT) se muestran por separado. Las métricas
  // descriptivas solo se publican si superan el gate estadístico (umbrales
  // devueltos por la propia API). Sin datos publicables, se muestran solo recuentos.
  import { onMount } from 'svelte';
  import { PUBLIC_API } from '../lib/api';
  import StationField from './StationField.svelte';
  import {
    STATS_VALIDATION, KIND_LABEL, reasonText, failedChecks, excludedSummary,
    fmtMin, fmtPct, fmtDayShort, statsGet,
  } from '../lib/stats';

  let opts = null;
  let ccaaList = [];
  let provinces = [];
  let statsOff = false;          // 404 o fallo de la API de estadísticas: validación
  // filtros combinables (slugs de la API de opciones)
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
  let lastQs = '';

  onMount(async () => {
    try {
      const [o, g] = await Promise.all([
        statsGet('/api/v1/stats/options'),
        fetch(`${PUBLIC_API}/api/v1/geo/ccaa`).then((r) => r.json()).catch(() => []),
      ]);
      if (o.status >= 400 || !o.data || o.data.detail) { statsOff = true; return; }
      opts = o.data;
      ccaaList = Array.isArray(g) ? g : [];
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
    } catch { err = 'No se pudieron cargar las opciones. Inténtalo de nuevo en unos segundos.'; }
  });

  // líneas del núcleo (slug + código visible) o las de AV/LD
  $: lineas = nucleo
    ? (opts?.nucleos?.find((n) => n.slug === nucleo)?.lines || [])
    : (feed === 'ld' ? (opts?.ld_lines || []) : []);

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
    try {
      const r = await fetch(`${PUBLIC_API}/api/v1/geo/ccaa/${ccaa}`);
      provinces = (await r.json()).provinces || [];
    } catch { provinces = []; }
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

  function syncUrl(qs) {
    const u = new URL(location.href);
    const vista = u.searchParams.get('vista');
    u.search = qs;
    if (vista) u.searchParams.set('vista', vista);
    try { history.replaceState(null, '', u); } catch { /* sin historial */ }
  }

  async function run() {
    loading = true; err = null; res = null; cmp = null;
    const qs = new URLSearchParams(params()).toString();
    lastQs = qs;
    syncUrl(qs);
    // la vista de días escucha el ámbito actual
    window.dispatchEvent(new CustomEvent('stats-scope', { detail: { qs } }));
    try {
      const { status, data } = await statsGet(`/api/v1/stats/delays?${qs}`);
      if (status === 404) { statsOff = true; loading = false; return; }
      if (status >= 400 || !data) throw new Error(data?.detail || `error ${status}`);
      res = data;
      if (nucleo) loadCompare();
    } catch (e) { err = e.message; }
    loading = false;
  }

  async function loadCompare() {
    cmpLoading = true;
    try {
      const { status, data } = await statsGet(
        `/api/v1/stats/compare?${new URLSearchParams({ by: 'line', nucleo })}`);
      cmp = status < 400 && data ? data : null;
    } catch { cmp = null; }
    cmpLoading = false;
  }

  function reset() {
    feed = nucleo = line = ccaa = provincia = '';
    stationG = fromG = toG = null;
    dateFrom = dateTo = hourFrom = hourTo = '';
    res = cmp = null; err = null; lastQs = '';
    provinces = [];
    try { history.replaceState(null, '', location.pathname); } catch { /* sin historial */ }
    window.dispatchEvent(new CustomEvent('stats-scope', { detail: { qs: '' } }));
  }

  // enlace al trayecto con el mismo origen/destino
  $: journeyHref = res?.links?.journey
    || (fromG && toG ? `/trayecto?from=${firstStop(fromG)}&to=${firstStop(toG)}` : null);

  // comparativa: primero unidades con muestra suficiente, por mediana ascendente
  $: cmpUnits = cmp?.enabled
    ? [...(cmp.units || [])].sort((a, b) => {
        const pa = a.gate?.pass ? 0 : 1, pb = b.gate?.pass ? 0 : 1;
        if (pa !== pb) return pa - pb;
        return (a.delay_median_sec ?? 0) - (b.delay_median_sec ?? 0);
      })
    : [];
</script>

<div class="statsx">
  {#if statsOff}
    <section class="card validation" role="status" aria-labelledby="val-h">
      <h2 id="val-h">Estadísticas en validación</h2>
      <p>{STATS_VALIDATION}</p>
      <p class="muted small">Mientras tanto, el retraso en directo está en
        <a href="/retrasos">Retrasos en vivo</a> y las incidencias oficiales en
        <a href="/incidencias">Incidencias</a>.</p>
    </section>
  {:else}
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
            {#each lineas as l}<option value={l.slug}>{l.line}</option>{/each}
          </select>
        </label>
        <label>Red
          <select bind:value={feed} on:change={() => { if (feed === 'ld') nucleo = ''; line = ''; }}>
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
        {Object.entries(res.scope || {}).map(([k, v]) => `${k}=${v}`).join(' · ') || '—'}
        <span class="muted"> · ventana {res.window?.from} → {res.window?.to}
          {#if res.window?.hour} ({res.window.hour[0]}–{res.window.hour[1]}){/if}
          {#if res.days_monitored != null} · {res.days_monitored} días monitorizados{/if}</span>
        {#if res.links?.station}
          · <a href={res.links.station}>ver estación</a>{/if}
        {#if journeyHref}
          · <a href={journeyHref}>ver trayecto</a>{/if}
        {#if lastQs}
          · <a href={`/estadisticas?vista=dias&${lastQs}`}>ver detalle por días</a>{/if}
      </div>

      {#each ['reported', 'prediction'] as k}
        {#if res.kinds?.[k]}
          {@const kk = res.kinds[k]}
          {@const gd = kk.gate_descriptive || { pass: false, checks: {} }}
          {@const gc = kk.gate_comparative}
          <section class="card kindcard" aria-labelledby={`kind-${k}`}>
            <h3 id={`kind-${k}`}>{KIND_LABEL[k].name}
              <small class="muted">· fuente: {KIND_LABEL[k].src}</small></h3>
            <p class="semnote">Esto <strong>no es</strong> puntualidad real ni llegada efectiva.</p>
            <div class="kpis">
              <div class="kpi"><b>{kk.with_data ?? '—'}</b><span>circulaciones con dato</span></div>
              <div class="kpi"><b>{kk.scheduled ?? '—'}</b><span>programadas</span></div>
              <div class="kpi"><b>{fmtPct(kk.coverage_pct)}</b><span>cobertura</span></div>
              <div class="kpi"><b>{kk.representative_days ?? kk.days_observed ?? '—'}</b>
                <span>días representativos</span></div>
            </div>
            {#if kk.with_data_all != null}
              <p class="muted small">Con dato en todos los días, incluidos los excluidos: {kk.with_data_all}.</p>
            {/if}

            <p class="gate" class:ok={gd.pass} class:blocked={!gd.pass}>
              Gate descriptivo: {gd.pass ? 'superado' : 'no superado'}</p>

            {#if gd.pass}
              <div class="metrics">
                <span>mediana <b>{fmtMin(kk.delay_median_sec)}</b></span>
                <span>P90 <b>{fmtMin(kk.delay_p90_sec)}</b></span>
                <span>media <b>{fmtMin(kk.delay_mean_sec)}</b></span>
              </div>
              {#if kk.histogram?.length}
                {@const maxN = Math.max(1, ...kk.histogram.map((b) => b.n))}
                <div class="hist" role="img"
                     aria-label="Distribución de retrasos informados: {KIND_LABEL[k].name}">
                  {#each kk.histogram as b}
                    <div class="hrow">
                      <span class="hlab">{b.label}</span>
                      <span class="hbar"><span style="width:{Math.round(100 * b.n / maxN)}%"></span></span>
                      <span class="hn">{b.n}</span>
                    </div>
                  {/each}
                </div>
              {/if}
            {:else}
              <div class="why">
                <p>No se publican medianas, P90 ni distribución: la muestra no cumple el gate descriptivo.
                  {#if failedChecks(gd).length}
                    Controles no superados: <strong>{failedChecks(gd).join('; ')}</strong>.
                  {/if}
                </p>
                {#if kk.capture_since?.cer || kk.capture_since?.ld}
                  <p class="muted small">Captura disponible desde
                    {[kk.capture_since.cer && `${kk.capture_since.cer} (CER)`,
                      kk.capture_since.ld && `${kk.capture_since.ld} (LD)`].filter(Boolean).join(' · ')}.</p>
                {/if}
              </div>
            {/if}

            {#if gc}
              <p class="muted small">Gate comparativo: {gc.pass ? 'superado' : 'no superado'}
                {#if !gc.pass && failedChecks(gc).length} · {failedChecks(gc).join('; ')}{/if}</p>
            {/if}

            {#if kk.excluded_days?.length}
              <details class="excl">
                <summary>{excludedSummary(kk.excluded_days)}</summary>
                <ul>
                  {#each kk.excluded_days as d}
                    <li>{fmtDayShort(d.day)} · {d.feed}: {(d.reasons || []).map(reasonText).join(', ') || 'sin motivo'}</li>
                  {/each}
                </ul>
              </details>
            {/if}
          </section>
        {/if}
      {/each}
    {/if}

    {#if nucleo}
      <section class="card cmp" aria-labelledby="cmp-h">
        <h3 id="cmp-h">Comparativa por línea del núcleo
          {#if cmpLoading}<small class="muted">· cargando…</small>{/if}</h3>
        {#if cmp && !cmp.enabled}
          <p class="gate blocked">{cmp.reason || 'La comparativa no está disponible para este ámbito.'}</p>
        {:else if cmp}
          <table class="board">
            <caption class="sr-only">Líneas del núcleo con datos, cobertura y mediana cuando la muestra basta</caption>
            <thead><tr><th scope="col">Línea</th><th scope="col">con datos</th>
              <th scope="col">cobertura</th><th scope="col">mediana</th><th scope="col">P90</th>
              <th scope="col">muestra</th></tr></thead>
            <tbody>
              {#each cmpUnits as u}
                <tr class:faded={!u.gate?.pass}>
                  <td>{u.label}</td>
                  <td>{u.with_data ?? '—'}</td>
                  <td>{fmtPct(u.coverage_pct)}</td>
                  <td>{u.gate?.pass && u.delay_median_sec != null ? fmtMin(u.delay_median_sec) : 'muestra insuficiente'}</td>
                  <td>{u.gate?.pass && u.delay_p90_sec != null ? fmtMin(u.delay_p90_sec) : 'muestra insuficiente'}</td>
                  <td>{u.gate?.pass ? 'suficiente' : 'insuficiente'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
          <p class="muted small">Solo se muestran medianas y P90 de las líneas que superan el gate.</p>
        {/if}
      </section>
    {/if}

    <details class="card met">
      <summary>Metodología y criterios estadísticos</summary>
      {#if opts?.gate}
        <ul>
          <li><strong>Qué se mide:</strong> retraso <em>informado</em> por las fuentes
            RT de Renfe (flota del visor o predicciones GTFS-RT). No existe feed de
            llegada efectiva; estas cifras no son «puntualidad real».</li>
          <li><strong>Unidad:</strong> instancia de circulación
            (feed + trip_id + día de servicio). El retraso tomado es el último
            informado dentro del ámbito en esa circulación.</li>
          <li><strong>Cobertura:</strong> circulaciones con dato / programadas,
            solo sobre días cerrados y desde el inicio efectivo de la captura
            tipificada por fuente. Los días excluidos (captura parcial, horario
            retrospectivo, hueco de captura…) no cuentan como pérdidas.</li>
          <li><strong>Gate descriptivo:</strong> n ≥ {opts.gate.descriptive?.min_instances},
            días ≥ {opts.gate.descriptive?.min_days}, cobertura ≥
            {opts.gate.descriptive?.min_coverage_pct}%.</li>
          <li><strong>Gate comparativo:</strong> n ≥ {opts.gate.comparative?.min_instances},
            días ≥ {opts.gate.comparative?.min_days}, cobertura ≥
            {opts.gate.comparative?.min_coverage_pct}% en ≥{opts.gate.comparative?.min_units}
            unidades.</li>
          <li>Los umbrales viajan en la propia respuesta API
            (<code>/api/v1/stats/delays</code>): el juicio es reproducible.</li>
        </ul>
      {:else}
        <p class="muted small">Disponible cuando se publiquen las estadísticas.</p>
      {/if}
    </details>
  {/if}
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
  .validation h2 { font-size: 1.05rem; margin: 0 0 .4rem; }
  .validation p { margin: .3rem 0; }
  .scope-info { font-size: .88rem; overflow-wrap: anywhere; }
  h3 { font-size: 1rem; margin: 0 0 .3rem; }
  h3 small { font-size: .78rem; font-weight: 400; }
  .kindcard { display: flex; flex-direction: column; gap: .3rem; }
  .semnote { font-size: .82rem; color: var(--muted); margin: 0; }
  .kpis { display: flex; gap: .5rem; flex-wrap: wrap; margin: .4rem 0; }
  .kpi { flex: 1; min-width: 7rem; background: var(--card2); border-radius: 10px;
    padding: .55rem .7rem; text-align: center; }
  .kpi b { display: block; font-size: 1.25rem; }
  .kpi span { font-size: .72rem; color: var(--muted); }
  .gate { font-size: .84rem; padding: .4rem .6rem; border-radius: 8px;
    background: var(--nd-bg); margin: .3rem 0; }
  .gate.ok { background: var(--ok-bg); color: var(--ok); }
  .gate.blocked { background: var(--warn-bg); color: var(--warn); }
  .why p { margin: .2rem 0; font-size: .9rem; }
  .metrics { display: flex; gap: 1.2rem; flex-wrap: wrap; margin: .4rem 0 .7rem; font-size: .9rem; }
  .hist { display: flex; flex-direction: column; gap: .25rem; }
  .hrow { display: grid; grid-template-columns: 8.5rem 1fr 2.5rem;
    align-items: center; gap: .5rem; font-size: .78rem; }
  .hlab { color: var(--muted); }
  .hbar { background: var(--card2); border-radius: 4px; height: .9rem; overflow: hidden; }
  .hbar span { display: block; height: 100%; background: var(--accent); }
  .hn { text-align: right; }
  .excl { font-size: .84rem; }
  .excl summary { cursor: pointer; color: var(--muted); }
  .excl ul { margin: .3rem 0 0; padding-left: 1.1rem; }
  .cmp table { font-size: .85rem; }
  .faded { opacity: .6; }
  .small { font-size: .78rem; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap; }
  .met summary { cursor: pointer; font-weight: 600; }
  .met ul { font-size: .85rem; }
  @media (max-width: 560px) {
    .hrow { grid-template-columns: 6.5rem 1fr 2rem; }
    .metrics { gap: .8rem; font-size: .82rem; }
  }
</style>
