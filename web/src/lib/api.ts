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
export interface LineInfo {
  code: string; slug: string | null; family: string | null; family_slug: string | null;
  nucleo: { code: string; slug: string; name: string; brand: string } | null;
  label: string; url: string | null; mode: string | null; status: string;
  color: string | null; text_color: string | null;
}

export function stopsKey(stops: StopRef[]): string {
  return stops.map((s) => `${s.feed}:${s.stop_id}`).join(',');
}

async function get(path: string, base = API) {
  const r = await fetch(base + path, { signal: AbortSignal.timeout(15000) });
  if (!r.ok) {
    const e: any = new Error(`${path} -> ${r.status}`);
    e.status = r.status;
    throw e;
  }
  return r.json();
}

const q = (o: Record<string, any>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) {
    if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
};

export const api = {
  station: (feed: string, id: string) => get(`/api/v1/stations/${feed}/${id}`),
  board: (stops: string, kind = 'departures', minutes = 180,
          date?: string, time?: string, semidirect = false) =>
    get(`/api/v1/stations/board${q({ stops, kind, minutes, date, time, semidirect })}`),
  journeys: (from: string, to: string,
             opts: { date?: string; time?: string; hours?: number; semidirect?: boolean } = {}) =>
    get(`/api/v1/journeys${q({ from, to, ...opts })}`),
  plan: (from: string, to: string,
         opts: { date?: string; time?: string; hours?: number; semidirect?: boolean } = {}) =>
    get(`/api/v1/journeys/plan${q({ from, to, ...opts })}`),
  train: (feed: string, id: string, date?: string) =>
    get(`/api/v1/trains/${feed}/${encodeURIComponent(id)}${q({ date })}`),
  trainByNumber: (n: string, date?: string) =>
    get(`/api/v1/trains/by-number/${encodeURIComponent(n)}${q({ date })}`),
  ranking: (limit = 50, opts: { nucleo?: string; linea?: string; min_delay?: number } = {}) =>
    get(`/api/v1/delays/ranking${q({ limit, ...opts })}`),
  delaysByLine: (opts: { nucleo?: string; min_delay?: number } = {}) =>
    get(`/api/v1/delays/lines${q(opts)}`),
  incidencias: (opts: Record<string, string | undefined> = {}) =>
    get(`/api/v1/incidencias${q(opts)}`),
  /** Señales INFERIDAS de retrasos RT (no son avisos oficiales). */
  anomalias: (opts: Record<string, string | undefined> = {}) =>
    get(`/api/v1/anomalias${q(opts)}`),
  /** Hilos de avisos OFICIALES (canales de Renfe), con sus mensajes originales. */
  avisosOficiales: (opts: Record<string, string | undefined> = {}) =>
    get(`/api/v1/avisos-oficiales${q(opts)}`),
  incidencia: (feed: string, id: string) =>
    get(`/api/v1/incidencias/${feed}/${encodeURIComponent(id)}`),
  nucleos: () => get('/api/v1/nucleos'),
  nucleo: (slug: string) => get(`/api/v1/nucleos/${slug}`),
  linea: (n: string, l: string) => get(`/api/v1/lineas/${n}/${l}`),
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

/** Último día válido común a las redes indicadas (o a todas). */
export function coverageMax(cov: any, feeds?: string[]): string | undefined {
  if (!cov?.feeds) return undefined;
  const fs = (feeds?.length ? feeds : Object.keys(cov.feeds))
    .map((f) => cov.feeds[f]?.last_day).filter(Boolean).sort();
  return fs[0];
}

export function feedsOf(key: string): string[] {
  return [...new Set(key.split(',').map((p) => p.split(':')[0]).filter(Boolean))];
}
