<script>
  // Distintivo de línea con identidad inequívoca: código + núcleo.
  // Sin núcleo verificable se muestra solo el código (nunca se adivina).
  // `stack`: el núcleo va en una línea propia bajo el código (tablas estrechas).
  export let line = null;        // LineInfo | null
  export let fallback = '';      // short_name si no hay line_info
  export let showNucleo = true;
  export let link = true;
  export let stack = false;

  $: code = line?.code || fallback || '';
  $: bg = line?.color && /^[0-9A-Fa-f]{6}$/.test(line.color) && line.color.toUpperCase() !== 'FFFFFF'
    ? `#${line.color}` : null;
  $: fg = bg ? (lum(line.color) > 0.55 ? '#111' : '#fff') : null;
  $: title = line?.nucleo
    ? `Línea ${code} · ${line.nucleo.brand} ${line.nucleo.name}`
      + (line.status === 'route_id_format' ? ' (ruta no publicada en el GTFS)' : '')
    : code ? `Línea ${code}` : '';

  function lum(hex) {
    const n = parseInt(hex, 16);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  }
</script>

{#if code}
  {#if link && line?.url}
    <a class="lb" class:stack href={line.url} {title}>
      <span class="code" style:background={bg} style:color={fg}>{code}</span>{#if showNucleo && line?.nucleo}<span class="nuc">{line.nucleo.name}</span>{/if}
    </a>
  {:else}
    <span class="lb" class:stack {title}>
      <span class="code" style:background={bg} style:color={fg}>{code}</span>{#if showNucleo && line?.nucleo}<span class="nuc">{line.nucleo.name}</span>{/if}
    </span>
  {/if}
{/if}

<style>
  /* Nunca desborda su celda: puede partirse en varias líneas. */
  .lb { display: inline-flex; align-items: center; gap: .3rem; flex-wrap: wrap;
        min-width: 0; max-width: 100%; color: var(--text); vertical-align: middle; }
  .lb.stack { flex-direction: column; align-items: flex-start; gap: .15rem; }
  .code { display: inline-block; min-width: 2.1rem; text-align: center; white-space: nowrap;
          border-radius: 6px; padding: .08rem .38rem; font-weight: 700;
          font-size: .82rem; background: var(--card2); line-height: 1.35; }
  .nuc { font-size: .72rem; color: var(--muted); min-width: 0; overflow-wrap: anywhere; }
  .lb.stack .nuc { font-size: .68rem; line-height: 1.2; }
  a.lb:hover .nuc { color: var(--accent); }
</style>
