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

export const FEED_NAME: Record<string, string> = {
  cer: 'Cercanías / Rodalies',
  ld: 'AV · LD · MD',
};
