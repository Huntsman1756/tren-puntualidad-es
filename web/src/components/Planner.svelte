<script>
  import { onMount } from 'svelte';
  import StationField from './StationField.svelte';
  import { PUBLIC_API, stopsKey, coverageMax } from '../lib/api';
  import { pushHistory } from '../lib/favs';
  import { addDays, madridToday, relDay, weekendStart } from '../lib/dates';

  export let initialDate = '';     // de la URL (?date=)
  export let initialTime = '';     // de la URL (?time=)
  export let initialTab = 'journey';

  let tab = initialTab;            // journey | station | train
  let from = null, to = null;
  let station = null;
  let trainNum = '';
  let today = madridToday();
  let date = initialDate || today;
  // Hora: vacía = "ahora" si es hoy, "todo el día" si es otra fecha.
  // Solo se conserva una hora si el usuario la eligió expresamente.
  let time = initialTime || '';
  let semidirect = false;
  let coverage = null;
  let picking = !!initialDate && ![today, addDays(today, 1), weekendStart(today)].includes(initialDate);

  onMount(async () => {
    today = madridToday();
    if (!initialDate) date = today;
    try {
      const r = await fetch(`${PUBLIC_API}/api/v1/meta/coverage`);
      if (r.ok) coverage = await r.json();
    } catch { /* sin cobertura: el servidor valida igualmente */ }
  });

  $: feeds = tab === 'journey'
    ? [...new Set([...(from?.stops || []), ...(to?.stops || [])].map((s) => s.feed))]
    : tab === 'station' ? [...new Set((station?.stops || []).map((s) => s.feed))] : [];
  $: maxDate = coverageMax(coverage, feeds);
  $: outOfRange = !!(maxDate && date > maxDate) || date < today;
  $: tomorrow = addDays(today, 1);
  $: weekend = weekendStart(today);
  $: shortcut = date === today ? 'today' : date === tomorrow ? 'tomorrow'
    : date === weekend ? 'weekend' : 'pick';
  $: timeHint = time ? `desde las ${time}` : date === today ? 'desde ahora' : 'todo el día';

  function setDay(d) {
    date = d;
    picking = false;
    // cambiar de día no arrastra la hora actual
    time = '';
  }

  function swap() { [from, to] = [to, from]; }

  function dateParams(p) {
    if (date && date !== today) p.set('date', date);
    if (time) { p.set('time', time); if (date === today) p.set('date', date); }
    return p;
  }

  function goJourney() {
    if (!from || !to) return;
    pushHistory({ key: stopsKey(from.stops), name: from.name });
    pushHistory({ key: stopsKey(to.stops), name: to.name });
    const p = dateParams(new URLSearchParams({ from: stopsKey(from.stops), to: stopsKey(to.stops) }));
    if (semidirect) p.set('semidirect', '1');
    location.href = `/trayecto?${p}`;
  }

  function goStation() {
    if (!station) return;
    pushHistory({ key: stopsKey(station.stops), name: station.name });
    const p = dateParams(new URLSearchParams());
    const qs = p.toString();
    location.href = `/estacion/${stopsKey(station.stops)}${qs ? `?${qs}` : ''}`;
  }

  function goTrain() {
    const n = trainNum.replace(/\D/g, '');
    if (n.length < 3) return;
    const p = dateParams(new URLSearchParams());
    p.delete('time');
    const qs = p.toString();
    location.href = `/tren-numero/${n}${qs ? `?${qs}` : ''}`;
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
    <div class="od" on:keydown={(e) => e.key === 'Enter' && goJourney()} role="group">
      <StationField label="Origen" placeholder="Origen" inputId="from"
                    bind:value={from} autoFocus />
      <button class="swap" on:click={swap} aria-label="Intercambiar origen y destino"
              title="Intercambiar" type="button">⇅</button>
      <StationField label="Destino" placeholder="Destino" inputId="to" bind:value={to} />
    </div>
  {:else if tab === 'station'}
    <div on:keydown={(e) => e.key === 'Enter' && goStation()} role="group">
      <StationField label="Estación" placeholder="Busca una estación (p. ej. Atocha, Sants)"
                    inputId="station" bind:value={station} autoFocus />
    </div>
  {:else}
    <div on:keydown={(e) => e.key === 'Enter' && goTrain()} role="group">
      <label class="sr-only" for="tnum">Número de tren</label>
      <input id="tnum" type="text" inputmode="numeric" pattern="[0-9]*"
             placeholder="Nº comercial del tren (p. ej. 06180, 21232)"
             bind:value={trainNum} />
    </div>
  {/if}

  <fieldset class="when">
    <legend class="sr-only">Fecha</legend>
    <div class="when-line">
      <div class="chips" role="radiogroup" aria-label="Fecha">
        <button type="button" role="radio" aria-checked={shortcut === 'today'}
                class:on={shortcut === 'today' && !picking} on:click={() => setDay(today)}>Hoy</button>
        <button type="button" role="radio" aria-checked={shortcut === 'tomorrow'}
                class:on={shortcut === 'tomorrow' && !picking} on:click={() => setDay(tomorrow)}>Mañana</button>
        <button type="button" role="radio" aria-checked={shortcut === 'weekend'}
                class:on={shortcut === 'weekend' && !picking}
                on:click={() => setDay(weekend)}
                title={relDay(weekend, today)}>Fin de semana</button>
        <button type="button" role="radio" aria-checked={shortcut === 'pick' || picking}
                class:on={shortcut === 'pick' || picking} on:click={() => (picking = true)}>Elegir fecha</button>
      </div>
      <div class="row">
        {#if picking || shortcut === 'pick'}
          <div class="dt">
            <label for="pdate">Fecha</label>
            <input id="pdate" type="date" bind:value={date} min={today} max={maxDate}
                   on:change={() => (time = '')} />
          </div>
        {/if}
        {#if tab !== 'train'}
          <div class="dt">
            <label for="ptime">Hora <span class="muted">(opcional)</span></label>
            <div class="tline">
              <input id="ptime" type="time" bind:value={time} />
              {#if time}<button type="button" class="link" on:click={() => (time = '')}>
                {date === today ? 'Ahora' : 'Todo el día'}</button>{/if}
            </div>
          </div>
        {/if}
        {#if tab === 'journey'}
          <label class="chk"><input type="checkbox" bind:checked={semidirect} /> Solo semidirectos</label>
        {/if}
      </div>
    </div>
    <p class="when-sum muted" aria-live="polite">
      {relDay(date, today)} · {tab === 'train' ? 'instancia del día elegido' : timeHint}
      {#if outOfRange}<span class="warn"> — fuera del horario oficial disponible
        {maxDate ? `(hasta ${relDay(maxDate, today)})` : ''}</span>{/if}
    </p>
  </fieldset>

  {#if tab === 'journey'}
    <button class="go" on:click={goJourney} disabled={!from || !to || outOfRange}>
      Ver trenes {from && to ? `${from.name} → ${to.name}` : ''}</button>
  {:else if tab === 'station'}
    <button class="go" on:click={goStation} disabled={!station || outOfRange}>
      Ver salidas y llegadas</button>
  {:else}
    <button class="go" on:click={goTrain} disabled={trainNum.trim().length < 3 || outOfRange}>
      Localizar tren</button>
  {/if}
</div>

<style>
  .planner { display: flex; flex-direction: column; gap: .6rem; }
  .tabs { display: flex; gap: .4rem; }
  .tabs button {
    flex: 1; padding: .55rem 0; border-radius: 10px; cursor: pointer;
    border: 1px solid var(--border); background: transparent;
    color: var(--muted); font-size: .9rem; font-weight: 500; min-height: 44px;
  }
  .tabs button.on { background: var(--accent); border-color: transparent;
                    color: var(--accent-fg); font-weight: 700; }
  .od { display: grid; grid-template-columns: 1fr auto 1fr; gap: .5rem; align-items: center; }
  .swap {
    width: 2.4rem; height: 2.4rem; border-radius: 50%; border: 1px solid var(--border);
    background: var(--card); color: var(--muted); cursor: pointer; font-size: 1rem;
  }
  .swap:hover { color: var(--accent); border-color: var(--accent); }
  .when { border: 0; padding: 0; margin: 0; display: flex; flex-direction: column; gap: .4rem; min-width: 0; }
  /* Escritorio: fechas, hora y semidirectos en una sola línea; móvil: se parte solo. */
  .when-line { display: flex; flex-wrap: wrap; gap: .5rem .75rem; align-items: flex-end; }
  .chips { display: flex; gap: .35rem; flex-wrap: wrap; flex: 0 1 auto; }
  .chips button { border: 1px solid var(--border); background: transparent; color: var(--muted);
                  border-radius: 999px; padding: .4rem .85rem; cursor: pointer; font-size: .85rem;
                  min-height: 36px; }
  .chips button.on { border-color: var(--accent); color: var(--accent); font-weight: 650;
                     background: var(--accent-dim); }
  .row { display: flex; gap: .5rem .6rem; align-items: flex-end; flex-wrap: wrap; }
  .dt { display: flex; flex-direction: column; gap: .2rem; min-width: 120px; }
  .dt label { font-size: .75rem; color: var(--muted); }
  .tline { display: flex; gap: .4rem; align-items: center; }
  .link { background: none; border: 0; color: var(--accent); cursor: pointer; font-size: .8rem; }
  .chk { display: flex; align-items: center; gap: .45rem; font-size: .85rem;
         color: var(--muted); white-space: nowrap; min-height: 2.4rem; }
  .chk input { width: auto; accent-color: var(--accent); }
  .when-sum { font-size: .8rem; margin: 0; }
  .warn { color: var(--warn); }
  .go {
    padding: .75rem; border-radius: 10px; border: 0; cursor: pointer;
    background: var(--accent); color: var(--accent-fg);
    font-size: .95rem; font-weight: 600; min-height: 44px;
  }
  .go:disabled { opacity: .45; cursor: default; }
  @media (max-width: 560px) {
    .od { grid-template-columns: 1fr; }
    .swap { justify-self: center; transform: rotate(90deg); }
  }
</style>
