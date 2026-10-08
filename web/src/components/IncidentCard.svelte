<script>
  import LineBadge from './LineBadge.svelte';
  import { fmtTime, fmtDate } from '../lib/format';

  export let a;                 // aviso normalizado de /incidencias
  export let compact = false;
  export let context = '';      // 'station' | 'line' | 'journey' | ''

  const REL = {
    station: 'En esta estación',
    origin: 'En la estación de origen',
    destination: 'En la estación de destino',
    line: 'Aviso de la línea completa',
    line_mentions_station: 'Aviso de la línea · menciona esta estación',
    line_other_station: 'Aviso de la línea · se refiere a otra estación',
    station_on_line: 'En una estación de la línea',
    station_in_nucleo: 'En una estación del núcleo',
  };
  const SCOPE = {
    line: 'Alcance oficial: línea', station: 'Alcance oficial: estación',
    route_at_stop: 'Alcance oficial: línea en estación', trip: 'Alcance oficial: tren',
    network: 'Alcance oficial: red', mixed: 'Alcance oficial: varios',
  };
  const STATUS = { active: 'Vigente', upcoming: 'Próximo', expired: 'Caducado',
                   withdrawn: 'Retirado' };

  $: text = a.description || a.header || 'Aviso sin texto';
  $: lines = a.lines || [];
  $: shownLines = compact ? lines.slice(0, 6) : lines;
  $: period = (a.periods || []).map((p) =>
      `${p.start ? fmtDate(p.start) + ' ' + fmtTime(p.start) : 'sin inicio'} → `
      + `${p.end ? fmtDate(p.end) + ' ' + fmtTime(p.end) : 'sin fin indicado'}`);
  $: cls = (a.categories || []).some((c) => c.category === 'interrumpido') ? 'bad'
    : (a.categories || []).some((c) => ['alternativo', 'obras'].includes(c.category)) ? 'warn'
    : 'info';
</script>

<article class="inc {cls}" class:dim={a.relevance === 'line_other_station'}
         aria-label="Aviso oficial">
  <header>
    {#if a.relevance && REL[a.relevance]}<span class="rel">{REL[a.relevance]}</span>{/if}
    {#each a.categories || [] as c}
      <span class="cat" title={`Motivo: ${c.reason}`}>{c.label}</span>
    {/each}
    {#if a.status !== 'active'}<span class="st">{STATUS[a.status] || a.status}</span>{/if}
  </header>
  <p class="txt">{text}</p>
  {#if a.header && a.description}<p class="hd muted">{a.header}</p>{/if}
  <div class="meta">
    {#if shownLines.length}
      <span class="lines">
        {#each shownLines as li}<LineBadge line={li} />{/each}
        {#if lines.length > shownLines.length}<span class="muted">+{lines.length - shownLines.length}</span>{/if}
      </span>
    {/if}
    {#if a.stations?.length}
      <span class="sts">📍 {#each a.stations as s, i}<a href={`/estacion/${s.key}`}>{s.name}</a>{i < a.stations.length - 1 ? ', ' : ''}{/each}</span>
    {/if}
    {#if a.mentioned_stations?.length}
      <span class="sts muted" title="Deducido del texto del aviso; el alcance oficial es la línea">
        menciona: {#each a.mentioned_stations as s, i}<a href={`/estacion/${s.key}`}>{s.name}</a>{i < a.mentioned_stations.length - 1 ? ', ' : ''}{/each}</span>
    {/if}
  </div>
  {#if !compact}
    <details class="prov">
      <summary>Detalles y procedencia</summary>
      <ul>
        <li>{SCOPE[a.scope] || a.scope}</li>
        {#if period.length}<li>Periodo: {period.join(' · ')}</li>{:else}<li>Sin periodo: vigente mientras esté publicado</li>{/if}
        {#if a.effect_label}<li>Efecto (oficial): {a.effect_label}</li>{/if}
        {#if a.cause_label}<li>Causa (oficial): {a.cause_label}</li>{/if}
        {#if a.severity}<li>Severidad (oficial): {a.severity}</li>{/if}
        {#if a.url}<li><a href={a.url} rel="noopener nofollow" target="_blank">Más información (Renfe)</a></li>{/if}
        <li>Fuente: {a.provenance?.source} · {a.provenance?.license} · id {a.id}</li>
        {#if a.unmatched?.routes?.length}<li class="muted">Rutas no presentes en el GTFS publicado: {a.unmatched.routes.length}</li>{/if}
      </ul>
    </details>
  {/if}
</article>

<style>
  .inc { border: 1px solid var(--border); border-left: 4px solid var(--accent);
         border-radius: 10px; padding: .65rem .8rem; margin: .5rem 0; background: var(--card); }
  .inc.warn { border-left-color: var(--warn); }
  .inc.bad { border-left-color: var(--bad); }
  .inc.dim { opacity: .8; }
  header { display: flex; gap: .35rem; flex-wrap: wrap; align-items: center; }
  .rel { font-size: .74rem; font-weight: 650; color: var(--text); }
  .cat, .st { font-size: .7rem; border: 1px solid var(--border); border-radius: 999px;
              padding: .05rem .5rem; color: var(--muted); }
  .st { color: var(--warn); border-color: var(--warn); }
  .txt { margin: .35rem 0; white-space: pre-line; font-size: .92rem; }
  .hd { font-size: .8rem; margin: 0 0 .3rem; }
  .meta { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center; font-size: .8rem; }
  .lines { display: inline-flex; flex-wrap: wrap; gap: .35rem; }
  .prov { font-size: .78rem; margin-top: .4rem; color: var(--muted); }
  .prov summary { cursor: pointer; }
  .prov ul { margin: .3rem 0 0; padding-left: 1.1rem; }
</style>
