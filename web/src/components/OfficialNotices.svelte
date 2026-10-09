<script>
  // Avisos OFICIALES de Renfe publicados en sus canales (p. ej. WhatsApp), texto original.
  import { fmtHM, fmtStamp } from '../lib/format';

  export let items = [];       // hilos de /api/v1/avisos-oficiales (o incidencias.official_notices)
  export let compact = false;  // una línea por hilo (portada)
  export let limit = 4;
  export let empty = '';

  const KIND = {
    averia_infraestructura: 'Avería en la infraestructura',
    averia_tren: 'Avería de tren',
    incidencia_tercero: 'Incidencia ajena a Renfe',
    meteorologia: 'Meteorología',
    obras: 'Obras',
    huelga: 'Huelga',
    servicio_alterado: 'Servicio alterado',
    reajuste_servicio: 'Reajuste de servicio',
    otra: 'Incidencia',
  };
  const ST = {
    activa: 'Activa', en_recuperacion: 'En recuperación', normalizada: 'Normalizada',
    sin_actualizar: 'Sin actualizar',
  };
  const ST_CLS = {
    activa: 'warn', en_recuperacion: 'info', normalizada: 'ok', sin_actualizar: 'nodata',
  };
  const CH = { whatsapp: 'WhatsApp', manual: 'manual' };

  const kindLbl = (k) => KIND[k] || (k ? String(k).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : 'Aviso');
  const statusLbl = (t) => (t.status === 'sin_actualizar'
    ? `Sin actualizar desde ${fmtHM(t.updated_at)}` : ST[t.status] || t.status || '');
  const statusNote = (t) => (t.status === 'en_recuperacion'
    ? 'incidencia subsanada; frecuencias recuperándose' : '');
  const nucLabel = (t) => (typeof t.nucleo === 'string' ? t.nucleo
    : [t.nucleo?.brand, t.nucleo?.name].filter(Boolean).join(' '));
  const chanOf = (t) => CH[t.channel] || t.channel || '';
  function headerOf(t) {
    const nl = nucLabel(t);
    const base = t.attribution
      || [nl ? (/^renfe/i.test(nl) ? nl : `Renfe ${nl}`) : 'Renfe', chanOf(t)].filter(Boolean).join(' · ');
    const manual = t.source === 'manual' && !/manual/i.test(base) ? ' · pegado manualmente' : '';
    return `Aviso oficial · ${base}${manual}`;
  }
  const msgs = (t) => [...(t.messages || [])].sort((a, b) => (b.posted_at || 0) - (a.posted_at || 0));
</script>

{#if items?.length && compact}
  <ul class="ncompact">
    {#each items.slice(0, limit) as t (t.thread_id)}
      <li>
        <a href={`/incidencias#aviso-${encodeURIComponent(t.thread_id)}`}>
          <span class="codes">{(t.lines || []).join(' · ') || 'Varias líneas'}</span>
          <span class="kind">{kindLbl(t.kind)}</span>
          <span class={`pill ${ST_CLS[t.status] || 'nodata'}`}>{statusLbl(t)}</span>
          <span class="muted tm">{fmtHM(t.updated_at)}</span>
        </a>
      </li>
    {/each}
  </ul>
  {#if items.length > limit}
    <p class="more"><a href="/incidencias">Ver los {items.length} avisos oficiales</a></p>
  {/if}
{:else if items?.length}
  <div class="notices">
    {#each items as t (t.thread_id)}
      <article id={`aviso-${t.thread_id}`} class="notice" aria-label="Aviso oficial">
        <header class="nh">
          <p class="hd">{headerOf(t)}</p>
          <span class={`pill ${ST_CLS[t.status] || 'nodata'}`}>{statusLbl(t)}</span>
        </header>
        <p class="meta">
          {#each t.lines || [] as code}<span class="chip">{code}</span>{/each}
          <span class="kind">{kindLbl(t.kind)}</span>
          <span class="muted">abierto {fmtStamp(t.opened_at)}</span>
        </p>
        {#if statusNote(t)}<p class="note">{statusNote(t)}</p>{/if}
        {#if t.stations?.length}
          <p class="sts">📍 {#each t.stations as s, i}{#if s.key}<a href={`/estacion/${s.key}`}>{s.name}</a>{:else}{s.name}{/if}{i < t.stations.length - 1 ? ', ' : ''}{/each}</p>
        {/if}
        <ol class="tl" aria-label="Mensajes del aviso">
          {#each msgs(t) as m (m.id)}
            <li>
              <p class="mh"><time>{fmtStamp(m.posted_at)}</time>
                {#if m.is_update}<span class="upd">📢 actualización</span>{/if}</p>
              <p class="mtxt">{m.text}</p>
            </li>
          {/each}
        </ol>
      </article>
    {/each}
  </div>
{:else if empty}
  <p class="muted">{empty}</p>
{/if}

<style>
  .ncompact { list-style: none; margin: 0; padding: 0; }
  .ncompact li { border-top: 1px solid var(--border); }
  .ncompact li:first-child { border-top: 0; }
  .ncompact a { display: flex; flex-wrap: wrap; gap: .3rem .6rem; align-items: center; min-height: 40px;
                padding: .3rem .2rem; color: var(--text); font-size: .88rem; }
  .ncompact a:hover .kind { color: var(--accent); }
  .codes { font-weight: 700; font-variant-numeric: tabular-nums; }
  .kind { color: var(--muted); }
  .tm { font-size: .8rem; }
  .more { margin: .4rem 0 0; font-size: .85rem; font-weight: 600; }

  .pill { font-size: .72rem; font-weight: 650; border-radius: 999px; padding: .08rem .55rem;
          border: 1px solid currentColor; white-space: nowrap; line-height: 1.5; }
  .pill.warn { color: var(--warn); background: var(--warn-bg); }
  .pill.info { color: var(--accent); }
  .pill.ok { color: var(--ok); }
  .pill.nodata { color: var(--muted); }

  .notices { display: grid; gap: .7rem; }
  .notice { border: 1px solid var(--border); border-left: 4px solid var(--warn); border-radius: 10px;
            padding: .7rem .85rem; background: var(--card); scroll-margin-top: 4rem; }
  .nh { display: flex; flex-wrap: wrap; gap: .3rem .6rem; align-items: center; justify-content: space-between; }
  .hd { margin: 0; font-size: .8rem; color: var(--muted); font-weight: 600; }
  .meta { display: flex; flex-wrap: wrap; gap: .35rem .6rem; align-items: center; margin: .35rem 0; font-size: .82rem; }
  .chip { font-weight: 700; border: 1px solid var(--border); border-radius: 6px; padding: .05rem .4rem; }
  .note { margin: 0 0 .3rem; font-size: .85rem; color: var(--muted); }
  .sts { margin: .25rem 0; font-size: .85rem; }
  .tl { list-style: none; margin: .5rem 0 0; padding: 0 0 0 .8rem; border-left: 3px solid var(--border); }
  .tl li { padding: .2rem 0 .45rem; }
  .mh { margin: 0; font-size: .78rem; color: var(--muted); display: flex; gap: .5rem; flex-wrap: wrap; }
  .upd { color: var(--warn); font-weight: 650; }
  .mtxt { margin: .15rem 0 0; white-space: pre-line; font-size: .92rem; }
</style>
