<script>
  // Ranking de mayores retrasos. Escritorio: columnas FIJAS (la insignia de línea
  // nunca desplaza las demás columnas). Móvil: tarjeta de dos líneas sin truncar destino.
  import LineBadge from './LineBadge.svelte';
  import { fmtDelay } from '../lib/format';

  export let items = [];   // filas de /api/v1/delays/ranking
</script>

{#if items?.length}
  <ol class="rank">
    <li class="rk-head" aria-hidden="true">
      <span>Línea</span><span>Tren</span><span>Destino</span><span class="r">Retraso</span>
    </li>
    {#each items as r (r.feed + r.trip_id)}
      {@const d = fmtDelay(r.delay)}
      <li class="rk-li">
        <a class="rk" href={`/tren/${r.feed}/${encodeURIComponent(r.trip_id)}`}>
          <span class="c-line"><LineBadge line={r.line_info} fallback={r.line || '—'} link={false} /></span>
          <span class="c-route">
            <span class="c-train"><span class="lbl">Tren</span><span class="num">{r.train_number || r.trip_id}</span></span>
            <span class="arrow" aria-hidden="true"> → </span>
            <span class="c-dest">{r.destination || '—'}</span>
          </span>
          <span class="c-delay"><span class={`badge ${d.cls}`}>{d.text}{r.delay_source === 'observed' ? ' · obs.' : ''}</span></span>
        </a>
      </li>
    {/each}
  </ol>
{/if}

<style>
  .rank { list-style: none; margin: 0; padding: 0; }
  .rk-head, .rk {
    display: grid;
    grid-template-columns: 7.5rem 4.5rem minmax(0, 1fr) auto;
    column-gap: .75rem;
    align-items: center;
  }
  .rk-head { padding: .3rem .4rem; font-size: .72rem; text-transform: uppercase;
             letter-spacing: .04em; color: var(--muted); border-bottom: 1px solid var(--border); }
  .rk-head .r { justify-self: end; }
  .rk {
    min-height: 44px; padding: .5rem .4rem; color: var(--text);
    border-top: 1px solid var(--border); border-radius: 8px;
  }
  .rk-head + .rk-li .rk { border-top: 0; }
  .rk:hover { background: var(--card2); }
  .rk:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  .c-line { min-width: 0; overflow: hidden; }
  .c-line :global(.lb) { flex-wrap: nowrap; width: 100%; min-width: 0; }
  .c-line :global(.code) { flex: 0 0 auto; }
  .c-line :global(.nuc) { flex: 1 1 auto; min-width: 0; overflow: hidden; white-space: nowrap;
                          text-overflow: ellipsis; overflow-wrap: normal; }
  .c-route { display: contents; }
  .c-train { font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden;
             text-overflow: ellipsis; min-width: 0; }
  .c-dest { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
            color: var(--muted); }
  .lbl, .arrow { display: none; }
  .c-delay { justify-self: end; }

  @media (max-width: 560px) {
    .rk-head { display: none; }
    .rk {
      grid-template-columns: minmax(0, 1fr) auto;
      row-gap: .35rem; padding: .6rem .75rem; margin-bottom: .5rem;
      border: 1px solid var(--border);
    }
    .rk-head + .rk-li .rk { border-top: 1px solid var(--border); }
    .c-line { grid-column: 1; grid-row: 1; }
    .c-delay { grid-column: 2; grid-row: 1; }
    .c-route { display: block; grid-column: 1 / -1; grid-row: 2; font-size: .92rem;
               line-height: 1.35; }
    .c-train, .c-dest { display: inline; white-space: normal; overflow: visible;
                        text-overflow: clip; overflow-wrap: anywhere; }
    .c-train { color: var(--text); font-weight: 600; }
    .c-dest { color: var(--text); }
    .lbl, .arrow { display: inline; }
    .lbl { margin-right: .35em; }
    .arrow { color: var(--muted); }
    .c-line :global(.lb) { flex-wrap: wrap; }
    .c-line :global(.nuc) { white-space: normal; font-size: .78rem; }
  }
</style>
