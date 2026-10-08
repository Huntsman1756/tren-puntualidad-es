import type { APIRoute } from 'astro';

const API = (typeof process !== 'undefined' && process.env?.API_INTERNAL_URL)
  || 'http://localhost:8000';
const SITE = (typeof process !== 'undefined' && process.env?.PUBLIC_SITE_URL)
  || 'https://trenes.h1756.es';

export const GET: APIRoute = async () => {
  const base = SITE.replace(/\/$/, '');
  let urls: string[] = ['/', '/retrasos', '/estado', '/fuentes', '/estaciones'];
  try {
    const [r, g] = await Promise.all([
      fetch(`${API}/api/v1/stations/sitemap`),
      fetch(`${API}/api/v1/geo/ccaa`),
    ]);
    if (r.ok) {
      const stops: { key: string }[] = await r.json();
      urls = urls.concat(stops.map((s) => `/estacion/${s.key}`));
    }
    if (g.ok) {
      const ccaas: { slug: string }[] = await g.json();
      for (const c of ccaas) urls.push(`/comunidades/${c.slug}`);
      const provs = await Promise.all(ccaas.map((c) =>
        fetch(`${API}/api/v1/geo/ccaa/${c.slug}`).then((x) => x.ok ? x.json() : null).catch(() => null)));
      for (const d of provs) {
        if (d?.provinces) for (const p of d.provinces) urls.push(`/provincias/${p.slug}`);
      }
    }
  } catch {}
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((u) => `  <url><loc>${base}${u}</loc></url>`).join('\n')}
</urlset>`;
  return new Response(xml, { headers: { 'Content-Type': 'application/xml' } });
};
