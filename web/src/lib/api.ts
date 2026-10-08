// import.meta.env queda horneado en build; el SSR debe leer el entorno en runtime.
const API = (typeof process !== 'undefined' && process.env?.API_INTERNAL_URL)
  || 'http://localhost:8000';
// '' = rutas relativas /api (proxy inverso). En dev directo: PUBLIC_API_URL=http://localhost:8000
export const PUBLIC_API = import.meta.env.PUBLIC_API_URL ?? '';

export interface StopRef { feed: string; stop_id: string }
export interface StationGroup {
  name: string; lat: number | null; lon: number | null;
  stops: StopRef[]; networks?: string[];
}

export function stopsKey(stops: StopRef[]): string {
  return stops.map((s) => `${s.feed}:${s.stop_id}`).join(',');
}

async function get(path: string) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`API ${path}: ${r.status}`);
  return r.json();
}

export const api = {
  station: (feed: string, id: string) => get(`/api/v1/stations/${feed}/${id}`),
  board: (stops: string, kind = 'departures', minutes = 180) =>
    get(`/api/v1/stations/board?stops=${encodeURIComponent(stops)}&kind=${kind}&minutes=${minutes}`),
  journeys: (from: string, to: string) =>
    get(`/api/v1/journeys?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
  train: (feed: string, trip: string) => get(`/api/v1/trains/${feed}/${encodeURIComponent(trip)}`),
  trainByNumber: (n: string) => get(`/api/v1/trains/by-number/${encodeURIComponent(n)}`),
  ranking: (limit = 50) => get(`/api/v1/delays/ranking?limit=${limit}`),
  alerts: (feed = 'cer', stop?: string) =>
    get(`/api/v1/alerts?feed=${feed}${stop ? `&stop_id=${stop}` : ''}`),
  status: () => get('/api/v1/meta/status'),
  dataStatus: () => get('/api/v1/data/status'),
};
