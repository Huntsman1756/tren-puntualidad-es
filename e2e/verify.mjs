// QA e2e: desktop + móvil contra la URL en BASE (prod por defecto).
import { chromium } from 'playwright';

const BASE = process.env.BASE || 'https://trenes.h1756.es';
const b = await chromium.launch();
let ok = true;
async function run(name, mobile, fn) {
  const ctx = await b.newContext({
    ignoreHTTPSErrors: true,
    viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 },
    isMobile: mobile, hasTouch: mobile,
  });
  const pg = await ctx.newPage();
  try { await fn(pg); console.log('OK  ', (mobile?'mob':'dsk'), name); }
  catch (e) { ok = false; console.log('FAIL', (mobile?'mob':'dsk'), name, e.message.split('\n')[0]); }
  await ctx.close();
}
for (const mob of [false, true]) {
  await run('home carga', mob, async (pg) => {
    await pg.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('input[type=text]', { timeout: 8000 });
  });
  await run('buscador autocompleta', mob, async (pg) => {
    await pg.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    const inp = pg.locator('input[type=text]').first();
    await inp.click();
    await inp.pressSequentially('atocha', { delay: 60 });
    await pg.waitForSelector('li[role=option]', { timeout: 8000 });
    if (!(await pg.locator('.st-prov').count()))
      throw new Error('sin provincia en sugerencias');
  });
  await run('explorador territorial', mob, async (pg) => {
    await pg.goto(BASE + '/estaciones', { waitUntil: 'networkidle' });
    await pg.selectOption('select >> nth=0', 'madrid');
    await pg.waitForSelector('.stlist a', { timeout: 8000 });
    const n = await pg.locator('.stlist a').count();
    if (n < 50) throw new Error('solo ' + n + ' estaciones');
    // filtro por texto
    await pg.locator('input[type=text]').pressSequentially('chamartin', { delay: 60 });
    await pg.waitForTimeout(500);
    if (!(await pg.locator('text=Chamart').count())) throw new Error('sin Chamartín');
  });
  await run('comunidad', mob, async (pg) => {
    await pg.goto(BASE + '/comunidades/cataluna', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('a[href^="/provincias/"]');
  });
  await run('provincia', mob, async (pg) => {
    await pg.goto(BASE + '/provincias/guadalajara', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('a[href^="/estacion/"]');
  });
  await run('ficha estación geo', mob, async (pg) => {
    await pg.goto(BASE + '/estacion/cer:18000,ld:18000', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('.geo-line a[href="/comunidades/madrid"]', { timeout: 8000 });
  });
  await run('trayecto', mob, async (pg) => {
    await pg.goto(BASE + '/trayecto?o=cer:18000,ld:18000&d=cer:14112,ld:14112',
      { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('body');
  });
}
await b.close();
console.log(ok ? 'ALL OK' : 'HAY FALLOS');
process.exit(ok ? 0 : 1);
