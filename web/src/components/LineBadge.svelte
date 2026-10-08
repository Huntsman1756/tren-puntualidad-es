<script>
  // Distintivo de línea con identidad inequívoca: código + núcleo.
  // Sin núcleo verificable se muestra solo el código (nunca se adivina).
  export let line = null;        // LineInfo | null
  export let fallback = '';      // short_name si no hay line_info
  export let showNucleo = true;
  export let link = true;

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
    <a class="lb" href={line.url} {title}>
      <span class="code" style:background={bg} style:color={fg}>{code}</span>{#if showNucleo && line?.nucleo}<span class="nuc">{line.nucleo.name}</span>{/if}
    </a>
  {:else}
    <span class="lb" {title}>
      <span class="code" style:background={bg} style:color={fg}>{code}</span>{#if showNucleo && line?.nucleo}<span class="nuc">{line.nucleo.name}</span>{/if}
    </span>
  {/if}
{/if}

<style>
  .lb { display: inline-flex; align-items: center; gap: .3rem; white-space: nowrap;
        color: var(--text); vertical-align: middle; }
  .code { display: inline-block; min-width: 2.1rem; text-align: center;
          border-radius: 6px; padding: .08rem .38rem; font-weight: 700;
          font-size: .82rem; background: var(--card2); line-height: 1.35; }
  .nuc { font-size: .72rem; color: var(--muted); }
  a.lb:hover .nuc { color: var(--accent); }
</style>
