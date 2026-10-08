<script>
  import { onMount } from 'svelte';
  import StationField from './StationField.svelte';
  import { PUBLIC_API, stopsKey } from '../lib/api';
  import { pushHistory } from '../lib/favs';

  let tab = 'journey';          // journey | station | train
  let from = null, to = null;
  let station = null;
  let trainNum = '';
  let date = '', time = '';
  let semidirect = false;
  let coverage = null;

  onMount(async () => {
    try {
      const r = await fetch(`${PUBLIC_API}/api/v1/meta/coverage`);
      coverage = await r.json();
    } catch {}
    // fecha por defecto = hoy; hora por defecto = ahora
    const n = new Date();
    date = n.toISOString().slice(0, 10);
    time = `${String(n.getHours()).padStart(2, '0')}:${String(n.getMinutes()).padStart(2, '0')}`;
  });

  $: maxDate = coverage
    ? Object.values(coverage.feeds || {})
        .map((f) => f.last_day).sort().reverse()[0]
    : undefined;
  $: minDate = coverage?.today;

  function swap() { [from, to] = [to, from]; }

  function goJourney() {
    if (!from || !to) return;
    pushHistory({ key: stopsKey(from.stops), name: from.name });
    pushHistory({ key: stopsKey(to.stops), name: to.name });
    const p = new URLSearchParams({ from: stopsKey(from.stops), to: stopsKey(to.stops) });
    if (date) p.set('date', date);
    if (time) p.set('time', time);
    if (semidirect) p.set('semidirect', '1');
    location.href = `/trayecto?${p}`;
  }

  function goStation() {
    if (!station) return;
    pushHistory({ key: stopsKey(station.stops), name: station.name });
    location.href = `/estacion/${stopsKey(station.stops)}`;
  }

  function goTrain() {
    const n = trainNum.replace(/\D/g, '');
    if (n.length >= 3) location.href = `/tren-numero/${n}`;
  }
</script>

<div class="planner">
  <div class="tabs" role="tablist" aria-label="Tipo de consulta">
    <button role="tab" aria-selected={tab === 'journey'} class:on={tab === 'journey'}
            on:click={() => (tab = 'journey')}>Trayecto</button>
    <button role="tab" aria-selected={tab === 'station'} class:on={tab === 'station'}
            on:click={() => (tab = 'station')}>Estación</button>
    <button role="tab" aria-selected={tab === 'train'} class:on={tab === 'train'}
            on:click={() => (tab = 'train')}>Nº de tren</button>
  </div>

  {#if tab === 'journey'}
    <div class="form" on:keydown={(e) => e.key === 'Enter' && goJourney()}>
      <div class="od">
        <StationField label="Origen" placeholder="Origen" inputId="from"
                      bind:value={from} autoFocus />
        <button class="swap" on:click={swap} aria-label="Intercambiar origen y destino"
                title="Intercambiar" type="button">⇅</button>
        <StationField label="Destino" placeholder="Destino" inputId="to" bind:value={to} />
      </div>
      <div class="row">
        <div class="dt">
          <label for="jdate">Fecha</label>
          <input id="jdate" type="date" bind:value={date} min={minDate} max={maxDate} />
        </div>
        <div class="dt">
          <label for="jtime">Desde las</label>
          <input id="jtime" type="time" bind:value={time} />
        </div>
        <label class="chk">
          <input type="checkbox" bind:checked={semidirect} />
          Solo semidirectos
        </label>
      </div>
      <button class="go" on:click={goJourney} disabled={!from || !to}>
        Ver trenes {from && to ? `${from.name} → ${to.name}` : ''}
      </button>
    </div>
  {:else if tab === 'station'}
    <div class="form" on:keydown={(e) => e.key === 'Enter' && goStation()}>
      <StationField label="Estación" placeholder="Busca una estación (p. ej. Atocha, Sants)"
                    inputId="station" bind:value={station} autoFocus />
      <button class="go" on:click={goStation} disabled={!station}>
        Ver salidas y llegadas
      </button>
    </div>
  {:else}
    <div class="form" on:keydown={(e) => e.key === 'Enter' && goTrain()}>
      <label class="sr-only" for="tnum">Número de tren</label>
      <input id="tnum" type="text" inputmode="numeric" pattern="[0-9]*"
             placeholder="Nº comercial del tren (p. ej. 06180, 21232)"
             bind:value={trainNum} />
      <button class="go" on:click={goTrain} disabled={trainNum.trim().length < 3}>
        Localizar tren
      </button>
    </div>
  {/if}
</div>

<style>
  .planner { display: flex; flex-direction: column; gap: .8rem; }
  .tabs { display: flex; gap: .4rem; }
  .tabs button {
    flex: 1; padding: .55rem 0; border-radius: 10px; cursor: pointer;
    border: 1px solid var(--border); background: transparent;
    color: var(--muted); font-size: .9rem; font-weight: 500;
  }
  .tabs button.on { background: var(--accent); border-color: transparent;
                    color: var(--accent-fg); font-weight: 700; }
  .form { display: flex; flex-direction: column; gap: .7rem; }
  .od { display: grid; grid-template-columns: 1fr auto 1fr; gap: .5rem; align-items: center; }
  .swap {
    width: 2.2rem; height: 2.2rem; border-radius: 50%; border: 1px solid var(--border);
    background: var(--card); color: var(--muted); cursor: pointer; font-size: 1rem;
  }
  .swap:hover { color: var(--accent); border-color: var(--accent); }
  .row { display: flex; gap: .6rem; align-items: end; flex-wrap: wrap; }
  .dt { display: flex; flex-direction: column; gap: .25rem; flex: 1; min-width: 120px; }
  .dt label { font-size: .75rem; color: var(--muted); }
  .chk { display: flex; align-items: center; gap: .45rem; font-size: .85rem;
         color: var(--muted); white-space: nowrap; padding-bottom: .55rem; }
  .chk input { width: auto; accent-color: var(--accent); }
  .go {
    padding: .7rem; border-radius: 10px; border: 0; cursor: pointer;
    background: var(--accent); color: var(--accent-fg);
    font-size: .95rem; font-weight: 600;
  }
  .go:disabled { opacity: .45; cursor: default; }
  @media (max-width: 560px) {
    .od { grid-template-columns: 1fr; }
    .swap { justify-self: center; transform: rotate(90deg); }
  }
</style>
