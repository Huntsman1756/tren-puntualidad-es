<script>
  // Episodios INFERIDOS de retrasos en tiempo real. NUNCA son avisos oficiales de Renfe.
  // compact: una fila por episodio abierto (portada); si no, tarjetas completas
  // (incluye resueltos que el padre haya dejado en `items`).
  import LineBadge from './LineBadge.svelte';
  import Sparkline from './Sparkline.svelte';
  import { fmtDelay, fmtHM, fmtDurSec } from '../lib/format';
  import {
    RANK, STATUS_LBL, STATUS_CLS, minOf, countOf, isOpen, curOf, peakOf, lineUrlOf, scopeOf,
  } from '../lib/signals';

  export let items = [];       // episodios de /api/v1/anomalias
  export let compact = false;
  export let limit = 4;        // filas máximas en modo compacto
  export let empty = '';       // texto si no hay episodios (opcional)

  // Porcentajes con coma decimal española: 40,9 %
  const NF = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 1 });
  const pct = (v) => (v == null ? '?' : NF.format(v));

  $: sorted = [...(items || [])].sort((a, b) => (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9));
  $: shown = compact ? sorted.filter(isOpen) : sorted;
  const keyOf = (it) => it.id ?? `${it.nucleo?.slug}/${it.line?.slug}/${it.opened_at}`;
</script>

{#if shown.length && compact}
  <p class="caption">Inferidas de retrasos en tiempo real — no es un aviso oficial.</p>
  <ul class="dense">
    {#each shown.slice(0, limit) as it (keyOf(it))}
      {@const c = curOf(it)}
      <li class="drow">
        <span class={`pill dpill ${STATUS_CLS[it.status] || 'neutral'}`}>{STATUS_LBL[it.status] || it.status}</span>
        <span class="dbadge"><LineBadge line={it.line} fallback={it.line?.code || ''} showNucleo={false} link={false} /></span>
        <a class="dnuc" href={lineUrlOf(it)}>{it.nucleo?.name}</a>
        <span class="dnums">{c.delayed15}/{c.monitored ?? '?'} ({pct(c.share)} %) con +15 min{c.maxDelay != null ? ` · máx +${minOf(c.maxDelay)} min` : ''}</span>
        <span class="dspark"><Sparkline ev={it.evolution} w={64} h={18} /></span>
        <a class="dtr" href={`/retrasos?${scopeOf(it)}`} aria-label={`Trenes con retraso en la ${it.line?.code} · ${it.nucleo?.name}`}>trenes</a>
      </li>
    {/each}
  </ul>
  {#if shown.length > limit}
    <p class="more"><a href="/incidencias">Ver las {shown.length} posibles incidencias</a></p>
  {/if}
{:else if shown.length}
  <div class="sigs">
    {#each shown as it (keyOf(it))}
      {@const c = curOf(it)}
      {@const pk = peakOf(it)}
      <article class={`sig ${it.status || ''}`} aria-label={`Posible incidencia en la ${it.line?.code}, ${STATUS_LBL[it.status] || ''}`}>
        <header class="sh">
          <h3>Posible incidencia en la {it.line?.code} · {it.nucleo?.name}</h3>
          <span class={`pill ${STATUS_CLS[it.status] || 'neutral'}`}>{STATUS_LBL[it.status] || it.status}</span>
        </header>
        <p class="when">
          {#if it.status === 'resuelta'}
            <span>episodio {fmtHM(it.opened_at)} – {fmtHM(it.resolved_at)}</span>
          {:else}
            <span>desde {fmtHM(it.opened_at)}</span>
            {#if it.confirmed_at}<span class="sep">·</span><span>confirmada {fmtHM(it.confirmed_at)}</span>{/if}
          {/if}
          {#if it.duration_sec != null}<span class="sep">·</span><span>duración {fmtDurSec(it.duration_sec)}</span>{/if}
        </p>
        {#if it.status === 'resuelta'}
          <p class="res">sin retrasos generalizados desde {fmtHM(it.resolved_at)}</p>
        {/if}
        {#if it.rt_gap_since}
          <p class="gap">sin datos en tiempo real desde {fmtHM(it.rt_gap_since)} — estado congelado</p>
        {/if}
        <p class="nums">
          <strong>{c.delayed15}</strong> de {c.monitored ?? '?'} trenes con dato RT llevan +15 min
          ({pct(c.share)} %)
          <span class="sep">·</span> {c.scheduled ?? '?'} programados ahora
          {#if c.maxDelay != null}<span class="sep">·</span> máx <span class={`badge ${fmtDelay(c.maxDelay).cls}`}>+{minOf(c.maxDelay)} min</span>{/if}
        </p>
        {#if pk && pk.share > (c.share ?? 0)}
          <p class="peak">Pico{pk.ts ? ` a las ${fmtHM(pk.ts)}` : ''}: {pk.delayed15} de {pk.monitored} trenes con dato ({pct(pk.share)} %){pk.maxDelay != null ? ` · máx +${minOf(pk.maxDelay)} min` : ''}</p>
        {/if}
        <div class="evo"><Sparkline ev={it.evolution} /><span class="muted small">% de trenes con +15 min</span></div>
        {#if c.trains.length}
          <ul class="chips" aria-label="Trenes afectados">
            {#each c.trains.slice(0, 3) as t (t.trip_id)}
              <li>
                <a class="tc" href={`/tren/${t.feed || 'cer'}/${encodeURIComponent(t.trip_id)}`}>
                  {t.train_number || t.trip_id}
                  <span class="tcd">{fmtDelay(t.delay_sec).text}</span>
                </a>
              </li>
            {/each}
          </ul>
        {/if}
        <p class="label">Inferida de retrasos en tiempo real — no es un aviso oficial</p>
        <p class="links">
          <a href={lineUrlOf(it)}>Ver línea {it.line?.code}</a>
          <span class="sep">·</span>
          <a href={`/retrasos?${scopeOf(it)}`}>Trenes con retraso</a>
          {#if countOf(it.official_notices) + countOf(it.official_alerts) > 0}
            <span class="sep">·</span>
            <a href={`/incidencias?${scopeOf(it)}`} class="off">{countOf(it.official_notices) + countOf(it.official_alerts)} avisos oficiales en la línea</a>
          {/if}
        </p>
      </article>
    {/each}
  </div>
{:else if empty}
  <p class="muted">{empty}</p>
{/if}

<style>
  .caption { margin: 0 0 .4rem; font-size: .8rem; color: var(--warn); font-weight: 600; }
  .dense { list-style: none; margin: 0; padding: 0; }
  /* Fila compacta: una línea en escritorio; en móvil, dos líneas (ver media query). */
  /* Pistas fijas en escritorio: todas las filas comparten columnas (números alineados). */
  .drow { display: grid; grid-template-columns: 8.8rem 3.6rem minmax(0, 1fr) 15rem 64px auto;
          align-items: center; gap: .3rem .6rem; border-top: 1px solid var(--border);
          min-height: 44px; padding: .3rem .2rem; }
  .dpill, .dbadge { justify-self: start; min-width: 0; }
  .dbadge { display: inline-flex; }
  .drow:first-child { border-top: 0; }
  .dnuc { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
          font-size: .9rem; color: var(--text); }
  .dnuc:hover { color: var(--accent); }
  /* Los números nunca se truncan: sin text-overflow, con ancho mínimo. */
  .dnums { min-width: 14rem; font-size: .78rem; color: var(--muted); font-variant-numeric: tabular-nums;
           white-space: nowrap; }
  .dspark { display: inline-flex; flex: none; }
  .dtr { font-size: .8rem; border: 1px solid var(--border); border-radius: 999px;
         padding: .2rem .6rem; color: var(--muted); white-space: nowrap; }
  .dtr:hover { color: var(--accent); border-color: var(--accent); }
  .more { margin: .4rem 0 0; font-size: .85rem; font-weight: 600; }

  @media (max-width: 560px) {
    .drow { grid-template-columns: auto auto minmax(0, 1fr) auto; }
    .dnums { grid-column: 1 / span 3; grid-row: 2; min-width: 0; white-space: normal; }
    .dspark { grid-column: 4; grid-row: 2; justify-self: end; }
  }

  .pill { font-size: .72rem; font-weight: 650; border-radius: 999px; padding: .08rem .55rem;
          border: 1px solid currentColor; white-space: nowrap; line-height: 1.5; }
  .pill.neutral { color: var(--muted); background: var(--card2); border-color: var(--border); }
  .pill.warn { color: var(--warn); background: var(--warn-bg); }
  .pill.ok { color: var(--ok); }
  .pill.muted { opacity: .75; }

  .sigs { display: grid; gap: .6rem; }
  .sig { border: 1px solid var(--border); border-left: 4px solid var(--warn);
         border-radius: 10px; padding: .7rem .85rem; background: var(--card); }
  .sig.observacion { border-left-color: var(--muted); }
  .sig.resuelta { border-left-color: var(--ok); opacity: .85; }
  .sh { display: flex; flex-wrap: wrap; gap: .3rem .6rem; align-items: center; justify-content: space-between; }
  .sh h3 { margin: 0 0 .15rem; }
  .when { margin: .1rem 0 .3rem; font-size: .82rem; color: var(--muted); }
  .res { margin: 0 0 .3rem; font-size: .88rem; color: var(--ok); font-weight: 600; }
  .gap { margin: 0 0 .3rem; font-size: .85rem; color: var(--bad); font-weight: 600; }
  .label { margin: .35rem 0 0; font-size: .78rem; color: var(--warn); font-weight: 600; }
  .nums { margin: 0; font-size: .9rem; font-variant-numeric: tabular-nums; }
  .nums strong { font-size: 1rem; }
  .peak { margin: .25rem 0 0; font-size: .82rem; color: var(--muted); }
  .evo { display: flex; align-items: center; gap: .5rem; margin-top: .35rem; }
  .sep { color: var(--muted); margin: 0 .25rem; }
  .small { font-size: .75rem; }
  .chips { list-style: none; margin: .45rem 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: .35rem; }
  .tc { display: inline-flex; gap: .35rem; align-items: center; min-height: 32px;
        border: 1px solid var(--border); border-radius: 999px; padding: .2rem .65rem;
        font-size: .85rem; color: var(--text); font-variant-numeric: tabular-nums; }
  .tc:hover { border-color: var(--accent); }
  .tcd { font-size: .75rem; color: var(--warn); font-weight: 600; }
  .links { margin: .45rem 0 0; font-size: .85rem; }
  .off { color: var(--warn); font-weight: 600; }
</style>
