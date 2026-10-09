<script>
  // Vista por días del ámbito seleccionado en el explorador (últimos 30 días).
  // Solo se publican mediana y >5 min en los días que superan el gate diario;
  // los días no representativos se muestran atenuados con sus motivos en texto.
  import { onMount, onDestroy } from 'svelte';
  import {
    STATS_VALIDATION, reasonText, fmtMin, fmtNum, fmtPct, fmtDayShort, statsGet, SCOPE_KEYS,
  } from '../lib/stats';

  let state = 'idle';      // idle | empty | loading | ok | off | error
  let data = null;
  let err = null;
  let seq = 0;

  onMount(() => {
    const u = new URL(location.href);
    load(scopeQs(u.searchParams));
    window.addEventListener('stats-scope', onScope);
  });
  onDestroy(() => {
    if (typeof window !== 'undefined') window.removeEventListener('stats-scope', onScope);
  });

  function onScope(e) { load(e.detail?.qs || ''); }

  function scopeQs(sp) {
    const p = new URLSearchParams();
    for (const k of SCOPE_KEYS) { const v = sp.get(k); if (v) p.set(k, v); }
    for (const k of ['date_from', 'date_to', 'hour_from', 'hour_to']) {
      const v = sp.get(k); if (v) p.set(k, v);
    }
    return p.toString();
  }

  async function load(qs) {
    const my = ++seq;
    if (!qs) { state = 'empty'; data = null; return; }
    state = 'loading'; err = null;
    try {
      const { status, data: d } = await statsGet(`/api/v1/stats/daily?${qs}`);
      if (my !== seq) return;
      if (status === 404) { state = 'off'; data = null; return; }
      if (status >= 400 || !d) throw new Error(d?.detail || `error ${status}`);
      data = d;
      state = 'ok';
    } catch (e) {
      if (my !== seq) return;
      err = e.message; state = 'error';
    }
  }

  // últimos 30 días distintos, más reciente primero
  $: rows = (() => {
    const days = data?.days || [];
    const dates = [...new Set(days.map((d) => d.day))].sort();
    const keep = new Set(dates.slice(-30));
    return days
      .filter((d) => keep.has(d.day))
      .sort((a, b) => (a.day === b.day ? (a.feed < b.feed ? -1 : 1) : (a.day < b.day ? 1 : -1)));
  })();
  $: repDays = new Set(rows.filter((r) => r.representative).map((r) => r.day)).size;
  $: allDays = new Set(rows.map((r) => r.day)).size;

  const FEED = { cer: 'Cercanías', ld: 'LD / AV' };
  const bar = (p) => Math.max(0, Math.min(100, Number(p) || 0));

  // mediana y >5 min solo con gate diario superado
  function kindCell(kr) {
    if (!kr) return null;
    return {
      with: kr.with_data ?? '—',
      cov: fmtPct(kr.coverage_pct),
      covBar: bar(kr.coverage_pct),
      med: kr.gate_day ? fmtMin(kr.delay_median_sec) : 'muestra insuficiente',
      over: kr.gate_day && kr.over_5min_pct != null ? `${fmtNum(kr.over_5min_pct)}% >5 min` : '',
    };
  }
</script>

<section class="card daily" aria-labelledby="daily-h">
  <h2 id="daily-h">Detalle por días</h2>
  <p class="muted small">Ámbito del explorador de arriba. Cada día cerrado se evalúa por separado:
    la mediana solo se muestra en los días que superan el gate diario.</p>

  {#if state === 'empty'}
    <p class="muted">Elige un núcleo, línea, estación o trayecto en el explorador para ver el detalle por días.</p>
  {:else if state === 'off'}
    <p class="gate blocked" role="status">{STATS_VALIDATION}</p>
  {:else if state === 'loading'}
    <p class="muted" aria-live="polite">Cargando días…</p>
  {:else if state === 'error'}
    <p class="err" role="alert">No se pudo cargar el detalle por días: {err}</p>
  {:else if state === 'ok'}
    {#if rows.length === 0}
      <p class="muted">No hay días monitorizados para este ámbito.</p>
    {:else}
      <p class="small"><strong>{repDays}</strong> de {allDays} días representativos en el periodo mostrado.</p>
      <div class="tablewrap">
        <table class="board dtable">
          <caption>Últimos 30 días del ámbito, del más reciente al más antiguo</caption>
          <thead>
            <tr>
              <th scope="col">Día</th>
              <th scope="col">Prog.</th>
              <th scope="col">Informado: con dato · cobertura</th>
              <th scope="col">Informado: mediana</th>
              <th scope="col">Predicción: con dato · cobertura</th>
              <th scope="col">Predicción: mediana</th>
              <th scope="col">Estado</th>
            </tr>
          </thead>
          <tbody>
            {#each rows as d (d.day + d.feed)}
              {@const rep = d.representative}
              {@const rc = kindCell(d.kinds?.reported)}
              {@const pc = kindCell(d.kinds?.prediction)}
              <tr class:nr={!rep}>
                <td data-label="Día">
                  <time datetime={d.day}>{fmtDayShort(d.day)}</time>
                  <small class="muted">{FEED[d.feed] || d.feed}</small>
                </td>
                <td data-label="Prog.">{d.scheduled ?? '—'}</td>
                <td data-label="Informado" class="cov">
                  {#if rc}<span>{rc.with} · {rc.cov}</span>
                    <span class="bar" aria-hidden="true"><span style="width:{rc.covBar}%"></span></span>
                  {:else}—{/if}
                </td>
                <td data-label="Informado mediana">
                  {#if rc}<span>{rc.med}</span>{#if rc.over}<br /><small class="muted">{rc.over}</small>{/if}{:else}—{/if}
                </td>
                <td data-label="Predicción" class="cov">
                  {#if pc}<span>{pc.with} · {pc.cov}</span>
                    <span class="bar" aria-hidden="true"><span style="width:{pc.covBar}%"></span></span>
                  {:else}—{/if}
                </td>
                <td data-label="Predicción mediana">
                  {#if pc}<span>{pc.med}</span>{#if pc.over}<br /><small class="muted">{pc.over}</small>{/if}{:else}—{/if}
                </td>
                <td data-label="Estado">
                  {#if rep}
                    <span class="badge ok">representativo</span>
                  {:else}
                    <span class="badge nodata">excluido</span>
                    <span class="reasons">{(d.reasons || []).map(reasonText).join(', ') || 'sin motivo registrado'}</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {/if}
</section>

<style>
  .daily h2 { font-size: 1rem; margin: 0 0 .3rem; }
  .small { font-size: .82rem; }
  .gate { font-size: .86rem; padding: .5rem .7rem; border-radius: 8px; margin: .3rem 0; }
  .gate.blocked { background: var(--warn-bg); color: var(--warn); }
  .err { color: var(--bad); }
  .tablewrap { overflow-x: auto; }
  table.dtable { font-size: .86rem; min-width: 36rem; }
  table.dtable caption { text-align: left; color: var(--muted); font-size: .8rem; padding-bottom: .3rem; }
  table.dtable th { white-space: normal; font-size: .72rem; }
  table.dtable td { vertical-align: top; }
  table.dtable tr.nr td { opacity: .6; }
  .cov .bar { display: block; height: .45rem; background: var(--card2); border-radius: 4px;
    overflow: hidden; margin-top: .2rem; }
  .cov .bar span { display: block; height: 100%; background: var(--ok); }
  .reasons { display: block; font-size: .78rem; color: var(--muted); margin-top: .15rem; }
  .badge.nodata { background: var(--nd-bg); color: var(--muted); }
  @media (max-width: 560px) {
    /* tarjetas apiladas: cada celda con su etiqueta visible */
    table.dtable { min-width: 0; font-size: .88rem; }
    table.dtable caption { margin-bottom: .4rem; }
    table.dtable thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
    table.dtable, table.dtable tbody, table.dtable tr, table.dtable td { display: block; width: 100%; }
    table.dtable tr { border: 1px solid var(--border); border-radius: 10px; padding: .4rem .6rem;
      margin-bottom: .55rem; background: var(--card2); }
    table.dtable td { border: 0; padding: .2rem 0; display: flex; gap: .6rem;
      justify-content: space-between; align-items: baseline; text-align: right; }
    table.dtable td::before { content: attr(data-label); color: var(--muted); font-size: .74rem;
      text-align: left; flex: 0 0 auto; }
    table.dtable td.cov .bar { flex: 1; }
    table.dtable td.cov { flex-wrap: wrap; }
    .reasons { text-align: left; }
  }
</style>
