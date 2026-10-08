<script>
  import { onMount } from 'svelte';
  import { PUBLIC_API } from '../lib/api';
  import { fmtTime, fmtDelay, fmtDuration } from '../lib/format';
  import {
    favJourneys, removeJourney, moveJourney, exportJSON, importJSON,
  } from '../lib/favs';

  let journeys = [];
  let next = {};          // id -> [journey items]
  let err = {};
  let importMsg = '';

  onMount(async () => {
    journeys = favJourneys();
    await Promise.all(journeys.map(async (j) => {
      const p = new URLSearchParams({ from: j.from.key, to: j.to.key, hours: '8' });
      if (j.semidirect) p.set('semidirect', '1');
      try {
        const r = await fetch(`${PUBLIC_API}/api/v1/journeys?${p}`);
        next = { ...next, [j.id]: (await r.json()).slice(0, 2) };
      } catch { err = { ...err, [j.id]: true }; }
    }));
  });

  function link(j, reverse = false) {
    const [a, b] = reverse ? [j.to, j.from] : [j.from, j.to];
    const p = new URLSearchParams({ from: a.key, to: b.key });
    if (j.semidirect) p.set('semidirect', '1');
    return `/trayecto?${p}`;
  }

  function del(id) { removeJourney(id); journeys = favJourneys(); }
  function move(id, d) { moveJourney(id, d); journeys = favJourneys(); }

  function doExport() {
    const blob = new Blob([exportJSON()], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'trenes-favoritos.json';
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function doImport(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const r = importJSON(await f.text());
      journeys = favJourneys();
      importMsg = `Importados: ${r.stations} estaciones, ${r.journeys} trayectos`;
    } catch {
      importMsg = 'Archivo no válido';
    }
    e.target.value = '';
  }

  const DAYS = 'DLMXJVS';
  function daysText(j) {
    if (!j.days?.length) return '';
    return j.days.map((d) => DAYS[(d + 6) % 7] ?? '').join('');
  }
</script>

{#if journeys.length}
  <div class="favs">
    <div class="fav-head">
      <h2>Tus trayectos</h2>
      <div class="fav-tools">
        <button class="tool" on:click={doExport} title="Descargar favoritos en JSON">Exportar</button>
        <label class="tool">Importar
          <input type="file" accept="application/json" class="sr-only" on:change={doImport} />
        </label>
      </div>
    </div>
    {#if importMsg}<p class="muted imp">{importMsg}</p>{/if}
    <div class="grid">
      {#each journeys as j (j.id)}
        <div class="jcard">
          <a class="jmain" href={link(j)}>
            <span class="jname">{j.label || `${j.from.name} → ${j.to.name}`}</span>
            {#if j.label}<span class="jsub">{j.from.name} → {j.to.name}</span>{/if}
            <span class="jmeta">
              {#if daysText(j)}<span class="chip">{daysText(j)}</span>{/if}
              {#if j.window}<span class="chip">{j.window}</span>{/if}
              {#if j.semidirect}<span class="chip">semidirectos</span>{/if}
            </span>
          </a>
          <div class="jnext">
            {#if err[j.id]}
              <span class="badge nodata">sin conexión</span>
            {:else if next[j.id]?.length}
              {#each next[j.id] as it}
                {@const d = fmtDelay(it.delay_sec)}
                <a class="nt" href={link(j)}>
                  <strong>{fmtTime(it.dep_scheduled)}</strong>
                  <span class="muted">{it.line || it.train_number}</span>
                  <span class="muted">{fmtDuration(it.dep_scheduled, it.arr_scheduled)}</span>
                  <span class="badge {it.realtime ? d.cls : 'nodata'}">
                    {it.realtime ? d.text : 'prog.'}</span>
                </a>
              {/each}
            {:else if next[j.id]}
              <span class="muted">Sin trenes directos en 8 h</span>
            {:else}
              <span class="muted">…</span>
            {/if}
          </div>
          <div class="jops">
            {#if j.return_pair}<a class="tool" href={link(j, true)}>Vuelta</a>{/if}
            <button class="tool" on:click={() => move(j.id, -1)} aria-label="Subir">↑</button>
            <button class="tool" on:click={() => move(j.id, 1)} aria-label="Bajar">↓</button>
            <button class="tool danger" on:click={() => del(j.id)} aria-label="Eliminar">✕</button>
          </div>
        </div>
      {/each}
    </div>
  </div>
{:else}
  <div class="favs-empty">
    <div class="fav-head">
      <h2>Tus trayectos</h2>
      <div class="fav-tools">
        <label class="tool">Importar
          <input type="file" accept="application/json" class="sr-only" on:change={doImport} />
        </label>
      </div>
    </div>
    <p class="muted">Aún no hay trayectos guardados. Busca un trayecto y pulsa
      «Guardar trayecto» para tenerlo aquí cada vez que entres.</p>
  </div>
{/if}

<style>
  .fav-head { display: flex; align-items: baseline; justify-content: space-between; }
  h2 { margin: 0 0 .5rem; font-size: 1rem; }
  .fav-tools { display: flex; gap: .5rem; }
  .tool { font-size: .78rem; color: var(--muted); border: 1px solid var(--border);
          background: transparent; border-radius: 7px; padding: .25rem .6rem;
          cursor: pointer; }
  .tool:hover { color: var(--text); border-color: var(--muted); }
  .tool.danger:hover { color: var(--bad); border-color: var(--bad); }
  .grid { display: grid; gap: .7rem; }
  .jcard { border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .jmain { display: block; padding: .7rem .8rem .4rem; color: var(--text); }
  .jname { font-weight: 650; font-size: .95rem; display: block; }
  .jsub { font-size: .75rem; color: var(--muted); }
  .jmeta { display: flex; gap: .35rem; margin-top: .3rem; flex-wrap: wrap; }
  .chip { font-size: .68rem; color: var(--muted); border: 1px solid var(--border);
          border-radius: 999px; padding: .05rem .45rem; }
  .jnext { padding: 0 .8rem; }
  .nt { display: flex; gap: .7rem; align-items: baseline; padding: .3rem 0;
        border-top: 1px solid var(--border); color: var(--text); font-size: .88rem; }
  .nt:first-child { border-top: 0; }
  .jops { display: flex; gap: .4rem; padding: .4rem .8rem .6rem; justify-content: flex-end; }
  .imp { font-size: .8rem; margin: .2rem 0 .5rem; }
  .favs-empty p { margin: 0; }
</style>
