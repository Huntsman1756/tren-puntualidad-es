// Proxy same-origin para QA local, equivalente al Caddy de producción:
//   /api/*  -> API   (127.0.0.1:8001 por defecto, API_URL)
//   resto   -> Astro (127.0.0.1:4381 por defecto, WEB_URL)
// Permite ejecutar v034.mjs sin PUBLIC_API_URL ni relajar CORS.
//   node proxy.mjs        → BASE=http://127.0.0.1:4380 node v034.mjs
import http from 'node:http';

const API = process.env.API_URL || 'http://127.0.0.1:8001';
const WEB = process.env.WEB_URL || 'http://127.0.0.1:4381';
const PORT = Number(process.env.PORT || 4380);

const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade', 'host',
]);

http
  .createServer(async (req, res) => {
    const base = req.url.startsWith('/api') ? API : WEB;
    const target = base + req.url;
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) {
      if (!HOP_BY_HOP.has(k.toLowerCase())) headers.set(k, String(v));
    }
    try {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const body = chunks.length ? Buffer.concat(chunks) : undefined;
      const upstream = await fetch(target, {
        method: req.method,
        headers,
        body: req.method === 'GET' || req.method === 'HEAD' ? undefined : body,
        redirect: 'manual',
      });
      const out = {};
      upstream.headers.forEach((v, k) => {
        if (!HOP_BY_HOP.has(k.toLowerCase())) out[k] = v;
      });
      res.writeHead(upstream.status, out);
      res.end(Buffer.from(await upstream.arrayBuffer()));
    } catch (e) {
      res.writeHead(502);
      res.end('proxy error: ' + String(e?.cause?.code || e));
    }
  })
  .listen(PORT, '127.0.0.1', () => {
    console.log(`same-origin proxy en http://127.0.0.1:${PORT} (/api→${API}, resto→${WEB})`);
  });
