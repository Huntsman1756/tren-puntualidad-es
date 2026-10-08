import type { APIRoute } from 'astro';

const API = (typeof process !== 'undefined' && process.env?.API_INTERNAL_URL)
  || 'http://localhost:8000';
const SITE = (typeof process !== 'undefined' && process.env?.PUBLIC_SITE_URL)
  || 'https://trenes.h1756.es';

export const GET: APIRoute = async () => {
  const base = SITE.replace(/\/$/, '');
  let urls: string[] = ['/', '/retrasos', '/estado', '/fuentes'];
  try {
    const r = await fetch(`${API}/api/v1/stations/sitemap`);
    if (r.ok) {
      const stops: { key: string }[] = await r.json();
      urls = urls.concat(stops.map((s) => `/estacion/${s.key}`));
    }
  } catch {}
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((u) => `  <url><loc>${base}${u}</loc></url>`).join('\n')}
</urlset>`;
  return new Response(xml, { headers: { 'Content-Type': 'application/xml' } });
};
