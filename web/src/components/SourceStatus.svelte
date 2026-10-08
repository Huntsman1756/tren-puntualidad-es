<script>
  // Estado de la fuente de avisos: distingue fuente sana sin avisos,
  // fuente caída y cobertura desconocida.
  import { ageText } from '../lib/format';
  export let source = null;
  export let count = 0;
  const CLS = { ok: 'ok', stale: 'warn', down: 'bad', unknown: 'nodata', not_available: 'nodata' };
  const LBL = { ok: 'Fuente operativa', stale: 'Fuente sin actualizar', down: 'Fuente caída',
                unknown: 'Cobertura desconocida', not_available: 'Sin fuente de avisos' };
</script>

{#if source}
  <p class="src" role="status">
    <span class="badge {CLS[source.status] || 'nodata'}">{LBL[source.status] || source.status}</span>
    {#if source.status === 'ok' && count === 0}
      <span>Sin avisos oficiales en este ámbito.</span>
    {:else if source.status !== 'ok'}
      <span>{source.message}</span>
    {/if}
    {#if source.last_ok}<span class="muted">· última descarga {ageText(source.last_ok)}</span>{/if}
  </p>
{/if}

<style>
  .src { display: flex; gap: .45rem; align-items: center; flex-wrap: wrap;
         font-size: .8rem; margin: .3rem 0; }
</style>
