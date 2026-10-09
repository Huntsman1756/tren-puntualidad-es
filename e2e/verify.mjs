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
    await pg.goto(BASE + '/trayecto?from=cer:18000,ld:18000&to=cer:14112,ld:14112',
      { waitUntil: 'domcontentloaded' });
    // el título debe venir de las estaciones reales, no del valor por defecto
    await pg.waitForSelector('h1', { timeout: 15000 });
    const h1 = (await pg.locator('h1').first().textContent()) || '';
    if (!h1.includes('→') || h1.trim() === 'Origen → Destino')
      throw new Error('título de trayecto por defecto: ' + h1);
  });
  await run('estadísticas: página y filtros', mob, async (pg) => {
    await pg.goto(BASE + '/estadisticas', { waitUntil: 'domcontentloaded' });
    // con STATS_PUBLIC apagado la página debe mostrar el estado de validación
    if (await pg.locator('.validation').count()) return;
    await pg.waitForSelector('.statsx select', { timeout: 8000 });
    if (!(await pg.locator('text=no es puntualidad real').count())
        && !(await pg.locator('text=no es').count()))
      throw new Error('sin aviso de semántica');
    // núcleo Madrid -> líneas y ejecución
    await pg.selectOption('form.filtros select >> nth=0', 'madrid');
    await pg.locator('button.go').click();
    await pg.waitForSelector('.kindcard', { timeout: 20000 });
    if (!(await pg.locator('.gate').count()))
      throw new Error('sin gate visible');
    if (!(await pg.locator('text=Comparativa').count()))
      throw new Error('sin bloque comparativa');
  });
  await run('estadísticas: metodología accesible', mob, async (pg) => {
    await pg.goto(BASE + '/estadisticas', { waitUntil: 'domcontentloaded' });
    if (await pg.locator('.validation').count()) return;
    await pg.waitForSelector('details.met summary', { timeout: 8000 });
    await pg.locator('details.met summary').click();
    await pg.waitForSelector('details.met li', { timeout: 8000 });
    if (!(await pg.locator('details.met').locator('text=Gate').count()))
      throw new Error('metodología sin gate');
  });
}
await b.close();
console.log(ok ? 'ALL OK' : 'HAY FALLOS');
process.exit(ok ? 0 : 1);
