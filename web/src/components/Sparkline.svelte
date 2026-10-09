<script>
  // Mini gráfico SVG (sin librerías) del % de trenes con +15 min a lo largo del episodio.
  // Escala fija 0–100 %. Accesible: role=img con min/máx/último en aria-label.
  export let ev = [];
  export let w = 110;
  export let h = 26;

  $: pts = (ev || []).filter((p) => p && p.share_pct != null);
  $: ok = pts.length >= 2;
  $: vals = pts.map((p) => p.share_pct);
  $: d = ok ? pts.map((p, i) => {
      const x = (i / (pts.length - 1)) * w;
      const y = h - (Math.min(100, Math.max(0, p.share_pct)) / 100) * h;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(' ') : '';
  $: label = ok
    ? `Evolución del % de trenes con +15 min: mínimo ${Math.min(...vals)} %, máximo ${Math.max(...vals)} %, último ${vals[vals.length - 1]} %`
    : 'Evolución no disponible';
</script>

{#if ok}
  <svg class="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label}>
    <path d={d} fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" />
  </svg>
{/if}

<style>
  .spark { display: block; color: var(--warn); overflow: visible; flex: none; }
</style>
