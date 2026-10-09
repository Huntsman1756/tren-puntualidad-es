export function fmtTime(epoch?: number | null): string {
  if (!epoch) return '—';
  return new Date(epoch * 1000).toLocaleTimeString('es-ES', {
    hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Madrid',
  });
}

export function fmtDelay(sec?: number | null): { text: string; cls: string } {
  if (sec == null) return { text: 's/d', cls: 'nodata' };
  const m = Math.round(sec / 60);
  if (sec <= 120) return { text: 'a tiempo', cls: 'ok' };
  if (sec <= 600) return { text: `+${m} min`, cls: 'warn' };
  return { text: `+${m} min`, cls: 'bad' };
}

export function fmtDuration(a?: number | null, b?: number | null): string {
  if (a == null || b == null) return '';
  const m = Math.max(0, Math.round((b - a) / 60));
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}`;
}

export function fmtDate(epoch?: number | null): string {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleDateString('es-ES', {
    weekday: 'short', day: 'numeric', month: 'short', timeZone: 'Europe/Madrid',
  });
}

export function ageText(ts?: number | null): string {
  if (!ts) return 'sin datos';
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (s < 90) return `hace ${s} s`;
  if (s < 5400) return `hace ${Math.round(s / 60)} min`;
  return `hace ${(s / 3600).toFixed(1)} h`;
}

export const FEED_NAME: Record<string, string> = {
  cer: 'Cercanías / Rodalies',
  ld: 'AV · LD · MD',
};

/** "dd/mm HH:MM" en Europe/Madrid (fecha de contenido de un feed). */
export function fmtStamp(epoch?: number | null): string {
  if (!epoch) return '—';
  const parts = new Intl.DateTimeFormat('es-ES', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    hourCycle: 'h23', timeZone: 'Europe/Madrid',
  }).formatToParts(new Date(epoch * 1000));
  const g = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  return `${g('day')}/${g('month')} ${g('hour')}:${g('minute')}`;
}

/** "HH:MM" en Europe/Madrid. */
export function fmtHM(epoch?: number | null): string {
  if (!epoch) return '—';
  return new Intl.DateTimeFormat('es-ES', {
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Europe/Madrid',
  }).format(new Date(epoch * 1000));
}

/** Duración en segundos → "42 min" o "2 h 05 min". */
export function fmtDurSec(sec?: number | null): string {
  if (sec == null) return '';
  const m = Math.max(0, Math.round(sec / 60));
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}
