<script>
  // Estado de la fuente de avisos: distingue fuente sana sin avisos,
  // fuente caída, cobertura desconocida y feed oficial sin actualizar.
  import { ageText, fmtStamp } from '../lib/format';
  export let source = null;
  export let count = 0;
  export let compact = false;  // portada: solo una línea si el feed está sin actualizar
  // Dos marcas de tiempo separadas: última descarga correcta vs. último cambio de contenido.
  $: lastOk = source?.last_fetch_ok ?? source?.last_ok ?? null;
  $: changed = source?.content_changed_at ?? source?.content_ts ?? null;
  const CLS = { ok: 'ok', stale: 'warn', down: 'bad', unknown: 'nodata', not_available: 'nodata' };
  const LBL = { ok: 'Fuente operativa', stale: 'Fuente sin actualizar', down: 'Fuente caída',
                unknown: 'Cobertura desconocida', not_available: 'Sin fuente de avisos' };
</script>

{#if source && compact}
  {#if source.content_stale}
    <p class="stale-line" role="status">⚠ El feed oficial de avisos de Renfe no se actualiza desde
      {fmtStamp(changed)} · <a href="/incidencias">ver detalles</a></p>
  {/if}
{:else if source}
  <p class="src" role="status">
    <span class="badge {CLS[source.status] || 'nodata'}">{LBL[source.status] || source.status}</span>
    {#if source.content_stale}
      <span class="muted">Ver aviso abajo</span>
    {:else if source.status === 'ok' && count === 0}
      <span>Sin avisos oficiales en este ámbito.</span>
    {:else if source.status !== 'ok'}
      <span>{source.message}</span>
    {/if}
  </p>
  <p class="stamps muted">
    <span>Última descarga correcta: {ageText(lastOk)}</span>
    {#if source.last_fetch_error}<span class="sep">·</span><span class="err" title={source.last_fetch_error}>último intento fallido</span>{/if}
    <span class="sep">·</span>
    <span>Último cambio de contenido: {changed ? fmtStamp(changed) : 'sin datos'}{#if source.content_hash} <span class="hash" title="El cambio se detecta comparando la huella (hash) del contenido">· comprobado por huella</span>{/if}</span>
  </p>
  {#if source.content_stale}
    <div class="stale" role="status">
      <p class="stale-msg"><strong>Feed oficial sin actualizar.</strong>
        {source.message || 'Renfe no ha actualizado su feed oficial de avisos. Los avisos publicados en WhatsApp o X no tienen datos abiertos y no aparecen aquí.'}</p>
      {#if source.official_channels?.length}
        <p class="chan">Canales oficiales:
          {#each source.official_channels as c, i}
            <a href={c.url} rel="noopener" target="_blank">{c.name}</a>{i < source.official_channels.length - 1 ? ' · ' : ''}
          {/each}
        </p>
      {/if}
      <p class="muted stamp">Último cambio de contenido: {fmtStamp(changed)} (Europe/Madrid)</p>
    </div>
  {/if}
{/if}

<style>
  .src { display: flex; gap: .45rem; align-items: center; flex-wrap: wrap;
         font-size: .85rem; margin: .3rem 0; }
  .stale { border: 1px solid var(--warn); border-left-width: 4px; background: var(--warn-bg);
           border-radius: 10px; padding: .6rem .8rem; margin: .5rem 0; font-size: .88rem; }
  .stale p { margin: .2rem 0; }
  .stale-msg { color: var(--text); }
  .chan a { font-weight: 600; }
  .stamp { font-size: .8rem; }
  .stale-line { margin: .3rem 0; font-size: .88rem; color: var(--warn); font-weight: 600; }
  .stamps { display: flex; flex-wrap: wrap; gap: .1rem .3rem; align-items: center; font-size: .8rem; margin: .1rem 0 .3rem; }
  .sep { margin: 0 .1rem; }
  .err { color: var(--bad); font-weight: 600; }
  .hash { font-style: italic; }
</style>
