// QA cross-browser / cross-device (v0.3.6): chromium, firefox, webkit x
// desktop 1366x900 | tablet 820x1180 (touch) | mobile (Pixel 7, iPhone 13,
// o 390x844 + UA móvil en firefox). Locale es-ES, zona Europe/Madrid.
//   BASE=https://trenes.h1756.es SHOTS=/f/Temp/trenes-audit/xb-prod node xbrowser.mjs
//   node xbrowser.mjs --selftest      (valida los detectores con fixtures/bad.html y good.html)
// Salida: una línea por motor/perfil/página, tabla resumen, exit 1 si hay FAIL.
//
// Detectores (auditInPage):
//   desborda  : texto que se sale sin elipsis ni scroll propio (scrollWidth > clientWidth+2)
//   solapa    : hermanos de un contenedor fila (flex/grid o clase *row*) con cajas que se cruzan >2px
//   alinea    : filas repetidas (>=3 hermanos con misma etiqueta+clase, apilados en vertical sin
//               compartir línea; cada fila con >=2 hijos en la misma banda vertical, o sea
//               forma de tabla; excluidos contenedores flex con wrap o en fila, grid de varias
//               columnas, li en bloque, y hijos inline dentro de prosa) cuya posición i tiene
//               left distinto >4px entre filas
//               (desktop/tablet: todos los hijos; móvil: solo el primero)
//   trunca    : texto con text-overflow:ellipsis realmente recortado y con <10 caracteres visibles
//               o ancho <80px (estimación caracteres = len * clientWidth / scrollWidth). Solo FAIL en móvil.
//
// Exclusiones (falsos positivos conocidos):
//   - .sr-only y elementos dentro de ella
//   - display:contents / display:inline (no tienen caja: clientWidth=0)
//   - elementos ocultos (display:none / visibility:hidden en la cadena)
//   - mapas Leaflet (cualquier clase con "leaflet")
//   - hijos absolute/fixed (decoraciones) en solape y alineación
//   - desbordes dentro de un ancestro con overflow-x auto/scroll (scroll intencional)
//   - filas con flex-wrap distinto de nowrap (los hijos pueden envolverse a propósito)
//   - truncado en desktop/tablet (solo informativo; no se reporta)
//   - mensajes de consola de tile.openstreetmap.org y favicon
import { chromium, firefox, webkit, devices } from 'playwright';
import { mkdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = (process.env.BASE || 'https://trenes.h1756.es').replace(/\/$/, '');
const SHOTS = process.env.SHOTS || '';
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const ENGINES = { chromium, firefox, webkit };
const PROFILES = ['desktop', 'tablet', 'mobile'];
const PAGES = process.env.PAGES ? JSON.parse(process.env.PAGES) : [
  '/', '/horarios', '/lineas', '/lineas?q=C1', '/nucleos', '/nucleos/madrid',
  '/lineas/madrid/c4', '/incidencias', '/retrasos', '/retrasos?vista=lineas',
  '/estacion/cer:18000,ld:18000', '/trayecto?from=cer:17000&to=cer:18000',
  '/mapa/madrid', '/estadisticas', '/favoritos', '/estado', '/fuentes',
];
const NAV_TIMEOUT = 20000;
const IGNORE_CONSOLE = /tile\.openstreetmap\.org|favicon/i;
const MOBILE_UA_FF = 'Mozilla/5.0 (Android 14; Mobile; rv:128.0) Gecko/128.0 Firefox/128.0';
const LABELS = {
  desborda: 'desbordamiento',
  solapa: 'solape de cajas',
  alinea: 'columnas desalineadas',
  trunca: 'texto truncado',
};

function contextOptions(engine, profile) {
  const common = { locale: 'es-ES', timezoneId: 'Europe/Madrid', ignoreHTTPSErrors: true };
  if (profile === 'desktop') return { ...common, viewport: { width: 1366, height: 900 } };
  if (profile === 'tablet') return { ...common, viewport: { width: 820, height: 1180 }, hasTouch: true };
  if (engine === 'chromium') { const { defaultBrowserType, ...d } = devices['Pixel 7']; return { ...common, ...d }; }
  if (engine === 'webkit') { const { defaultBrowserType, ...d } = devices['iPhone 13']; return { ...common, ...d }; }
  // firefox: sin isMobile; viewport móvil + UA móvil
  return { ...common, viewport: { width: 390, height: 844 }, hasTouch: false, userAgent: MOBILE_UA_FF };
}

// Se inyecta con page.evaluate(auditInPage, profile): debe ser autocontenida.
// Devuelve { desborda, solapa, alinea, trunca } = { total, items (máx. 5) }.
function auditInPage(profile) {
  const SEL = 'a, button, td, th, .badge, .lb, [class*=row], li';
  const MAX = 5;
  const isMobile = profile === 'mobile';
  const inLeaflet = (el) => !!el.closest('[class*="leaflet"]');
  const srOnly = (el) => !!el.closest('.sr-only');
  const hiddenEl = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') return true;
    }
    return false;
  };
  const scrollAncestor = (el) => {
    for (let e = el.parentElement; e; e = e.parentElement) {
      const ox = getComputedStyle(e).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
    }
    return false;
  };
  const pathOf = (el) => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && e !== document.body && parts.length < 4; e = e.parentElement) {
      let p = e.tagName.toLowerCase();
      if (e.id) p += '#' + e.id;
      else if (typeof e.className === 'string' && e.className.trim()) p += '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.');
      parts.unshift(p);
    }
    return parts.join(' > ');
  };
  const snip = (el) => (el.innerText || el.getAttribute('aria-label') || el.textContent || '')
    .trim().replace(/\s+/g, ' ').slice(0, 60);
  const visBox = (k) => {
    const cs = getComputedStyle(k);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.display === 'contents') return false;
    if (srOnly(k) || hiddenEl(k) || inLeaflet(k)) return false;
    const r = k.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const cats = { desborda: new Set(), solapa: new Set(), alinea: new Set(), trunca: new Set() };

  // desborda: contenido que se desborda sin elipsis ni scroll propio
  for (const el of document.querySelectorAll(SEL)) {
    if (inLeaflet(el) || srOnly(el) || hiddenEl(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'contents' || cs.display === 'inline') continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (el.scrollWidth > el.clientWidth + 2 && cs.textOverflow !== 'ellipsis'
        && cs.overflowX === 'visible' && !scrollAncestor(el)) {
      cats.desborda.add(`${pathOf(el)} "${snip(el)}"`);
    }
  }

  // solapa: hermanos de un contenedor fila con cajas que se cruzan >2px
  for (const p of document.querySelectorAll('body *')) {
    const pcs = getComputedStyle(p);
    const cls = typeof p.className === 'string' ? p.className : '';
    const isRow = /flex|grid/.test(pcs.display) || /(^|[-_\s])row/i.test(cls);
    if (!isRow || inLeaflet(p) || srOnly(p) || hiddenEl(p)) continue;
    const kids = [...p.children].filter(visBox)
      .filter((k) => { const c = getComputedStyle(k); return c.position !== 'absolute' && c.position !== 'fixed'; });
    for (let i = 0; i < kids.length; i++) {
      for (let j = i + 1; j < kids.length; j++) {
        const a = kids[i].getBoundingClientRect();
        const b = kids[j].getBoundingClientRect();
        const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const iy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (ix > 2 && iy > 2) cats.solapa.add(`${pathOf(kids[j])} "${snip(kids[j])}" con "${snip(kids[i])}"`);
      }
    }
  }

  // alinea: filas repetidas (>=3 hermanos con misma etiqueta+clase) con columnas desalineadas
  for (const P of document.querySelectorAll('body *')) {
    if (inLeaflet(P) || srOnly(P) || hiddenEl(P)) continue;
    const groups = new Map();
    for (const k of P.children) {
      const key = k.tagName.toLowerCase() + '.' + (typeof k.className === 'string' ? k.className.trim() : '');
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(k);
    }
    for (const [key, all] of groups) {
      const rows = all.filter(visBox);
      if (rows.length < 3) continue;
      if (rows.some((r) => getComputedStyle(r).flexWrap !== 'nowrap')) continue;
      // contenedor con elementos envueltos o dispuestos en fila (chips, botones): no son filas
      const pcs = getComputedStyle(P);
      if (/flex/.test(pcs.display) && (pcs.flexWrap !== 'nowrap' || /^row/.test(pcs.flexDirection))) continue;
      if (/grid/.test(pcs.display) && pcs.gridTemplateColumns.trim().split(/\s+/).length > 1) continue;
      // solo filas apiladas en vertical: hermanos que comparten línea (celdas, tarjetas
      // en rejilla, chips) son columnas de una misma fila, no filas repetidas
      const byTop = rows.slice().sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
      let stacked = true;
      for (let i = 1; i < byTop.length; i++) {
        if (byTop[i].getBoundingClientRect().top < byTop[i - 1].getBoundingClientRect().bottom - 2) { stacked = false; break; }
      }
      if (!stacked) continue;
      // cada fila debe parecer una fila de tabla: >=2 hijos que comparten banda vertical.
      // Contenedores de bloque con hijos apilados (título + párrafos) no cuentan.
      const tableLike = (r) => {
        const ks = [...r.children].filter(visBox);
        if (ks.length < 2) return false;
        for (let i = 0; i < ks.length; i++) {
          for (let j = i + 1; j < ks.length; j++) {
            const a = ks[i].getBoundingClientRect(), b = ks[j].getBoundingClientRect();
            if (Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2) return true;
          }
        }
        return false;
      };
      if (!rows.every(tableLike)) continue;
      // li en bloque (prosa) no son filas; solo li grid/flex
      if (key.startsWith('li.') && !/grid|flex/.test(getComputedStyle(rows[0]).display)) continue;
      const INLINE = new Set(['strong', 'em', 'b', 'i', 'code', 'a', 'span']);
      const hasDirectText = (r) => [...r.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      const maxChildren = Math.max(...rows.map((r) => r.children.length));
      const limit = isMobile ? Math.min(1, maxChildren) : maxChildren;
      let spread = 0, idx = -1;
      for (let i = 0; i < limit; i++) {
        // prosa: un hijo inline (strong, code, a…) dentro de texto corrido no es una columna
        const cells = rows.map((r) => r.children[i]);
        if (cells.some((c, n) => !c || !visBox(c) || (INLINE.has(c.tagName.toLowerCase()) && hasDirectText(rows[n])))) continue;
        const xs = cells.map((c) => c.getBoundingClientRect().left);
        const s = Math.max(...xs) - Math.min(...xs);
        if (s > spread) { spread = s; idx = i; }
      }
      if (spread > 4) {
        cats.alinea.add(`${pathOf(P)} > ${key}×${rows.length}: spread ${spread.toFixed(0)}px (hijo ${idx})`);
      }
    }
  }

  // trunca: ellipsis realmente recortado y poco texto visible
  for (const el of document.querySelectorAll('body *')) {
    if (getComputedStyle(el).textOverflow !== 'ellipsis') continue;
    if (inLeaflet(el) || srOnly(el) || hiddenEl(el)) continue;
    if (!el.clientWidth || el.scrollWidth <= el.clientWidth + 1) continue;
    const text = (el.textContent || '').trim();
    const vis = Math.floor(text.length * el.clientWidth / el.scrollWidth);
    if (el.clientWidth < 80 || vis < 10) {
      cats.trunca.add(`${pathOf(el)} "${snip(el)}" (~${vis} car. visibles, ${el.clientWidth}px)`);
    }
  }

  const out = {};
  for (const k of Object.keys(cats)) {
    const arr = [...cats[k]];
    out[k] = { total: arr.length, items: arr.slice(0, MAX) };
  }
  return out;
}

function attachListeners(pg) {
  const st = { pageErrors: [], consoleErrs: [] };
  pg.on('pageerror', (e) => st.pageErrors.push(e.message.split('\n')[0]));
  pg.on('console', (m) => {
    if (m.type() !== 'error') return;
    const text = m.text();
    const loc = m.location()?.url || '';
    if (IGNORE_CONSOLE.test(text) || IGNORE_CONSOLE.test(loc)) return;
    st.consoleErrs.push(text + (loc ? ` @${loc}` : ''));
  });
  return st;
}

// Convierte el resultado de auditInPage en problemas (trunca solo cuenta en móvil)
function auditProblems(audit, profile) {
  const problems = [];
  for (const [k, v] of Object.entries(audit)) {
    if (!v.total) continue;
    if (k === 'trunca' && profile !== 'mobile') continue;
    problems.push(`${LABELS[k]} x${v.total}: ${v.items.join(' | ')}`);
  }
  return problems;
}

async function checkPage(pg, st, path, shotPath, profile) {
  const problems = [];
  st.pageErrors.length = 0;
  st.consoleErrs.length = 0;
  try {
    try {
      const resp = await pg.goto(BASE + path, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });
      const status = resp ? resp.status() : null;
      if (status !== 200) problems.push(`HTTP ${status}`);
    } catch (e) {
      return ['navegación: ' + e.message.split('\n')[0]];
    }
    await pg.waitForLoadState('load', { timeout: 10000 }).catch(() => {});
    try {
      await pg.locator('h1').first().waitFor({ state: 'visible', timeout: 10000 });
    } catch {
      problems.push('h1 no visible');
    }
    await pg.waitForTimeout(300);
    if (st.pageErrors.length) problems.push(`pageerror: ${st.pageErrors[0]}${st.pageErrors.length > 1 ? ` (+${st.pageErrors.length - 1})` : ''}`);
    if (st.consoleErrs.length) problems.push(`console.error: ${st.consoleErrs[0]}${st.consoleErrs.length > 1 ? ` (+${st.consoleErrs.length - 1})` : ''}`);
    const overflow = await pg.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 2) problems.push(`desbordamiento horizontal de página +${overflow}px`);
    const audit = await pg.evaluate(auditInPage, profile);
    problems.push(...auditProblems(audit, profile));
  } finally {
    if (shotPath) await pg.screenshot({ path: shotPath, fullPage: true }).catch(() => {});
  }
  return problems;
}

async function interaction(pg, profile) {
  await pg.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });
  const from = pg.locator('#from');
  await from.click();
  await from.pressSequentially('atocha', { delay: 40 });
  await pg.locator('#from-list li[role=option]').first().waitFor({ timeout: 15000 });
  await pg.locator('#from-list li[role=option]').first().click();
  const to = pg.locator('#to');
  await to.click();
  await to.pressSequentially('chamart', { delay: 40 });
  await pg.locator('#to-list li[role=option]').first().waitFor({ timeout: 15000 });
  await pg.locator('#to-list li[role=option]').first().click();
  await pg.getByRole('radio', { name: 'Mañana' }).click();
  await Promise.all([
    pg.waitForURL((u) => u.toString().includes('/trayecto') && u.toString().includes('date='), { timeout: 20000 }),
    pg.getByRole('button', { name: /^Ver trenes/ }).click(),
  ]);
  if (profile === 'mobile') {
    if (!(await pg.locator('#main-nav').isVisible())) await pg.locator('#menu-toggle').click();
    await pg.locator('#main-nav').waitFor({ state: 'visible', timeout: 5000 });
    const links = pg.locator('#main-nav a');
    const n = await links.count();
    if (!n) throw new Error('menú móvil sin enlaces');
    for (let i = 0; i < n; i++) {
      if (!(await links.nth(i).isVisible())) throw new Error(`enlace de menú oculto: ${(await links.nth(i).textContent()).trim()}`);
    }
  }
}

// --selftest: valida los detectores con fixtures locales (chromium, file://)
async function selftest() {
  const dir = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
  const cases = [
    // [fixture, profile, detectores que DEBEN disparar; el resto no se exige]
    ['bad.html', 'desktop', ['desborda', 'solapa', 'alinea']],
    ['bad.html', 'mobile', ['desborda', 'solapa', 'trunca']],
    ['good.html', 'desktop', []],
    ['good.html', 'mobile', []],
    // falsos positivos conocidos (chips envueltos, tarjetas en bloque, prosa con <strong>): 0 en ambos
    ['fp.html', 'desktop', []],
    ['fp.html', 'mobile', []],
  ];
  const b = await chromium.launch();
  let fails = 0;
  for (const [file, profile, expected] of cases) {
    const pg = await b.newPage({ viewport: profile === 'mobile' ? { width: 390, height: 844 } : { width: 1366, height: 900 } });
    await pg.goto(pathToFileURL(join(dir, file)).href, { waitUntil: 'load' });
    const audit = await pg.evaluate(auditInPage, profile);
    const counts = Object.entries(audit).map(([k, v]) => `${k}=${v.total}`).join(' ');
    const errs = [];
    if (file.startsWith('bad')) {
      for (const k of expected) if (!audit[k].total) errs.push(`no disparó ${k}`);
    } else {
      for (const k of Object.keys(audit)) {
        // good: ningún detector debe disparar (trunca solo cuenta en móvil, pero aquí se exige 0 en ambos)
        if (audit[k].total) errs.push(`falso positivo ${k}: ${audit[k].items[0]}`);
      }
    }
    const ok = errs.length === 0;
    if (!ok) fails++;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${file} ${profile} — ${counts}${ok ? '' : ' — ' + errs.join(' ; ')}`);
    for (const k of Object.keys(audit)) {
      for (const item of audit[k].items) console.log(`       ${k}: ${item}`);
    }
    await pg.close();
  }
  await b.close();
  console.log(fails ? `SELFTEST FALLÓ (${fails})` : 'SELFTEST OK');
  return fails ? 1 : 0;
}

if (process.argv.includes('--selftest')) {
  process.exit(await selftest());
}

const results = [];
function record(engine, profile, page, problems) {
  const ok = problems.length === 0;
  results.push({ engine, profile, page, ok });
  const reason = ok ? 'ok' : problems.join(' ; ');
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${engine} ${profile} ${page} — ${reason}`);
}

for (const [engine, type] of Object.entries(ENGINES)) {
  let browser;
  try {
    browser = await type.launch();
  } catch (e) {
    for (const profile of PROFILES) for (const p of [...PAGES, 'interaccion'])
      record(engine, profile, p, ['no se pudo lanzar el motor: ' + e.message.split('\n')[0]]);
    continue;
  }
  for (const profile of PROFILES) {
    let ctx;
    try {
      ctx = await browser.newContext(contextOptions(engine, profile));
    } catch (e) {
      for (const p of [...PAGES, 'interaccion']) record(engine, profile, p, ['contexto: ' + e.message.split('\n')[0]]);
      continue;
    }
    const pg = await ctx.newPage();
    const st = attachListeners(pg);
    for (const path of PAGES) {
      const slug = path.replace(/^\//, '').replace(/[^\w-]+/g, '_') || 'home';
      const shot = SHOTS ? `${SHOTS}/${engine}-${profile}-${slug}.png` : '';
      const problems = await checkPage(pg, st, path, shot, profile);
      record(engine, profile, path, problems);
    }
    if (profile !== 'tablet') {
      st.pageErrors.length = 0;
      st.consoleErrs.length = 0;
      try {
        await interaction(pg, profile);
        if (st.pageErrors.length) throw new Error('pageerror: ' + st.pageErrors[0]);
        record(engine, profile, 'interaccion (portada→trayecto)', []);
      } catch (e) {
        record(engine, profile, 'interaccion (portada→trayecto)', [e.message.split('\n')[0]]);
      }
    }
    await ctx.close();
  }
  await browser.close();
}

// Tabla resumen
console.log('\nResumen (páginas + interacción por motor/perfil):');
console.log('engine        profile   OK   FAIL  total');
const pad = (s, n) => String(s).padEnd(n);
let anyFail = false;
for (const engine of Object.keys(ENGINES)) {
  for (const profile of PROFILES) {
    const rs = results.filter((r) => r.engine === engine && r.profile === profile);
    const ok = rs.filter((r) => r.ok).length;
    const fail = rs.length - ok;
    if (fail) anyFail = true;
    console.log(`${pad(engine, 13)} ${pad(profile, 9)} ${pad(ok, 4)} ${pad(fail, 5)} ${rs.length}`);
  }
}
const totalFail = results.filter((r) => !r.ok).length;
console.log(`\nTotal: ${results.length - totalFail} OK, ${totalFail} FAIL`);
process.exit(anyFail ? 1 : 0);
