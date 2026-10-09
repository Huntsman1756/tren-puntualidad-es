<script>
  // Señales INFERIDAS de retrasos en tiempo real. NUNCA son avisos oficiales de Renfe.
  // compact: lista densa (una fila por línea) para la portada; si no, tarjetas completas.
  import LineBadge from './LineBadge.svelte';
  import { fmtDelay } from '../lib/format';

  export let items = [];       // filas de /api/v1/anomalias
  export let compact = false;
  export let limit = 4;        // filas máximas en modo compacto
  export let empty = '';       // texto si no hay señales (opcional)

  const min = (s) => (s == null ? 0 : Math.round(s / 60));
  const plural = (n, one, many) => (n === 1 ? one : many);
  const scopeOf = (it) => `nucleo=${encodeURIComponent(it.nucleo?.slug || '')}&linea=${encodeURIComponent(it.line?.slug || '')}`;
  const lineUrlOf = (it) => it.line?.url || `/lineas/${it.nucleo?.slug}/${it.line?.slug}`;
</script>

{#if items?.length && compact}
  <p class="caption">Posibles incidencias inferidas de retrasos en tiempo real — no es un aviso oficial.</p>
  <ul class="dense">
    {#each items.slice(0, limit) as it (`${it.nucleo?.slug}/${it.line?.slug}`)}
      <li class="drow">
        <a class="dmain" href={lineUrlOf(it)}>
          <LineBadge line={it.line} fallback={it.line?.code || ''} showNucleo={false} link={false} />
          <span class="dnuc">{it.nucleo?.name}</span>
          <span class="dnums">{it.delayed_15 ?? 0}/{it.monitored ?? 0} con +15 min{it.max_delay_sec != null ? ` · máx +${min(it.max_delay_sec)} min` : ''}</span>
        </a>
        <a class="dtr" href={`/retrasos?${scopeOf(it)}`} aria-label={`Trenes con retraso en la ${it.line?.code} · ${it.nucleo?.name}`}>trenes</a>
      </li>
    {/each}
  </ul>
  {#if items.length > limit}
    <p class="more"><a href="/incidencias">Ver las {items.length} posibles incidencias</a></p>
  {/if}
{:else if items?.length}
  <div class="sigs">
    {#each items as it (`${it.nucleo?.slug}/${it.line?.slug}`)}
      <article class="sig" aria-label={`Posible incidencia en la ${it.line?.code}`}>
        <h3>Posible incidencia en la {it.line?.code} · {it.nucleo?.name}</h3>
        <p class="label">Inferida de retrasos en tiempo real — no es un aviso oficial</p>
        <p class="nums">
          <strong>{it.delayed_15 ?? 0}</strong> de {it.monitored ?? 0} trenes con dato con +15 min
          {#if it.delayed_30}<span class="sep">·</span> {it.delayed_30} con +30 min{/if}
          {#if it.max_delay_sec != null}<span class="sep">·</span> máximo
            <span class={`badge ${fmtDelay(it.max_delay_sec).cls}`}>+{min(it.max_delay_sec)} min</span>{/if}
        </p>
        {#if it.trains?.length}
          <ul class="chips" aria-label="Trenes afectados">
            {#each it.trains.slice(0, 3) as t (t.trip_id)}
              <li>
                <a class="tc" href={`/tren/${t.feed || 'cer'}/${encodeURIComponent(t.trip_id)}`}>
                  {t.train_number || t.trip_id}
                  <span class="tcd">{fmtDelay(t.delay_sec).text}</span>
                </a>
              </li>
            {/each}
          </ul>
        {/if}
        <p class="links">
          <a href={lineUrlOf(it)}>Ver línea {it.line?.code}</a>
          <span class="sep">·</span>
          <a href={`/retrasos?${scopeOf(it)}`}>Trenes con retraso</a>
          {#if it.official_alerts > 0}
            <span class="sep">·</span>
            <a href={`/incidencias?${scopeOf(it)}`} class="off">{it.official_alerts} {plural(it.official_alerts, 'aviso oficial', 'avisos oficiales')}</a>
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
  .drow { display: flex; align-items: center; gap: .5rem; border-top: 1px solid var(--border);
          min-height: 44px; }
  .drow:first-child { border-top: 0; }
  .dmain { flex: 1; min-width: 0; display: grid; grid-template-columns: auto minmax(0, 1fr);
           gap: .1rem .6rem; align-items: center; padding: .3rem .3rem; color: var(--text);
           border-radius: 8px; }
  .dmain:hover .dnuc { color: var(--accent); }
  .dnuc { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .9rem; }
  .dnums { grid-column: 2; font-size: .78rem; color: var(--muted); font-variant-numeric: tabular-nums;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dtr { font-size: .8rem; border: 1px solid var(--border); border-radius: 999px;
         padding: .2rem .6rem; color: var(--muted); white-space: nowrap; }
  .dtr:hover { color: var(--accent); border-color: var(--accent); }
  .more { margin: .4rem 0 0; font-size: .85rem; font-weight: 600; }

  .sigs { display: grid; gap: .6rem; }
  .sig { border: 1px solid var(--border); border-left: 4px solid var(--warn);
         border-radius: 10px; padding: .7rem .85rem; background: var(--card); }
  .sig h3 { margin-bottom: .15rem; }
  .label { margin: 0 0 .35rem; font-size: .78rem; color: var(--warn); font-weight: 600; }
  .nums { margin: 0; font-size: .9rem; font-variant-numeric: tabular-nums; }
  .nums strong { font-size: 1rem; }
  .sep { color: var(--muted); margin: 0 .25rem; }
  .chips { list-style: none; margin: .45rem 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: .35rem; }
  .tc { display: inline-flex; gap: .35rem; align-items: center; min-height: 32px;
        border: 1px solid var(--border); border-radius: 999px; padding: .2rem .65rem;
        font-size: .85rem; color: var(--text); font-variant-numeric: tabular-nums; }
  .tc:hover { border-color: var(--accent); }
  .tcd { font-size: .75rem; color: var(--warn); font-weight: 600; }
  .links { margin: .45rem 0 0; font-size: .85rem; }
  .off { color: var(--warn); font-weight: 600; }
</style>
