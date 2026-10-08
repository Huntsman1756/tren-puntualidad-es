<script>
  import { onMount } from 'svelte';
  import { favJourneys, saveJourney, removeJourney, isFavJourney } from '../lib/favs';

  export let fromKey = '';
  export let fromName = '';
  export let toKey = '';
  export let toName = '';
  export let semidirect = false;

  let saved = null;         // FavJourney | null
  let editing = false;
  let label = '';
  let daysSel = [];         // [0..6] dom..sab
  let window_ = '';
  let returnPair = false;

  onMount(() => {
    const j = favJourneys().find((x) => x.id === `${fromKey}->${toKey}`);
    if (j) {
      saved = j; label = j.label || ''; daysSel = j.days || [];
      window_ = j.window || ''; returnPair = !!j.return_pair;
    }
  });

  function toggleDay(d) {
    daysSel = daysSel.includes(d) ? daysSel.filter((x) => x !== d) : [...daysSel, d].sort();
  }

  function save() {
    saved = saveJourney({
      from: { key: fromKey, name: fromName },
      to: { key: toKey, name: toName },
      label: label.trim() || undefined,
      days: daysSel.length ? daysSel : undefined,
      window: window_.trim() || undefined,
      semidirect: semidirect || undefined,
      return_pair: returnPair || undefined,
    });
    editing = false;
  }

  function del() { removeJourney(saved.id); saved = null; editing = false; }

  const DAYNAMES = ['D', 'L', 'M', 'X', 'J', 'V', 'S'];
  const DOW = [1, 2, 3, 4, 5, 6, 0]; // L M X J V S D
</script>

{#if !saved}
  <button class="fav" on:click={() => (editing = true)}>☆ Guardar trayecto</button>
{:else}
  <button class="fav on" on:click={() => (editing = !editing)}>★ Guardado</button>
{/if}

{#if editing}
  <div class="edit">
    <label>Nombre (opcional)
      <input type="text" bind:value={label} placeholder={`${fromName} → ${toName}`} />
    </label>
    <div class="days">
      <span class="muted">Días:</span>
      {#each DOW as d}
        <button type="button" class="d" class:on={daysSel.includes(d)}
                aria-pressed={daysSel.includes(d)} on:click={() => toggleDay(d)}>
          {DAYNAMES[d]}
        </button>
      {/each}
    </div>
    <label>Franja habitual <input type="text" bind:value={window_}
      placeholder="07:30-08:30" size="10" /></label>
    <label class="chk"><input type="checkbox" bind:checked={returnPair} />
      Mostrar también la vuelta</label>
    <div class="ops">
      <button class="save" on:click={save}>Guardar</button>
      {#if saved}<button class="del" on:click={del}>Eliminar</button>{/if}
      <button class="tool" on:click={() => (editing = false)}>Cerrar</button>
    </div>
  </div>
{/if}

<style>
  .fav { background: none; border: 1px solid var(--border); color: var(--muted);
         border-radius: 8px; padding: .3rem .8rem; cursor: pointer; font-size: .85rem; }
  .fav.on { color: var(--warn); border-color: var(--warn); }
  .edit { border: 1px solid var(--border); border-radius: 10px; padding: .8rem;
          margin-top: .6rem; display: flex; flex-direction: column; gap: .6rem;
          background: var(--card2); }
  .edit label { display: flex; flex-direction: column; gap: .25rem; font-size: .82rem;
                color: var(--muted); }
  .days { display: flex; gap: .3rem; align-items: center; }
  .days .d { width: 1.9rem; height: 1.9rem; border-radius: 50%; border: 1px solid var(--border);
             background: transparent; color: var(--muted); cursor: pointer; font-size: .78rem; }
  .days .d.on { background: var(--accent); color: var(--accent-fg); border-color: transparent;
                font-weight: 700; }
  .chk { flex-direction: row !important; align-items: center; gap: .45rem; }
  .chk input { width: auto; accent-color: var(--accent); }
  .ops { display: flex; gap: .5rem; }
  .save { background: var(--accent); color: var(--accent-fg); border: 0; border-radius: 8px;
          padding: .45rem 1rem; font-weight: 600; cursor: pointer; }
  .del { background: none; border: 1px solid var(--bad); color: var(--bad);
         border-radius: 8px; padding: .45rem 1rem; cursor: pointer; }
</style>
