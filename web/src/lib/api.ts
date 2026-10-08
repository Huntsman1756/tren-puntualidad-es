// import.meta.env queda horneado en build; el SSR debe leer el entorno en runtime.
const API = (typeof process !== 'undefined' && process.env?.API_INTERNAL_URL)
  || 'http://localhost:8000';
// '' = rutas relativas /api (proxy inverso). En dev directo: PUBLIC_API_URL=http://localhost:8000
export const PUBLIC_API = import.meta.env.PUBLIC_API_URL ?? '';

export interface StopRef { feed: string; stop_id: string }
export interface StationGroup {
  name: string; lat: number | null; lon: number | null;
  stops: StopRef[]; networks: string[];
}

export function stopsKey(stops: StopRef[]): string {
  return stops.map((s) => `${s.feed}:${s.stop_id}`).join(',');
}

async function get(path: string, base = API) {
  const r = await fetch(base + path, { signal: AbortSignal.timeout(15000) });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

export const api = {
  station: (feed: string, id: string) => get(`/api/v1/stations/${feed}/${id}`),
  board: (stops: string, kind = 'departures', minutes = 180,
          date?: string, time?: string, semidirect = false) =>
    get(`/api/v1/stations/board?stops=${encodeURIComponent(stops)}&kind=${kind}`
      + `&minutes=${minutes}${date ? `&date=${date}` : ''}${time ? `&time=${time}` : ''}`
      + (semidirect ? '&semidirect=true' : '')),
  journeys: (from: string, to: string,
             opts: { date?: string; time?: string; hours?: number; semidirect?: boolean } = {}) =>
    get(`/api/v1/journeys?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`
      + `${opts.date ? `&date=${opts.date}` : ''}${opts.time ? `&time=${opts.time}` : ''}`
      + `${opts.hours ? `&hours=${opts.hours}` : ''}${opts.semidirect ? '&semidirect=true' : ''}`),
  train: (feed: string, id: string) =>
    get(`/api/v1/trains/${feed}/${encodeURIComponent(id)}`),
  trainByNumber: (n: string, date?: string) =>
    get(`/api/v1/trains/by-number/${encodeURIComponent(n)}${date ? `?date=${date}` : ''}`),
  ranking: (limit = 50) => get(`/api/v1/delays/ranking?limit=${limit}`),
  alerts: (feed = 'cer', stop?: string) =>
    get(`/api/v1/alerts?feed=${feed}${stop ? `&stop_id=${stop}` : ''}`),
  status: () => get('/api/v1/meta/status'),
  dataStatus: () => get('/api/v1/data/status'),
  coverage: () => get('/api/v1/meta/coverage'),
  punctuality: (feed: string, id: string, days = 7) =>
    get(`/api/v1/stations/${feed}/${id}/punctuality?days=${days}`),
  statsOptions: () => get('/api/v1/stats/options'),
  statsDelays: (q: Record<string, string>) =>
    get(`/api/v1/stats/delays?${new URLSearchParams(q)}`),
  statsCompare: (q: Record<string, string>) =>
    get(`/api/v1/stats/compare?${new URLSearchParams(q)}`),
  geoCcaa: () => get('/api/v1/geo/ccaa'),
  geoProv: (ccaa: string) => get(`/api/v1/geo/ccaa/${ccaa}`),
};
