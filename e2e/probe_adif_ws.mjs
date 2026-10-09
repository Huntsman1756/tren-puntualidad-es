// Sonda ADIF SignalR desde Chromium real (evade el bloqueo de fingerprint TLS).
// Uso: node probe_adif_ws.mjs <estacion> [segundos] [out.json]
import { chromium } from 'playwright';
import { writeFileSync } from 'node:fs';

const station = process.argv[2] || '60000';
const seconds = Number(process.argv[3] || 25);
const out = process.argv[4] || `../pilot_tmp/adif_ws_${station}.json`;

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  locale: 'es-ES',
});
const page = await ctx.newPage();
// cargamos radardetrenes.com: su CSP permite wss://info.adif.es
await page.goto('https://radardetrenes.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });

const result = await page.evaluate(async ({ station, seconds }) => {
  const RS = '\x1e';
  const msgs = [];
  const log = [];
  return await new Promise((resolve) => {
    const ws = new WebSocket('wss://info.adif.es/InfoStation');
    const send = (o) => ws.send(JSON.stringify(o) + RS);
    let inv = 0;
    const invoke = (target, ...args) =>
      send({ arguments: args, invocationId: String(++inv), target, type: 1 });
    const t0 = Date.now();
    ws.onopen = () => {
      log.push('open');
      send({ protocol: 'json', version: 1 });
      invoke('JoinInfo', `PRO-ECM-${station}`);
      invoke('GetLastMessage', `PRO-ECM-${station}`);
    };
    ws.onmessage = (ev) => {
      for (const f of String(ev.data).split(RS)) {
        if (!f) continue;
        try {
          const m = JSON.parse(f);
          if (m.type !== 6) msgs.push(m);
        } catch { /* ignore */ }
      }
      if (Date.now() - t0 > seconds * 1000) { ws.close(); resolve({ msgs, log }); }
    };
    ws.onerror = (e) => { log.push('error'); };
    ws.onclose = (e) => { log.push(`close ${e.code}`); resolve({ msgs, log }); };
    setTimeout(() => { try { ws.close(); } catch {} resolve({ msgs, log }); }, (seconds + 5) * 1000);
  });
}, { station, seconds });

console.error('log:', result.log.join(' | '), '| mensajes:', result.msgs.length);
writeFileSync(out, JSON.stringify(result.msgs, null, 1));
console.error('guardado en', out);
await browser.close();
