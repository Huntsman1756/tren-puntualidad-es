// Favoritos e historial en localStorage. Sin cuentas: todo local al dispositivo.
// v2: añade trayectos habituales con nombre, días y franja horaria.

export interface StopRef { feed: string; stop_id: string }
export interface FavStation { key: string; name: string }
export interface FavJourney {
  id: string;
  from: { key: string; name: string };   // key = "cer:18000,ld:18000"
  to: { key: string; name: string };
  label?: string;                        // nombre personalizado ("Casa → trabajo")
  days?: number[];                       // 0..6 (dom..sáb); vacío = todos
  window?: string;                       // "HH:MM-HH:MM" franja habitual
  semidirect?: boolean;                  // preferencia de servicios
  return_pair?: boolean;                 // muestra también el trayecto inverso
}
export interface HistoryItem { key: string; name: string; ts: number }

const KEY = 'tt_favs_v2';
const HIST_KEY = 'tt_history';

interface Store { stations: FavStation[]; journeys: FavJourney[] }

function read(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const p = JSON.parse(raw);
      return { stations: p.stations ?? [], journeys: p.journeys ?? [] };
    }
    // migración desde v1: solo estaciones
    const v1 = JSON.parse(localStorage.getItem('tt_favs') || '[]');
    return { stations: Array.isArray(v1) ? v1 : [], journeys: [] };
  } catch {
    return { stations: [], journeys: [] };
  }
}

function write(s: Store) {
  localStorage.setItem(KEY, JSON.stringify(s));
}

/* ---------- estaciones ---------- */

export function favStations(): FavStation[] { return read().stations; }

export function isFavStation(key: string): boolean {
  return favStations().some((f) => f.key === key);
}

export function toggleFavStation(fav: FavStation): boolean {
  const s = read();
  const i = s.stations.findIndex((f) => f.key === fav.key);
  if (i >= 0) s.stations.splice(i, 1);
  else s.stations.unshift(fav);
  write(s);
  return i < 0;
}

/* ---------- trayectos ---------- */

export function favJourneys(): FavJourney[] { return read().journeys; }

export function journeyId(f: { key: string }, t: { key: string }) {
  return `${f.key}->${t.key}`;
}

export function isFavJourney(fromKey: string, toKey: string): boolean {
  const id = journeyId({ key: fromKey }, { key: toKey });
  return favJourneys().some((j) => j.id === id);
}

export function saveJourney(j: Omit<FavJourney, 'id'> & { id?: string }): FavJourney {
  const s = read();
  const id = j.id ?? journeyId(j.from, j.to);
  const i = s.journeys.findIndex((x) => x.id === id);
  const rec = { ...j, id };
  if (i >= 0) s.journeys[i] = rec; else s.journeys.unshift(rec);
  write(s);
  return rec;
}

export function removeJourney(id: string) {
  const s = read();
  s.journeys = s.journeys.filter((j) => j.id !== id);
  write(s);
}

export function moveJourney(id: string, dir: -1 | 1) {
  const s = read();
  const i = s.journeys.findIndex((j) => j.id === id);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= s.journeys.length) return;
  [s.journeys[i], s.journeys[j]] = [s.journeys[j], s.journeys[i]];
  write(s);
}

/* ---------- historial ---------- */

export function history(): HistoryItem[] {
  try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); }
  catch { return []; }
}

export function pushHistory(item: { key: string; name: string }) {
  const h = history().filter((x) => x.key !== item.key);
  h.unshift({ ...item, ts: Date.now() });
  localStorage.setItem(HIST_KEY, JSON.stringify(h.slice(0, 8)));
}

/* ---------- export / import ---------- */

export function exportJSON(): string {
  return JSON.stringify({ version: 2, ...read(), history: history() }, null, 2);
}

export function importJSON(text: string): { stations: number; journeys: number } {
  const p = JSON.parse(text);
  if (!p || (!Array.isArray(p.stations) && !Array.isArray(p.journeys))) {
    throw new Error('formato no reconocido');
  }
  const s = read();
  const stKeys = new Set(s.stations.map((x) => x.key));
  for (const f of p.stations ?? []) {
    if (f?.key && f?.name && !stKeys.has(f.key)) s.stations.push(f);
  }
  const jIds = new Set(s.journeys.map((x) => x.id));
  for (const j of p.journeys ?? []) {
    if (j?.from?.key && j?.to?.key) {
      const id = j.id ?? journeyId(j.from, j.to);
      if (!jIds.has(id)) s.journeys.push({ ...j, id });
    }
  }
  write(s);
  return { stations: s.stations.length, journeys: s.journeys.length };
}
