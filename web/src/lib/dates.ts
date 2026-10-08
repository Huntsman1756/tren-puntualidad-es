// Fechas SIEMPRE en Europe/Madrid (nunca toISOString, que es UTC: entre
// las 00:00 y las 02:00 de Madrid devolvía el día anterior).

const TZ = 'Europe/Madrid';

function parts(d: Date) {
  const f = new Intl.DateTimeFormat('en-CA', {
    timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', weekday: 'short',
  }).formatToParts(d);
  const g = (t: string) => f.find((p) => p.type === t)?.value ?? '';
  return { y: g('year'), m: g('month'), d: g('day'), hh: g('hour'), mm: g('minute'),
           wd: g('weekday') };
}

/** YYYY-MM-DD de hoy en Madrid. */
export function madridToday(now = new Date()): string {
  const p = parts(now);
  return `${p.y}-${p.m}-${p.d}`;
}

/** HH:MM actual en Madrid. */
export function madridNowHM(now = new Date()): string {
  const p = parts(now);
  return `${p.hh}:${p.mm}`;
}

/** Suma días a una fecha civil YYYY-MM-DD (sin husos: aritmética UTC pura). */
export function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.toISOString().slice(0, 10);
}

/** 0=domingo … 6=sábado de una fecha civil. */
export function weekday(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

/** Próximo sábado (hoy si ya es sábado; si es domingo, hoy). */
export function weekendStart(today = madridToday()): string {
  const wd = weekday(today);
  if (wd === 6 || wd === 0) return today;
  return addDays(today, 6 - wd);
}

export function fmtDayLong(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d, 12)).toLocaleDateString('es-ES', {
    weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC',
  });
}

/** Etiqueta relativa: Hoy / Mañana / sábado 10 oct. */
export function relDay(iso: string, today = madridToday()): string {
  if (iso === today) return 'Hoy';
  if (iso === addDays(today, 1)) return 'Mañana';
  return fmtDayLong(iso);
}

/** Construye query string preservando fecha/hora de contexto. */
export function withDate(href: string, date?: string | null, time?: string | null,
                         today = madridToday()): string {
  const u = new URL(href, 'http://x');
  if (date && date !== today) u.searchParams.set('date', date);
  else if (date === today && time) u.searchParams.set('date', date);
  if (time) u.searchParams.set('time', time);
  return u.pathname + (u.search ? u.search : '');
}
