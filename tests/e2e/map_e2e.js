// Uji peta (Milestone 3): kontrol lapisan, legend dinamis, zona bahaya resmi vs perkiraan, 3D, kepadatan penduduk,
// grid waktu evakuasi, kapasitas shelter, dan definisi tier yang sama di ketiga peran. Data SINTETIS (SEMPAT_FAKE_OSM).
//   node tests/e2e/map_e2e.js     (tangkapan layar: tests/e2e/out/map_*.png)
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');
const PORT = process.env.PORT || '8767';
const BASE = `http://localhost:${PORT}`;
const ROOT = path.resolve(__dirname, '..', '..');
const OUT = process.env.OUT || path.join(__dirname, 'out');
fs.mkdirSync(OUT, { recursive: true });
const errors = [];
const log = (...a) => console.log('•', ...a);
const ok = (c, m) => { if (!c) throw new Error('GAGAL: ' + m); log('OK', m); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const IGNORE = /Failed to load resource|ERR_INTERNET_DISCONNECTED|net::ERR|EventSource|cartocdn|openstreetmap|AbortError|401|permission denied/i;
let SERVER;
const shot = (p, n) => p.screenshot({ path: path.join(OUT, `map_${n}.png`) });

async function login(page, u, pw) {
  await page.fill('input[name=u]', u); await page.fill('input[name=p]', pw); await page.click('#loginf button[type=submit]');
}
async function layerVis(page, obj, id) { return page.evaluate(([o, i]) => window[o].m.map.getLayoutProperty(i, 'visibility'), [obj, id]); }
async function toggle(page, key, on) {
  if (await page.isHidden('.lyr-panel')) await page.click('.lyr-btn');
  const box = page.locator(`input[data-lyr=${key}]`);
  if (on) await box.check(); else await box.uncheck();
  await page.waitForTimeout(300);
}

(async () => {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'sempat-map-'));
  SERVER = spawn(process.env.PYTHON || 'python3', ['run.py', '--no-browser', '--port', PORT], { cwd: ROOT, stdio: 'ignore',
    env: { ...process.env, SEMPAT_FAKE_OSM: '1', SEMPAT_BMKG_POLL: '0', SEMPAT_DATA_DIR: data } });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + '/api/meta')).ok) break; } catch { /* */ } await sleep(500); }
  const browser = await chromium.launch();
  const watch = (page, tag) => { page.on('pageerror', (e) => errors.push(`[${tag}] ${e.message}`)); page.on('console', (m) => { if (m.type() === 'error') errors.push(`[${tag}] ${m.text()}`); }); };

  // ---------------- petugas ----------------
  const pctx = await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: 'id-ID' });
  const p = await pctx.newPage(); watch(p, 'petugas');
  await p.goto(BASE + '/petugas'); await login(p, 'petugas', 'petugas123');
  await p.waitForFunction(() => window.__petugas && window.__petugas.m && window.__petugas.area && window.__petugas.sync.tables, null, { timeout: 30000 });
  await p.waitForTimeout(800);
  ok(await p.isVisible('.lyr-btn'), 'petugas: tombol "Lapisan" ada di peta');
  await p.click('.lyr-btn');
  const groups = await p.$$eval('input[data-lyr]', (xs) => xs.map((x) => x.dataset.lyr));
  ok(['bangunan', 'kelayakan', 'bangunan3d', 'bahaya', 'jalan', 'shelter', 'penduduk'].every((k) => groups.includes(k)), `petugas: kelompok lapisan ${groups.join(',')}`);
  const lg = await p.textContent('.lyr-legend');
  ok(lg.includes('Terverifikasi') && lg.includes('Perlu Verifikasi') && lg.includes('Tidak Aman'), 'petugas: legend tier (3 tingkat) tampil');
  ok(/PERKIRAAN aturan.*BUKAN peta resmi/.test(lg), 'petugas: zona bahaya perkiraan berlabel "bukan peta resmi"');
  const hzr = await p.evaluate(() => window.__petugas.m.map.querySourceFeatures('hzr').length);
  ok(hzr > 0, `petugas: zona bahaya aturan digambar sebagai area (${hzr} sel/run)`);
  ok(await p.isDisabled('input[data-lyr=penduduk]'), 'petugas: kepadatan penduduk nonaktif tanpa grid (model 4/rumah tidak dipetakan)');
  ok((await p.textContent('.lyr-panel')).includes('TIDAK dipetakan'), 'petugas: alasan lapisan penduduk tidak tersedia ditampilkan');
  await toggle(p, 'jalan', false);
  ok(await layerVis(p, '__petugas', 'roads') === 'none' && !(await p.textContent('.lyr-legend')).includes('Jalan putus'), 'petugas: lapisan jalan dimatikan -> layer & legend hilang');
  await toggle(p, 'jalan', true);
  ok(await layerVis(p, '__petugas', 'roads') === 'visible', 'petugas: lapisan jalan dinyalakan lagi');
  // 3D di zoom dekat
  const tall = await p.evaluate(() => window.__petugas.area.buildings.find((b) => b.levels >= 2 && b.levels_src !== 'default'));
  await p.evaluate((b) => window.__petugas.m.map.jumpTo({ center: [b.lon, b.lat], zoom: 17.2 }), tall);
  await toggle(p, 'bangunan3d', true);
  await p.waitForTimeout(900);
  ok(await layerVis(p, '__petugas', 'bld-3d') === 'visible', 'petugas: bangunan 3D aktif (fill-extrusion)');
  ok(await p.evaluate(() => window.__petugas.m.map.getPitch()) > 20, 'petugas: kamera dimiringkan saat 3D');
  const n3d = await p.evaluate(() => window.__petugas.m.map.queryRenderedFeatures({ layers: ['bld-3d', 'bld-3d-est'] }).length);
  ok(n3d > 0, `petugas: gedung bertingkat diekstrusi di zoom 17 (${n3d} fitur)`);
  const flat = await p.evaluate(() => window.__petugas.m.map.queryRenderedFeatures({ layers: ['bld-3d', 'bld-3d-est'] }).every((f) => f.properties.known === 1));
  ok(flat, 'petugas: bangunan tanpa data lantai TIDAK diekstrusi');
  ok((await p.textContent('.lyr-legend')).includes('PERKIRAAN'), 'petugas: legend 3D membedakan tinggi perkiraan');
  await shot(p, 'petugas_3d');
  // detail gedung saat diklik
  await p.evaluate(() => window.__petugas.m.map.easeTo({ pitch: 0, duration: 0 }));
  await toggle(p, 'bangunan3d', false);
  await p.evaluate((b) => window.__petugas.m.map.jumpTo({ center: [b.lon, b.lat], zoom: 18 }), tall);
  await p.waitForTimeout(600);
  const pt = await p.evaluate((b) => { const q = window.__petugas.m.map.project([b.lon, b.lat]); return { x: q.x, y: q.y }; }, tall);
  const box = await p.locator('#map canvas').boundingBox();
  await p.mouse.click(box.x + pt.x, box.y + pt.y);
  await p.waitForSelector('.maplibregl-popup .bld-pop', { timeout: 5000 });
  const pop = await p.textContent('.maplibregl-popup .bld-pop');
  ok(/Terverifikasi|Perlu Verifikasi|Tidak Aman/.test(pop) && pop.includes('lantai'), 'petugas: klik gedung -> detail status tier & data lantai');
  await shot(p, 'petugas_detail');
  // kapasitas shelter tersedia bila ada tujuan berkapasitas
  ok(await p.evaluate(() => Array.isArray(window.__petugas.m.ctx.cap)), 'petugas: data kapasitas shelter diteruskan ke peta');

  // ---------------- perencana ----------------
  const rctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'id-ID' });
  const r = await rctx.newPage(); watch(r, 'perencana');
  await r.goto(BASE + '/perencana'); await login(r, 'perencana', 'perencana123');
  await r.waitForFunction(() => window.__perencana && window.__perencana.plan && window.__perencana.m, null, { timeout: 30000 });
  await r.waitForFunction(() => window.__perencana.m.ctx.tgrid && window.__perencana.m.ctx.tgrid.runs, null, { timeout: 15000 });
  await toggle(r, 'waktu', true);
  ok(await layerVis(r, '__perencana', 'tgrid') === 'visible', 'perencana: grid waktu evakuasi aktif');
  const tl = await r.textContent('.lyr-legend');
  ok(tl.includes('0–3 menit') && tl.includes('resolusi bukan akurasi'), 'perencana: legend waktu 3 menit + keterbatasan metode');
  const tg = await r.evaluate(() => window.__perencana.m.ctx.tgrid);
  ok(tg.cell_m <= 30.5 && tg.band_s === 180, `perencana: sel ${tg.cell_m} m, pita ${tg.band_s / 60} menit`);
  await shot(r, 'perencana_waktu');
  // unggah grid penduduk SINTETIS -> lapisan kepadatan tersedia & berlabel SINTETIS
  const tok = (await (await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'perencana', password: 'perencana123' }) })).json()).token;
  const ar = await r.evaluate(() => window.__perencana.area);
  const rows = ar.buildings.filter((b) => b.residents > 0).map((b) => `${(Math.round(b.lon * 3600) / 3600).toFixed(6)},${(Math.round(b.lat * 3600) / 3600).toFixed(6)},6`);
  const up = await fetch(`${BASE}/api/population?gid=uji-peta&name=Grid%20uji&synthetic=true&license=-`, { method: 'POST', headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'text/csv' },
    body: 'longitude,latitude,population\n' + rows.join('\n') });
  ok(up.status === 201, 'perencana: grid penduduk sintetis diunggah');
  await r.reload();
  await r.waitForFunction(() => window.__perencana && window.__perencana.m && window.__perencana.m.ctx.pop && window.__perencana.m.ctx.pop.count > 0, null, { timeout: 20000 });
  await r.waitForTimeout(500);
  ok(!(await r.isDisabled('input[data-lyr=penduduk]')), 'perencana: lapisan kepadatan tersedia setelah ada grid');
  ok((await r.textContent('.lyr-legend')).includes('SINTETIS'), 'perencana: kepadatan dari grid sintetis berlabel SINTETIS');
  ok((await r.textContent('#dataBody')).includes('SINTETIS'), 'perencana: kartu data menyebut sumber penduduk sintetis');
  await shot(r, 'perencana_penduduk');

  // ---------------- warga ----------------
  const wctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: 'id-ID',
    geolocation: { latitude: -0.9420, longitude: 100.3620, accuracy: 12 }, permissions: ['geolocation'] });
  const w = await wctx.newPage(); watch(w, 'warga');
  await w.goto(BASE + '/');
  await w.waitForFunction(() => window.__sempat && window.__sempat.S.area && window.__sempat.S.tables, null, { timeout: 30000 });
  await w.waitForTimeout(600);
  ok(!(await w.textContent('.lyr-legend')).includes('Zona bahaya'), 'warga (HP): legend terlipat secara bawaan agar peta tidak tertutup');
  await w.click('.lg-toggle');
  const wl = await w.textContent('.lyr-legend');
  ok(wl.includes('Zona bahaya') && !wl.includes('Kepadatan'), 'warga: legend sederhana (zona bahaya, rute, jalan, penanda)');
  await w.click('.lyr-btn');
  const wg = await w.$$eval('input[data-lyr]', (xs) => xs.map((x) => x.dataset.lyr));
  ok(!wg.includes('penduduk') && !wg.includes('waktu'), 'warga: tidak ada lapisan analitis perencana');
  const pubTier = await w.evaluate(() => window.__sempat.S.area.buildings.every((b) => ['verified', 'unverified', 'unsafe'].includes(b.tier)));
  ok(pubTier, 'warga: setiap gedung membawa tier dari server (definisi sama)');
  const same = await w.evaluate(async () => {
    const pub = window.__sempat.S.area.buildings;
    return pub.slice(0, 20).map((b) => [b.id, b.tier]);
  });
  const staff = await p.evaluate(() => Object.fromEntries(window.__petugas.area.buildings.map((b) => [b.id, b.tier])));
  ok(same.every(([id, t]) => !(id in staff) || staff[id] === t), 'warga = petugas: tier gedung yang sama di kedua peran');
  await shot(w, 'warga');

  // ---------------- pembaruan inkremental (M4) ----------------
  // Survei satu gedung -> klien lain di area yang sama menambal gedung itu saja (tanpa unduh ulang area & tanpa
  // bangun ulang GeoJSON). Warga memakai peta otomatis (area live), petugas & perencana memakai desa sintetis.
  const S_ = (k) => (k === '__sempat' ? 'window.__sempat.S' : `window.${k}`);
  const stats = (pg, o) => pg.evaluate((e) => { const S = eval(e); return { sync: { ...S.sync.patchStats }, map: S.m.renderStats(), area: S.area.meta.id }; }, S_(o));
  const ptok = (await (await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'petugas', password: 'petugas123' }) })).json()).token;
  const survey = async (aid, b, lv) => (await fetch(`${BASE}/api/buildings/${encodeURIComponent(b.id)}`, { method: 'PUT', headers: { Authorization: 'Bearer ' + ptok, 'Content-Type': 'application/json' },
    body: JSON.stringify({ area_id: aid, levels: lv, levels_confirmed: true, structure: 'beton', access: '24jam', condition: 'baik' }) })).status;
  const patched = (pg, o, id, lv) => pg.waitForFunction(([e, i, l]) => { const S = eval(e); const b = S.area.buildings.find((x) => x.id === i); return b && b.levels === l && b.surveyed; }, [S_(o), id, lv], { timeout: 15000 });
  const groupsInc = [[[w, '__sempat', 'warga']], [[p, '__petugas', 'petugas'], [r, '__perencana', 'perencana']]];
  for (const grp of groupsInc) {
    const [pg0, o0] = grp[0];
    const st0 = await Promise.all(grp.map(([pg, o]) => stats(pg, o)));
    const aid = st0[0].area;
    ok(st0.every((x) => x.area === aid), `tambalan: klien ${grp.map((g) => g[2]).join(' & ')} di area ${aid}`);
    const target = await pg0.evaluate((e) => eval(e).area.buildings.find((b) => (b.levels || 1) >= 2 && !b.surveyed), S_(o0));
    ok(await survey(aid, target, 5) === 200, `survei gedung ${target.id} (${aid}) lewat API`);
    for (const [pg, o] of grp) await patched(pg, o, target.id, 5);
    for (let k = 0; k < grp.length; k++) {
      const [pg, o, name] = grp[k];
      const a = await stats(pg, o); const b = st0[k];
      ok(a.sync.patches > b.sync.patches && a.sync.full === b.sync.full,
        `${name}: survei diterapkan sebagai tambalan (${a.sync.bytes} B; area tidak diunduh ulang)`);
      ok(a.map.patch > b.map.patch && a.map.full === b.map.full, `${name}: peta memperbarui fitur gedung itu saja, GeoJSON tidak dibangun ulang`);
    }
  }

  // ---------------- Paket bukti Tsunami Ready (M4) ----------------
  const evHref = await r.getAttribute('#evLink', 'href');
  ok(/\/perencana\/bukti\?area=desa-sintetis&eta=\d+/.test(evHref), `perencana: tautan paket bukti membawa area & skenario (${evHref})`);
  const ev = await rctx.newPage(); watch(ev, 'bukti');
  await ev.goto(BASE + evHref);
  await ev.waitForFunction(() => window.__bukti && window.__bukti.ready, null, { timeout: 30000 });
  const evText = await ev.textContent('#doc');
  ok(evText.includes('BUKAN penilaian') && evText.includes('bukan sistem peringatan dini resmi'), 'bukti: penafian (bukan penilaian Tsunami Ready, bukan peringatan resmi)');
  ok(evText.includes('DATA SINTETIS'), 'bukti: area sintetis ditandai jelas');
  const codes = await ev.$$eval('#doc table:first-of-type tbody tr td:first-child strong', (xs) => xs.map((x) => x.textContent));
  ok(codes.length === 12 && codes[0] === 'ASSESS-1' && codes[11] === 'RESP-4', `bukti: 12 indikator UNESCO-IOC (${codes.join(',')})`);
  const st = await ev.$$eval('.st', (xs) => xs.map((x) => x.textContent));
  ok(!st.includes('bukti pendukung tersedia') && st.includes('contoh dari data SINTETIS (bukan bukti)'), 'bukti: data sintetis tidak pernah berstatus "bukti tersedia"');
  ok(st.filter((x) => x === 'di luar cakupan SEMPAT').length >= 5, 'bukti: indikator kegiatan lembaga ditandai di luar cakupan SEMPAT');
  ok(evText.includes('PERKIRAAN aturan') && /grid SINTETIS|PERKIRAAN MODEL/.test(evText), 'bukti: zona bahaya berlabel perkiraan, penduduk berlabel sumbernya (grid sintetis yang diunggah di uji ini)');
  ok(evText.includes('parameter ') && /skema area \d/.test(evText) && evText.includes('dibangun'), 'bukti: snapshot data & versi model (params_sha, skema)');
  ok(await ev.locator('svg.map rect, svg.map path').count() > 10, 'bukti: peta draf SVG tergambar tanpa ubin internet');
  ok(evText.includes('Margin elevasi') && evText.includes('Keterbatasan'), 'bukti: kebijakan margin & keterbatasan dicantumkan');
  const pdfPath = path.join(OUT, 'paket_bukti_tsunami_ready.pdf');
  await ev.emulateMedia({ media: 'print' });
  ok(await ev.isHidden('.toolbar'), 'bukti: toolbar disembunyikan saat dicetak');
  await ev.pdf({ path: pdfPath, format: 'A4', printBackground: true });
  const pdf = fs.readFileSync(pdfPath);
  const pages = (pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g) || []).length;
  ok(pdf.length > 20000 && pages >= 3, `bukti: PDF A4 dibuat dari dialog cetak (${Math.round(pdf.length / 1024)} KB, ${pages} halaman)`);
  await ev.emulateMedia({ media: 'screen' });
  await ev.screenshot({ path: path.join(OUT, 'map_bukti.png'), fullPage: false });

  await browser.close();
  const real = errors.filter((e) => !IGNORE.test(e));
  if (real.length) { console.log('GALAT NYATA:\n' + real.join('\n')); process.exitCode = 1; return; }
  console.log('\nE2E PETA SELESAI');
})().catch((e) => { console.error(e); console.log(errors.filter((x) => !IGNORE.test(x)).join('\n')); process.exitCode = 1; })
  .finally(() => { if (SERVER) SERVER.kill(); process.exit(process.exitCode || 0); });
