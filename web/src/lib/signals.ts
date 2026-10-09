// Helpers de episodios de anomalías (contrato v0.4). Degradan si faltan campos.
export const RANK: Record<string, number> = { confirmada: 0, observacion: 1, resuelta: 2 };
export const STATUS_LBL: Record<string, string> = {
  observacion: 'En observación', confirmada: 'Confirmada', resuelta: 'Resuelta',
};
export const STATUS_CLS: Record<string, string> = {
  observacion: 'neutral', confirmada: 'warn', resuelta: 'ok muted',
};

export const minOf = (s?: number | null) => (s == null ? 0 : Math.round(s / 60));
export const countOf = (v: any): number => (typeof v === 'number' ? v : Array.isArray(v) ? v.length : 0);
export const isOpen = (it: any) => it?.status !== 'resuelta';

/** Cifras actuales de un episodio (`current`; si falta, campos planos del contrato anterior). */
export function curOf(it: any) {
  const c = it?.current ?? it ?? {};
  const monitored = c.monitored ?? null;
  const delayed15 = c.delayed_15 ?? 0;
  let share: number | null = c.share_pct ?? (c.share != null ? Math.round(c.share * 100) : null);
  if (share == null && monitored) share = Math.round((delayed15 / monitored) * 100);
  return {
    monitored, delayed15, scheduled: c.scheduled_now ?? null, share,
    maxDelay: c.max_delay_sec ?? null, trains: (c.trains || []) as any[],
  };
}

/** Pico del episodio con cobertura (necesita el punto de evolución con `monitored`). */
export function peakOf(it: any) {
  const pk = it?.peak || {};
  const pts = (it?.evolution || []).filter((p: any) => p?.monitored && p?.share_pct != null);
  if (!pts.length || pk.share_pct == null) return null;
  const top = pts.reduce((a: any, p: any) => (p.share_pct > a.share_pct ? p : a));
  return {
    delayed15: top.delayed_15, monitored: top.monitored, share: top.share_pct,
    maxDelay: pk.max_delay_sec ?? top.max_delay_sec ?? null, ts: top.ts ?? null,
  };
}

export const lineUrlOf = (it: any) => it.line?.url || `/lineas/${it.nucleo?.slug}/${it.line?.slug}`;
export const scopeOf = (it: any) =>
  `nucleo=${encodeURIComponent(it.nucleo?.slug || '')}&linea=${encodeURIComponent(it.line?.slug || '')}`;
