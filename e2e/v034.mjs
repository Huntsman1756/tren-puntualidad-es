// QA e2e v0.3.4 (escritorio + móvil): identidad de líneas, incidencias,
// planificador con fechas futuras, navegación. BASE = URL de la web.
//   BASE=http://127.0.0.1:4381 node v034.mjs
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE = process.env.BASE || 'https://trenes.h1756.es';
const SHOTS = process.env.SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const madrid = (d = new Date()) => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Madrid', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const TODAY = madrid();
const D1 = (() => { const [y, m, d] = TODAY.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10); })();

const b = await chromium.launch();
let fails = 0, oks = 0;
async function run(name, mobile, fn) {
  const ctx = await b.newContext({
    ignoreHTTPSErrors: true, locale: 'es-ES', timezoneId: 'Europe/Madrid',
    viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 860 },
    isMobile: mobile, hasTouch: mobile,
  });
  const pg = await ctx.newPage();
  const errors = [];
  pg.on('pageerror', (e) => errors.push(e.message));
  try {
    await fn(pg);
    if (errors.length) throw new Error('JS error: ' + errors[0]);
    oks++; console.log('OK  ', mobile ? 'mob' : 'dsk', name);
  } catch (e) {
    fails++; console.log('FAIL', mobile ? 'mob' : 'dsk', name, '—', e.message.split('\n')[0]);
  }
  if (SHOTS) await pg.screenshot({ path: `${SHOTS}/${mobile ? 'mob' : 'dsk'}-${name.replace(/\W+/g, '_')}.png`, fullPage: true }).catch(() => {});
  await ctx.close();
}
const expect = (c, msg) => { if (!c) throw new Error(msg); };

async function pickStation(pg, id, text) {
  const inp = pg.locator(`#${id}`);
  await inp.click();
  await inp.pressSequentially(text, { delay: 50 });
  await pg.waitForSelector(`#${id}-list li[role=option]`, { timeout: 10000 });
  await pg.locator(`#${id}-list li[role=option]`).first().dispatchEvent('mousedown');
}

for (const mob of [false, true]) {
  await run('portada: fecha por defecto Madrid y atajo Mañana sin hora', mob, async (pg) => {
    await pg.goto(BASE + '/', { waitUntil: 'networkidle' });
    const sum = pg.locator('.when-sum');
    expect((await sum.textContent()).includes('Hoy'), 'el resumen no indica Hoy');
    await pg.getByRole('radio', { name: 'Mañana' }).click();
    const t = await sum.textContent();
    expect(t.includes('Mañana') && t.includes('todo el día'), `resumen: ${t}`);
    expect((await pg.locator('#ptime').inputValue()) === '', 'la hora se arrastró');
  });

  await run('estación desde portada respeta fecha', mob, async (pg) => {
    await pg.goto(BASE + '/', { waitUntil: 'networkidle' });
    await pg.getByRole('tab', { name: 'Estación' }).click();
    await pickStation(pg, 'station', 'chamart');
    await pg.getByRole('radio', { name: 'Mañana' }).click();
    await Promise.all([pg.waitForURL(/\/estacion\//), pg.getByRole('button', { name: /Ver salidas/ }).click()]);
    expect(pg.url().includes(`date=${D1}`), 'sin date en ' + pg.url());
    await pg.waitForSelector('text=Horario programado', { timeout: 10000 });
  });

  await run('trayecto futuro → ficha de tren de ese día', mob, async (pg) => {
    await pg.goto(`${BASE}/trayecto?from=cer:17000&to=cer:18000&date=${D1}`, { waitUntil: 'networkidle' });
    await pg.waitForSelector('text=Horario programado', { timeout: 10000 });
    // los primeros de madrugada pueden ser servicios de HOY que cruzan
    // medianoche (enlazan a la instancia de hoy); el resto, a la de mañana
    const hrefs = await pg.locator('a.jrow').evaluateAll((as) => as.map((x) => x.getAttribute('href')));
    const late = hrefs.slice(-3);
    expect(late.every((h) => h.includes(`date=${D1}`)), 'enlaces sin fecha: ' + late.join(' '));
    const a = pg.locator(`a.jrow[href*="date=${D1}"]`).first();
    await Promise.all([pg.waitForURL(/\/tren\//), a.click()]);
    await pg.waitForSelector('text=Horario programado del mañana', { timeout: 10000 });
    expect(!(await pg.locator('th', { hasText: 'Estimado' }).count()), 'muestra columna Estimado');
    const badges = await pg.locator('h1 + p .lb').count();
    expect(badges >= 1, 'sin distintivo de línea');
  });

  await run('trayecto sin directo: requiere transbordo', mob, async (pg) => {
    await pg.goto(`${BASE}/trayecto?from=cer:17000&to=cer:15211&date=${D1}`, { waitUntil: 'networkidle' });
    await pg.waitForSelector('text=Requiere transbordo', { timeout: 10000 });
  });

  await run('trayecto fuera de cobertura', mob, async (pg) => {
    await pg.goto(`${BASE}/trayecto?from=cer:17000&to=cer:18000&date=2030-01-01`, { waitUntil: 'networkidle' });
    await pg.waitForSelector('text=Fuera de la cobertura del calendario', { timeout: 10000 });
  });

  await run('líneas homónimas: C1 Madrid ≠ C1 Asturias', mob, async (pg) => {
    await pg.goto(BASE + '/lineas/madrid/c1', { waitUntil: 'domcontentloaded' });
    const h1 = await pg.locator('h1').textContent();
    expect(h1.includes('C1') && h1.includes('Madrid'), h1);
    const mad = await pg.locator('ol.stops li').allTextContents();
    await pg.goto(BASE + '/lineas/asturias/c1', { waitUntil: 'domcontentloaded' });
    expect((await pg.locator('h1').textContent()).includes('Asturias'), 'h1 asturias');
    const ast = await pg.locator('ol.stops li').allTextContents();
    expect(mad.length && ast.length && mad[0] !== ast[0], 'mismas estaciones');
  });

  await run('línea → estación → líneas que la sirven', mob, async (pg) => {
    await pg.goto(BASE + '/lineas/madrid/c4', { waitUntil: 'domcontentloaded' });
    const st = pg.locator('ol.stops a').first();
    await Promise.all([pg.waitForURL(/\/estacion\//), st.click()]);
    const lines = pg.locator('p.lines a.lb');
    expect(await lines.count() >= 1, 'estación sin líneas');
    const t = await lines.first().getAttribute('title');
    expect(/Madrid/.test(t), 'el distintivo no indica núcleo: ' + t);
  });

  await run('incidencias: estado de fuente y filtro por núcleo', mob, async (pg) => {
    await pg.goto(BASE + '/incidencias', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('.src .badge', { timeout: 10000 });
    await pg.selectOption('select[name=nucleo]', 'madrid');
    await Promise.all([pg.waitForURL(/nucleo=madrid/), pg.getByRole('button', { name: 'Filtrar' }).click()]);
    expect((await pg.locator('h1').textContent()).includes('Madrid'), 'h1 sin Madrid');
  });

  await run('retrasos: trenes y resumen por línea', mob, async (pg) => {
    await pg.goto(BASE + '/retrasos?vista=lineas', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('text=Cobertura', { timeout: 10000 });
    await pg.goto(BASE + '/retrasos?nucleo=madrid', { waitUntil: 'domcontentloaded' });
    expect((await pg.locator('h1').textContent()).includes('Madrid'), 'filtro núcleo');
  });

  await run('navegación principal', mob, async (pg) => {
    await pg.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    if (mob) {
      expect(!(await pg.locator('#main-nav').isVisible()), 'menú abierto por defecto en móvil');
      await pg.locator('#menu-toggle').click();
    }
    for (const l of ['Horarios', 'Estaciones', 'Líneas', 'Núcleos', 'Incidencias', 'Retrasos', 'Favoritos']) {
      expect(await pg.locator('#main-nav a', { hasText: l }).isVisible(), 'falta ' + l);
    }
    await Promise.all([pg.waitForURL(/\/nucleos$/), pg.locator('#main-nav a', { hasText: 'Núcleos' }).click()]);
    expect(await pg.locator('#main-nav a[aria-current=page]').textContent() === 'Núcleos', 'aria-current');
  });

  await run('favoritos y avisos sin JS roto', mob, async (pg) => {
    await pg.goto(BASE + '/favoritos', { waitUntil: 'networkidle' });
    await pg.waitForSelector('#avisos', { timeout: 8000 });
  });

  await run('mapa: trazados validados y estaciones', mob, async (pg) => {
    await pg.goto(BASE + '/mapa/sevilla', { waitUntil: 'networkidle' });
    await pg.waitForSelector('.leaflet-container canvas', { timeout: 15000 });
    await pg.waitForSelector('.legend', { timeout: 10000 });
    expect(/trenes con posición/.test(await pg.locator('.legend').textContent()), 'sin leyenda');
    expect(!(await pg.locator('.map + .err').count()), 'muestra error de consulta');
    expect(await pg.locator('.leaflet-control-attribution', { hasText: 'OpenStreetMap' }).count(), 'sin atribución');
    await pg.waitForSelector('.legend', { timeout: 5000 });
  });

  await run('sin scroll horizontal', mob, async (pg) => {
    for (const u of ['/', '/lineas/madrid/c4', '/incidencias', `/trayecto?from=cer:17000&to=cer:18000`, '/retrasos?vista=lineas', '/mapa/madrid']) {
      await pg.goto(BASE + u, { waitUntil: 'domcontentloaded' });
      const over = await pg.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(over <= 2, `${u}: desborda ${over}px`);
    }
  });
}
await b.close();
console.log(`\n${oks} OK · ${fails} FAIL`);
process.exit(fails ? 1 : 0);
