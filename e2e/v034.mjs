// QA e2e v0.3.4 (escritorio + móvil): identidad de líneas, incidencias,
// planificador con fechas futuras, navegación. BASE = URL de la web.
//   BASE=http://127.0.0.1:4381 node v034.mjs
import { chromium, firefox, webkit } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE = process.env.BASE || 'https://trenes.h1756.es';
const SHOTS = process.env.SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const madrid = (d = new Date()) => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Madrid', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const TODAY = madrid();
const D1 = (() => { const [y, m, d] = TODAY.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10); })();

const BROWSER = process.env.BROWSER || 'chromium';
const ENGINE = { chromium, firefox, webkit }[BROWSER];
const b = await ENGINE.launch();
console.log('browser:', BROWSER);
// arranque en frío (JIT/perfil del motor): una navegación de calentamiento
// para que el primer test no mida el arranque del navegador
{
  const w = await b.newPage();
  await w.goto(BASE, { waitUntil: 'load', timeout: 60000 }).catch(() => {});
  await w.waitForTimeout(500).catch(() => {});
  await w.context().close();
}
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

  await run('trayecto entre redes: opciones con un transbordo', mob, async (pg) => {
    await pg.goto(`${BASE}/trayecto?from=cer:17000&to=ld:71801&date=${D1}`, { waitUntil: 'networkidle' });
    await pg.waitForSelector('text=Con un transbordo', { timeout: 15000 });
    const first = pg.locator('.xrow').first();
    const txt = await first.textContent();
    expect(/cambio mín\. \d+′/.test(txt), 'sin tiempo mínimo de cambio: ' + txt);
    expect(await pg.locator('.xrow .badge').first().textContent(), 'sin distintivo de margen');
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

  await run('incidencias: tres secciones en orden', mob, async (pg) => {
    await pg.goto(BASE + '/incidencias', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('h2', { timeout: 10000 });
    const heads = await pg.locator('h2').allTextContents();
    // oficiales → inferidas → GTFS-RT (el feed antiguo ya no domina la página)
    const want = ['Avisos oficiales (canales de Renfe)', 'Posibles incidencias inferidas', 'Avisos del feed oficial GTFS-RT'];
    const idx = want.map((w) => heads.findIndex((h) => h.includes(w)));
    expect(idx.every((i) => i >= 0), `faltan encabezados: ${heads.join(' | ')}`);
    expect(idx[0] < idx[1] && idx[1] < idx[2], `orden incorrecto: ${idx.join(',')}`);
  });

  await run('incidencias: filtros de estado y fuente', mob, async (pg) => {
    await pg.goto(BASE + '/incidencias', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('select[name=estado]', { timeout: 10000 });
    await pg.selectOption('select[name=fuente]', 'gtfs');
    await Promise.all([pg.waitForURL(/fuente=gtfs/),
                       pg.getByRole('button', { name: 'Filtrar' }).click()]);
    // solo la sección GTFS queda visible
    expect(await pg.locator('h2', { hasText: 'GTFS-RT' }).count() === 1, 'falta sección GTFS');
    expect(await pg.locator('h2', { hasText: 'canales de Renfe' }).count() === 0,
           'sección de canales visible con fuente=gtfs');
    await pg.goto(BASE + '/incidencias?estado=activa', { waitUntil: 'domcontentloaded' });
    const sel = await pg.locator('select[name=estado]').inputValue();
    expect(sel === 'activa', `estado no persistido en URL/control: ${sel}`);
  });

  await run('incidencias: feed congelado se muestra plegado, no como novedad', mob, async (pg) => {
    await pg.goto(BASE + '/incidencias', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('h2', { timeout: 10000 });
    const frozen = pg.locator('details.frozen');
    if (await frozen.count()) {
      expect(await frozen.count() === 1, `varios bloques .frozen: ${await frozen.count()}`);
      expect(!(await frozen.first().getAttribute('open')), 'feed antiguo desplegado por defecto');
      const sum = await frozen.locator('> summary').textContent();
      expect(/no son novedades de hoy/.test(sum), 'sin aviso de antigüedad: ' + sum);
    }
  });

  await run('incidencias: detalles expandibles por teclado', mob, async (pg) => {
    await pg.goto(BASE + '/incidencias', { waitUntil: 'domcontentloaded' });
    const sum = pg.locator('details.frozen > summary, .notice details.full > summary').first();
    if (await sum.count()) {
      await sum.focus();
      await pg.keyboard.press('Enter');
      expect(await pg.locator('details[open]').count() >= 1, 'Enter no abre el detalle');
    }
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

  await run('líneas: buscar C1 lista ≥3 núcleos', mob, async (pg) => {
    await pg.goto(BASE + '/lineas', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('#linea-q', { timeout: 10000 });
    await pg.locator('#linea-q').fill('C1');
    await pg.waitForTimeout(150);
    const nucs = await pg.locator('section.grp', { has: pg.locator('h2[id="g-C1"]') }).locator('li').count();
    expect(nucs >= 3, `C1 en ${nucs} núcleos (se esperan ≥3)`);
  });

  await run('núcleos: resumen en vivo en cada tarjeta', mob, async (pg) => {
    await pg.goto(BASE + '/nucleos', { waitUntil: 'domcontentloaded' });
    await pg.waitForSelector('section.nuc', { timeout: 10000 });
    const cards = await pg.locator('section.nuc').count();
    const lives = await pg.locator('section.nuc .live').count();
    expect(cards > 0 && lives === cards, `${lives} de ${cards} tarjetas con resumen en vivo`);
  });

  if (mob) await run('retrasos móvil: celdas sin desbordar', true, async (pg) => {
    await pg.goto(BASE + '/retrasos', { waitUntil: 'networkidle' });
    const bad = await pg.evaluate(() => [...document.querySelectorAll('table.board td')]
      .filter((td) => td.scrollWidth > td.clientWidth + 2)
      .map((td) => (td.textContent || '').trim().slice(0, 40)));
    expect(bad.length === 0, `celdas desbordadas: ${bad.slice(0, 3).join(' | ')}`);
  });

  await run('estadísticas: sin errores JS; resultados, gate o validación', mob, async (pg) => {
    await pg.goto(BASE + '/estadisticas', { waitUntil: 'networkidle' });
    await pg.waitForSelector('.validation, .kindcard, .gate, form.filtros', { timeout: 20000 });
    const val = await pg.locator('.validation').count();
    const res = await pg.locator('.kindcard, .gate').count();
    const form = await pg.locator('form.filtros').count();
    expect(val + res + form > 0, 'sin resultados, gate ni estado de validación');
  });

  await run('sin scroll horizontal', mob, async (pg) => {
    for (const u of ['/', '/lineas/madrid/c4', '/incidencias', `/trayecto?from=cer:17000&to=cer:18000`, '/retrasos?vista=lineas', '/mapa/madrid']) {
      // 'load' (no solo DOM): las peticiones de módulos/fetch deben
      // terminar antes de navegar fuera, si no WebKit las reporta como
      // pageerror ("access control checks" al abortar fetch en vuelo)
      await pg.goto(BASE + u, { waitUntil: 'load' });
      await pg.waitForTimeout(300);
      const over = await pg.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(over <= 2, `${u}: desborda ${over}px`);
    }
  });
}
await b.close();
console.log(`\n${oks} OK · ${fails} FAIL`);
process.exit(fails ? 1 : 0);
