<script>
  // Suscripción Web Push anónima. Sin cuentas: el endpoint lo genera el
  // navegador; guardar la clave en localStorage permite la baja inmediata.
  import { onMount } from 'svelte';
  import { PUBLIC_API } from '../lib/api';

  // cfg: { type:'journey'|'station', from_key?, to_key?, station_key? }
  export let cfg = {};
  export let label = '';

  const LS_KEY = 'tt_push_subs';   // { [cfgHash]: endpoint }
  let supported = false;
  let subscribed = false;
  let busy = false;
  let showCfg = false;
  let threshold = 5;
  let fromTime = '07:00';
  let toTime = '09:30';
  let daysSel = [1, 2, 3, 4, 5];   // L-V (0=domingo)
  let denied = false;
  let disabled = false;

  const DAYNAMES = ['D', 'L', 'M', 'X', 'J', 'V', 'S'];
  const DOW = [1, 2, 3, 4, 5, 6, 0];

  function cfgHash() { return JSON.stringify([cfg.type, cfg.from_key || cfg.station_key, cfg.to_key]); }
  function readSubs() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch { return {}; }
  }

  onMount(async () => {
    supported = 'serviceWorker' in navigator && 'PushManager' in window;
    if (!supported) return;
    const subs = readSubs();
    subscribed = !!subs[cfgHash()];
    try {
      const r = await fetch(`${PUBLIC_API}/api/v1/push/public-key`);
      if (!r.ok) disabled = true;
    } catch { disabled = true; }
  });

  function b64ToUint8(b64) {
    const pad = '='.repeat((4 - b64.length % 4) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function subscribe() {
    busy = true;
    try {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') { denied = true; return; }
      const { key } = await (await fetch(`${PUBLIC_API}/api/v1/push/public-key`)).json();
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: b64ToUint8(key),
      });
      const payload = {
        endpoint: sub.endpoint,
        keys: sub.toJSON().keys,
        config: {
          ...cfg,
          threshold_min: threshold,
          from_time: fromTime, to_time: toTime,
          days: daysSel.length === 7 ? [] : daysSel,
        },
      };
      const r = await fetch(`${PUBLIC_API}/api/v1/push/subscribe`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error('subscribe ' + r.status);
      const subs = readSubs();
      subs[cfgHash()] = sub.endpoint;
      localStorage.setItem(LS_KEY, JSON.stringify(subs));
      subscribed = true; showCfg = false;
    } catch (e) { console.warn(e); }
    finally { busy = false; }
  }

  async function unsubscribe() {
    busy = true;
    try {
      const subs = readSubs();
      const endpoint = subs[cfgHash()];
      if (endpoint) {
        await fetch(`${PUBLIC_API}/api/v1/push/unsubscribe`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint }),
        }).catch(() => {});
        try {
          const reg = await navigator.serviceWorker.ready;
          (await reg.pushManager.getSubscription())?.unsubscribe();
        } catch {}
        delete subs[cfgHash()];
        localStorage.setItem(LS_KEY, JSON.stringify(subs));
      }
      subscribed = false;
    } finally { busy = false; }
  }

  function toggleDay(d) {
    daysSel = daysSel.includes(d) ? daysSel.filter((x) => x !== d) : [...daysSel, d].sort();
  }
</script>

{#if supported && !disabled}
  {#if subscribed}
    <div class="sub-ok">
      <button class="ab on" on:click={unsubscribe} disabled={busy}>
        🔔 Alertas activas{label ? ` · ${label}` : ''}</button>
    </div>
  {:else if !showCfg}
    <button class="ab" on:click={() => (showCfg = true)} disabled={busy}>
      🔕 Avisar si hay retrasos</button>
    {#if denied}<p class="denied muted">Notificaciones bloqueadas en el navegador.</p>{/if}
  {:else}
    <div class="cfg">
      <p class="muted cfg-t">Avisar en {label || 'este contexto'} cuando un tren próximo acumule:</p>
      <div class="row">
        <label>Umbral
          <select bind:value={threshold}>
            <option value={3}>+3 min</option>
            <option value={5}>+5 min</option>
            <option value={10}>+10 min</option>
            <option value={15}>+15 min</option>
          </select>
        </label>
        <label>Desde <input type="time" bind:value={fromTime} /></label>
        <label>Hasta <input type="time" bind:value={toTime} /></label>
      </div>
      <div class="days">
        {#each DOW as d}
          <button type="button" class="d" class:on={daysSel.includes(d)}
                  on:click={() => toggleDay(d)}>{DAYNAMES[d]}</button>
        {/each}
      </div>
      <div class="ops">
        <button class="save" on:click={subscribe} disabled={busy || daysSel.length === 0}>
          {busy ? '…' : 'Activar avisos'}</button>
        <button class="tool" on:click={() => (showCfg = false)}>Cancelar</button>
      </div>
      <p class="muted legal">Sin cuenta ni correo: la suscripción vive en tu navegador.
        Borrado inmediato desde el botón o <a href="/privacidad">privacidad</a>.</p>
    </div>
  {/if}
{/if}

<style>
  .ab { background: none; border: 1px solid var(--border); color: var(--muted);
        border-radius: 8px; padding: .3rem .8rem; cursor: pointer; font-size: .85rem; }
  .ab.on { color: var(--ok); border-color: var(--ok); }
  .denied { font-size: .78rem; margin: .3rem 0 0; }
  .cfg { border: 1px solid var(--border); border-radius: 10px; padding: .8rem;
         margin-top: .6rem; display: flex; flex-direction: column; gap: .65rem;
         background: var(--card2); }
  .cfg-t { font-size: .82rem; margin: 0; }
  .row { display: flex; gap: .6rem; flex-wrap: wrap; }
  .row label { display: flex; flex-direction: column; gap: .2rem; font-size: .78rem;
               color: var(--muted); }
  .row input, .row select { font-size: .85rem; padding: .35rem .5rem; width: auto; }
  .days { display: flex; gap: .3rem; }
  .d { width: 1.9rem; height: 1.9rem; border-radius: 50%; border: 1px solid var(--border);
       background: transparent; color: var(--muted); cursor: pointer; font-size: .78rem; }
  .d.on { background: var(--accent); color: var(--accent-fg); border-color: transparent;
          font-weight: 700; }
  .ops { display: flex; gap: .5rem; }
  .save { background: var(--accent); color: var(--accent-fg); border: 0; border-radius: 8px;
          padding: .45rem 1rem; font-weight: 600; cursor: pointer; }
  .save:disabled { opacity: .5; }
  .legal { font-size: .72rem; margin: 0; }
</style>
