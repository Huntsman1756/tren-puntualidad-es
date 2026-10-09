// Utilidades compartidas por las vistas de estadísticas (explorador y días).
import { PUBLIC_API } from './api';

export const STATS_VALIDATION =
  'Estadísticas históricas en validación: se publicarán cuando la muestra supere los controles de calidad.';

// Los dos tipos de dato se muestran siempre por separado. Nunca "puntualidad real".
export const KIND_LABEL: Record<string, { name: string; src: string }> = {
  reported: { name: 'Retraso informado (flota)', src: 'visor de flota de Renfe' },
  prediction: { name: 'Predicción GTFS-RT', src: 'trip_updates GTFS-RT' },
};

export const REASON_TEXT: Record<string, string> = {
  dia_en_curso: 'día en curso',
  antes_de_captura: 'antes del inicio de captura',
  dia_inicio_captura: 'día de inicio de captura (parcial)',
  snapshot_retrospectivo: 'horario capturado a posteriori',
  sin_snapshot: 'sin horario capturado',
  sin_registro_captura: 'sin registro de captura',
  inicio_tardio: 'captura iniciada tarde',
  fin_temprano: 'captura terminada pronto',
  hueco_captura: 'hueco de captura >30 min',
  sin_fuente: 'fuente no disponible',
};

export const reasonText = (c: string): string => REASON_TEXT[c] ?? c.replace(/_/g, ' ');

const CHECK_TEXT: Record<string, string> = {
  min_instances: 'n circulaciones',
  min_days: 'días representativos',
  min_coverage_pct: 'cobertura',
};

/** Nombres legibles de los controles que no se cumplen (con valor y mínimo si la API los trae). */
export function failedChecks(g: any): string[] {
  if (!g?.checks) return [];
  return Object.entries(g.checks)
    .filter(([, v]: [string, any]) => v && !v.pass)
    .map(([k, v]: [string, any]) => {
      const name = CHECK_TEXT[k] ?? k;
      const val = v.value ?? v.actual;
      const req = v.threshold ?? v.required ?? v.min;
      return val != null && req != null ? `${name} (${val} de ${req} mínimo)` : name;
    });
}

/** Resumen de motivos de exclusión: "captura parcial…" con los más frecuentes. */
export function excludedSummary(list: { reasons?: string[] }[] | null | undefined): string {
  const count = new Map<string, number>();
  for (const d of list || []) {
    for (const r of d.reasons || []) {
      const t = reasonText(r);
      count.set(t, (count.get(t) || 0) + 1);
    }
  }
  const top = [...count.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([t]) => t);
  const n = (list || []).length;
  if (!n) return '';
  return `${n} ${n === 1 ? 'día excluido' : 'días excluidos'}`
    + (top.length ? `: ${top.join(', ')}${count.size > 3 ? '…' : ''}` : '');
}

export const fmtMin = (s: number | null | undefined): string => s == null ? '—'
  : `${s < 0 ? '−' : '+'}${Math.abs(Math.round(s / 60))} min`;

export const fmtNum = (v: number | null | undefined): string => v == null ? '—'
  : Number(v).toLocaleString('es-ES', { maximumFractionDigits: 1 });

export const fmtPct = (v: number | null | undefined): string => v == null ? '—' : `${fmtNum(v)}%`;

/** "lun 6 oct" en Europe/Madrid, a partir de una fecha civil YYYY-MM-DD. */
export function fmtDayShort(iso: string): string {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString('es-ES', {
    weekday: 'short', day: 'numeric', month: 'short', timeZone: 'Europe/Madrid',
  });
}

export const SCOPE_KEYS = ['nucleo', 'line', 'feed', 'ccaa', 'provincia', 'station', 'from', 'to'];

/** Respuesta sin excepciones: status 404 = estadísticas no publicadas (validación). */
export async function statsGet(path: string): Promise<{ status: number; data: any }> {
  const r = await fetch(PUBLIC_API + path);
  let data: any = null;
  try { data = await r.json(); } catch { /* cuerpo no JSON */ }
  return { status: r.status, data };
}
