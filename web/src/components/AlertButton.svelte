<script>
  // Avisos Web Push del contexto actual (trayecto o estación). Un mismo
  // navegador puede tener varias reglas independientes (p. ej. ida por la
  // mañana y vuelta por la tarde); borrar una no afecta a las demás.
  import { onMount } from 'svelte';
  import { PUBLIC_API } from '../lib/api';
  import {
    createRule, deleteRule, listRules, migrateLegacy, pushSupported,
    sameContext, updateRule,
  } from '../lib/push';

  export let cfg = {};     // { type, from_key?, to_key?, station_key? }
  export let label = '';

  let supported = false, disabled = false, denied = false, busy = false;
  let error = '';
  let rules = [];          // reglas de este contexto
  let editing = null;      // null | 'new' | rule.id
  let form = defaults();

  const DAYNAMES = ['D', 'L', 'M', 'X', 'J', 'V', 'S'];
  const DOW = [1, 2, 3, 4, 5, 6, 0];

  function defaults() {
    return { threshold_min: 5, from_time: '07:00', to_time: '09:30', days: [1, 2, 3, 4, 5] };
  }

  async function refresh() {
    try { rules = (await listRules()).filter((r) => sameContext(r.config, cfg)); }
    catch { error = 'No se pudieron cargar tus avisos.'; }
  }

  onMount(async () => {
    supported = pushSupported();
    if (!supported) return;
    try {
      const r = await fetch(`${PUBLIC_API}/api/v1/push/public-key`);
      if (!r.ok) { disabled = true; return; }
    } catch { disabled = true; return; }
    await migrateLegacy();
    await refresh();
  });

  function startNew() { form = defaults(); editing = 'new'; error = ''; }
  function startEdit(r) {
    form = { threshold_min: r.config.threshold_min ?? 5,
             from_time: r.config.from_time || '00:00',
             to_time: r.config.to_time || '23:59',
             days: r.config.days?.length ? [...r.config.days] : [0, 1, 2, 3, 4, 5, 6] };
    editing = r.id; error = '';
  }

  function payload() {
    return { ...cfg, label, threshold_min: Number(form.threshold_min),
             from_time: form.from_time, to_time: form.to_time,
             days: form.days.length === 7 ? [] : form.days };
  }

  async function save() {
    busy = true; error = '';
    try {
      if (editing === 'new') await createRule(payload());
      else await updateRule(editing, payload());
      editing = null;
      await refresh();
    } catch (e) {
      if (e?.denied) denied = true;
      else error = e?.status === 409 ? 'Has alcanzado el máximo de avisos.' : 'No se pudo guardar el aviso.';
    } finally { busy = false; }
  }

  async function remove(id) {
    busy = true; error = '';
    try { await deleteRule(id); await refresh(); if (editing === id) editing = null; }
    catch { error = 'No se pudo borrar el aviso.'; }
    finally { busy = false; }
  }

  function toggleDay(d) {
    form.days = form.days.includes(d) ? form.days.filter((x) => x !== d) : [...form.days, d].sort();
  }

  function summary(r) {
    const c = r.config;
    const days = c.days?.length ? c.days.slice().sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7))
      .map((d) => DAYNAMES[d]).join('') : 'todos los días';
    return `+${c.threshold_min} min · ${c.from_time || '00:00'}–${c.to_time || '23:59'} · ${days}`;
  }
</script>

{#if supported && !disabled}
  <div class="alerts-box">
    {#each rules as r (r.id)}
      <div class="rule">
        <span class="on">🔔</span>
        <span class="sum">{summary(r)}</span>
        <button class="tool" on:click={() => startEdit(r)} disabled={busy}
                aria-label="Editar aviso">Editar</button>
        <button class="tool danger" on:click={() => remove(r.id)} disabled={busy}
                aria-label="Borrar este aviso">Borrar</button>
      </div>
    {/each}
    {#if editing === null}
      <button class="ab" on:click={startNew} disabled={busy}>
        {rules.length ? '＋ Otro aviso para este contexto' : '🔕 Avisar si hay retrasos'}</button>
    {/if}
    {#if denied}<p class="note muted" role="alert">Notificaciones bloqueadas en el navegador.</p>{/if}
    {#if error}<p class="note err" role="alert">{error}</p>{/if}

    {#if editing !== null}
      <div class="cfg">
        <p class="muted cfg-t">Avisar en {label || 'este contexto'} cuando un tren próximo
          acumule (solo con dato en tiempo real, nunca por horario teórico):</p>
        <div class="row">
          <label>Umbral
            <select bind:value={form.threshold_min}>
              <option value={3}>+3 min</option>
              <option value={5}>+5 min</option>
              <option value={10}>+10 min</option>
              <option value={15}>+15 min</option>
            </select>
          </label>
          <label>Desde <input type="time" bind:value={form.from_time} /></label>
          <label>Hasta <input type="time" bind:value={form.to_time} /></label>
        </div>
        <div class="days" role="group" aria-label="Días">
          {#each DOW as d}
            <button type="button" class="d" class:on={form.days.includes(d)}
                    aria-pressed={form.days.includes(d)} on:click={() => toggleDay(d)}>{DAYNAMES[d]}</button>
          {/each}
        </div>
        <div class="ops">
          <button class="save" on:click={save} disabled={busy || form.days.length === 0}>
            {busy ? '…' : editing === 'new' ? 'Activar aviso' : 'Guardar cambios'}</button>
          <button class="tool" on:click={() => (editing = null)}>Cancelar</button>
        </div>
        <p class="muted legal">Sin cuenta ni correo: el aviso queda ligado a este navegador
          mediante una clave que solo guarda él. Gestiona todos tus avisos en
          <a href="/favoritos#avisos">Favoritos</a> · <a href="/privacidad">privacidad</a>.</p>
      </div>
    {/if}
  </div>
{/if}

<style>
  .alerts-box { display: flex; flex-direction: column; gap: .4rem; margin: .3rem 0; }
  .rule { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; font-size: .82rem;
          border: 1px solid var(--ok); border-radius: 8px; padding: .3rem .6rem; }
  .rule .sum { flex: 1; color: var(--text); }
  .ab { align-self: flex-start; background: none; border: 1px solid var(--border);
        color: var(--muted); border-radius: 8px; padding: .3rem .8rem; cursor: pointer;
        font-size: .85rem; }
  .tool { font-size: .75rem; color: var(--muted); border: 1px solid var(--border);
          background: transparent; border-radius: 7px; padding: .2rem .55rem; cursor: pointer; }
  .tool.danger:hover { color: var(--bad); border-color: var(--bad); }
  .note { font-size: .78rem; margin: 0; }
  .err { color: var(--bad); }
  .cfg { border: 1px solid var(--border); border-radius: 10px; padding: .8rem;
         display: flex; flex-direction: column; gap: .65rem; background: var(--card2); }
  .cfg-t { font-size: .82rem; margin: 0; }
  .row { display: flex; gap: .6rem; flex-wrap: wrap; }
  .row label { display: flex; flex-direction: column; gap: .2rem; font-size: .78rem;
               color: var(--muted); }
  .row input, .row select { font-size: .85rem; padding: .35rem .5rem; width: auto; }
  .days { display: flex; gap: .3rem; }
  .d { width: 2.1rem; height: 2.1rem; border-radius: 50%; border: 1px solid var(--border);
       background: transparent; color: var(--muted); cursor: pointer; font-size: .78rem; }
  .d.on { background: var(--accent); color: var(--accent-fg); border-color: transparent;
          font-weight: 700; }
  .ops { display: flex; gap: .5rem; }
  .save { background: var(--accent); color: var(--accent-fg); border: 0; border-radius: 8px;
          padding: .45rem 1rem; font-weight: 600; cursor: pointer; }
  .save:disabled { opacity: .5; }
  .legal { font-size: .72rem; margin: 0; }
</style>
