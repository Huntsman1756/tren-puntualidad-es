// Web Push v2: un dispositivo (este navegador) con varias reglas.
// El token de posesión se guarda SOLO en este navegador (localStorage).
import { PUBLIC_API } from './api';

const DEV_KEY = 'tt_push_device_v2';     // { device_id, token, endpoint }
const LEGACY_KEY = 'tt_push_subs';       // v0.3.x: { [cfgHash]: endpoint }

export interface RuleConfig {
  type: 'journey' | 'station';
  from_key?: string; to_key?: string; station_key?: string;
  label?: string; days?: number[]; from_time?: string; to_time?: string;
  threshold_min?: number; min_interval_min?: number;
}
export interface Rule { id: string; config: RuleConfig; enabled: boolean;
  last_notify_at: string | null }

interface Dev { device_id: string; token: string; endpoint: string }

function readDev(): Dev | null {
  try { return JSON.parse(localStorage.getItem(DEV_KEY) || 'null'); } catch { return null; }
}
function writeDev(d: Dev | null) {
  try {
    if (d) localStorage.setItem(DEV_KEY, JSON.stringify(d));
    else localStorage.removeItem(DEV_KEY);
  } catch { /* almacenamiento no disponible */ }
}

export function pushSupported(): boolean {
  return typeof window !== 'undefined' && 'serviceWorker' in navigator
    && 'PushManager' in window && 'Notification' in window;
}

export function hasDevice(): boolean { return !!readDev(); }

async function req(method: string, path: string, body?: any) {
  const dev = readDev();
  const r = await fetch(`${PUBLIC_API}/api/v1/push${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(dev ? { Authorization: `Bearer ${dev.token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { writeDev(null); throw Object.assign(new Error('auth'), { status: 401 }); }
  if (!r.ok) throw Object.assign(new Error(`push ${path} ${r.status}`), { status: r.status });
  return r.json();
}

function b64ToUint8(b64: string) {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** Garantiza suscripción del navegador + dispositivo registrado.
 *  Pide permiso de notificaciones si hace falta. */
export async function ensureDevice(): Promise<Dev> {
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw Object.assign(new Error('denied'), { denied: true });
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    const { key } = await (await fetch(`${PUBLIC_API}/api/v1/push/public-key`)).json();
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true,
                                            applicationServerKey: b64ToUint8(key) });
  }
  const dev = readDev();
  if (dev && dev.endpoint === sub.endpoint) return dev;
  const r = await fetch(`${PUBLIC_API}/api/v1/push/devices`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json',
               ...(dev ? { Authorization: `Bearer ${dev.token}` } : {}) },
    body: JSON.stringify({ endpoint: sub.endpoint, keys: sub.toJSON().keys }),
  });
  if (!r.ok) throw new Error(`devices ${r.status}`);
  const d = await r.json();
  const out = { device_id: d.device_id, token: d.token ?? dev?.token, endpoint: sub.endpoint };
  writeDev(out);
  try { localStorage.removeItem(LEGACY_KEY); } catch { /* */ }
  return out;
}

/** Reclama automáticamente un dispositivo de v0.3.x si el navegador ya
 *  estaba suscrito (sin pedir permiso de nuevo). */
export async function migrateLegacy(): Promise<void> {
  if (!pushSupported() || readDev()) return;
  let legacy: Record<string, string> = {};
  try { legacy = JSON.parse(localStorage.getItem(LEGACY_KEY) || '{}'); } catch { /* */ }
  if (!Object.keys(legacy).length || Notification.permission !== 'granted') return;
  try { await ensureDevice(); } catch { /* se reintentará al activar avisos */ }
}

export async function listRules(): Promise<Rule[]> {
  if (!readDev()) return [];
  try { return (await req('GET', '/rules')).rules; }
  catch (e: any) { if (e.status === 401) return []; throw e; }
}

export async function createRule(config: RuleConfig): Promise<Rule> {
  await ensureDevice();
  return req('POST', '/rules', { config });
}

export async function updateRule(id: string, config: RuleConfig, enabled = true): Promise<Rule> {
  return req('PUT', `/rules/${id}`, { config, enabled });
}

/** Borra UNA regla; si no quedan, da de baja el navegador. */
export async function deleteRule(id: string): Promise<number> {
  const { remaining } = await req('DELETE', `/rules/${id}`);
  if (remaining === 0) await unsubscribeAll();
  return remaining;
}

export async function unsubscribeAll(): Promise<void> {
  try { await req('DELETE', '/devices/me'); } catch { /* ya no existía */ }
  writeDev(null);
  try {
    const reg = await navigator.serviceWorker.ready;
    await (await reg.pushManager.getSubscription())?.unsubscribe();
  } catch { /* */ }
}

/** ¿La regla corresponde a este contexto (trayecto/estación)? */
export function sameContext(r: RuleConfig, cfg: RuleConfig): boolean {
  if (r.type !== cfg.type) return false;
  return r.type === 'journey'
    ? r.from_key === cfg.from_key && r.to_key === cfg.to_key
    : r.station_key === cfg.station_key;
}
