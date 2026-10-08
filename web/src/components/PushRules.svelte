<script>
  // Todos los avisos Web Push de este navegador: ver, pausar, borrar.
  import { onMount } from 'svelte';
  import {
    deleteRule, listRules, migrateLegacy, pushSupported, unsubscribeAll, updateRule,
  } from '../lib/push';

  let supported = false, loading = true, error = '', busy = false;
  let rules = [];
  const DAYNAMES = ['D', 'L', 'M', 'X', 'J', 'V', 'S'];

  async function load() {
    loading = true;
    try { rules = await listRules(); error = ''; }
    catch { error = 'Error de consulta: no se pudieron cargar tus avisos.'; }
    finally { loading = false; }
  }

  onMount(async () => {
    supported = pushSupported();
    if (!supported) { loading = false; return; }
    await migrateLegacy();
    await load();
  });

  function where(c) {
    if (c.label) return c.label;
    return c.type === 'journey' ? `${c.from_key} → ${c.to_key}` : c.station_key;
  }
  function href(c) {
    return c.type === 'journey'
      ? `/trayecto?${new URLSearchParams({ from: c.from_key, to: c.to_key })}`
      : `/estacion/${c.station_key}`;
  }
  function when(c) {
    const d = c.days?.length ? c.days.map((x) => DAYNAMES[x]).join('') : 'todos los días';
    return `+${c.threshold_min} min · ${c.from_time || '00:00'}–${c.to_time || '23:59'} · ${d}`;
  }

  async function toggle(r) {
    busy = true;
    try { await updateRule(r.id, r.config, !r.enabled); await load(); }
    catch { error = 'No se pudo cambiar el aviso.'; } finally { busy = false; }
  }
  async function del(r) {
    busy = true;
    try { await deleteRule(r.id); await load(); }
    catch { error = 'No se pudo borrar el aviso.'; } finally { busy = false; }
  }
  async function all() {
    busy = true;
    try { await unsubscribeAll(); rules = []; } finally { busy = false; }
  }
</script>

{#if !supported}
  <p class="muted">Este navegador no admite notificaciones push.</p>
{:else if loading}
  <p class="muted" aria-busy="true">Cargando tus avisos…</p>
{:else}
  {#if error}<p class="err" role="alert">{error}</p>{/if}
  {#if rules.length === 0}
    <p class="muted">No tienes avisos activos en este navegador. Actívalos desde la página de
      un trayecto o una estación («Avisar si hay retrasos»).</p>
  {:else}
    <ul class="rules">
      {#each rules as r (r.id)}
        <li class:off={!r.enabled}>
          <a href={href(r.config)}>{where(r.config)}</a>
          <span class="muted">{r.config.type === 'journey' ? 'trayecto' : 'estación'} · {when(r.config)}</span>
          <span class="ops">
            <button class="tool" on:click={() => toggle(r)} disabled={busy}>
              {r.enabled ? 'Pausar' : 'Reanudar'}</button>
            <button class="tool danger" on:click={() => del(r)} disabled={busy}>Borrar</button>
          </span>
        </li>
      {/each}
    </ul>
    <button class="tool danger" on:click={all} disabled={busy}>Borrar todos y desactivar notificaciones</button>
  {/if}
{/if}

<style>
  .rules { list-style: none; padding: 0; margin: 0 0 .8rem; }
  .rules li { display: flex; flex-wrap: wrap; gap: .3rem .7rem; align-items: center;
              padding: .55rem 0; border-top: 1px solid var(--border); }
  .rules li.off { opacity: .6; }
  .rules .muted { font-size: .8rem; flex: 1; }
  .ops { display: flex; gap: .4rem; }
  .tool { font-size: .78rem; color: var(--muted); border: 1px solid var(--border);
          background: transparent; border-radius: 7px; padding: .3rem .6rem; cursor: pointer;
          min-height: 32px; }
  .tool.danger:hover { color: var(--bad); border-color: var(--bad); }
  .err { color: var(--bad); font-size: .85rem; }
</style>
