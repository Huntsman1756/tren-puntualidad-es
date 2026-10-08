const API = import.meta.env.API_INTERNAL_URL || 'http://localhost:8000';
// '' = rutas relativas /api (Caddy). En dev directo: PUBLIC_API_URL=http://localhost:8000
export const PUBLIC_API = import.meta.env.PUBLIC_API_URL ?? '';

async function get(path: string) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`API ${path}: ${r.status}`);
  return r.json();
}

export const api = {
  station: (feed: string, id: string) => get(`/api/stations/${feed}/${id}`),
  board: (feed: string, id: string, kind = 'departures', minutes = 120) =>
    get(`/api/stations/${feed}/${id}/board?kind=${kind}&minutes=${minutes}`),
  journeys: (from: string, to: string) =>
    get(`/api/journeys?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
  train: (feed: string, trip: string) => get(`/api/trains/${feed}/${encodeURIComponent(trip)}`),
  ranking: (limit = 50) => get(`/api/delays/ranking?limit=${limit}`),
  alerts: (feed = 'cer', stop?: string) =>
    get(`/api/alerts?feed=${feed}${stop ? `&stop_id=${stop}` : ''}`),
  status: () => get('/api/meta/status'),
};
