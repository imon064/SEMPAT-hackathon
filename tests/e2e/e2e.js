// Uji ujung-ke-ujung SEMPAT dengan GPS "nyata" (geolokasi browser) di lokasi mana pun.
// Skrip ini menyalakan server sendiri dalam mode uji tanpa internet (SEMPAT_FAKE_OSM=1: desa sintetis
// dipindahkan ke lokasi yang diminta) dan mematikannya untuk menguji mode offline.
//   npm i playwright && npx playwright install chromium
//   node tests/e2e/e2e.js
// Tangkapan layar disimpan di tests/e2e/out/.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');
const PORT = process.env.PORT || '8765';
const BASE = `http://localhost:${PORT}`;
const ROOT = path.resolve(__dirname, '..', '..');
const OUT = process.env.OUT || path.join(__dirname, 'out');
fs.mkdirSync(OUT, { recursive: true });
const errors = [];
const shot = (p, n) => p.screenshot({ path: path.join(OUT, n + '.png') });
const log = (...a) => console.log('•', ...a);
const ok = (c, m) => { if (!c) throw new Error('GAGAL: ' + m); log('OK', m); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function startServer() {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'sempat-e2e-'));
  const py = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const srv = spawn(py, ['run.py', '--no-browser', '--port', PORT], { cwd: ROOT, stdio: 'ignore',
    env: { ...process.env, SEMPAT_FAKE_OSM: '1', SEMPAT_BMKG_POLL: '0', SEMPAT_DATA_DIR: data } });
  return srv;
}
async function waitServer() {
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + '/api/meta')).ok) return; } catch { /* belum siap */ } await sleep(500); }
  throw new Error('Server tidak menyala');
}
const PADANG = { latitude: -0.9420, longitude: 100.3620 };
const IGNORE = /Failed to load resource|ERR_INTERNET_DISCONNECTED|net::ERR|EventSource|navigator.vibrate|401|cartocdn|openstreetmap|AbortError|push service|Registration failed|permission denied/i;

let SERVER = null;
(async () => {
  SERVER = startServer();
  await waitServer();
  const tok = (await (await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'petugas', password: 'petugas123' }) })).json()).token;
  await fetch(BASE + '/api/demo/reset', { method: 'POST', headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json' }, body: '{}' });
  const browser = await chromium.launch();
  const watch = (page, tag) => {
    page.on('pageerror', (e) => errors.push(`[${tag}] pageerror ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`[${tag}] console ${m.text()}`); });
  };

  // ---- warga di Padang dengan GPS
  const wctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    locale: 'id-ID', geolocation: { ...PADANG, accuracy: 12 }, permissions: ['geolocation', 'notifications'] });
  const w = await wctx.newPage(); watch(w, 'warga');
  await w.goto(BASE + '/');
  await w.waitForFunction(() => window.__sempat && window.__sempat.S.area && window.__sempat.S.area.meta.live && window.__sempat.S.tables, null, { timeout: 30000 });
  await w.waitForTimeout(800);
  const st0 = await w.textContent('#stateCard');
  ok(st0.includes('Tidak ada peringatan'), 'warga (GPS Padang): status normal');
  ok(/zona bahaya|luar zona|perbatasan/.test(st0), 'warga: status zona lokasi nyata tampil');
  const area = await w.evaluate(() => window.__sempat.S.area.meta);
  ok(area.id.startsWith('live-s'), `warga: peta otomatis dibangun di lokasi GPS (${area.id})`);
  ok((await w.textContent('#locStatus')).includes('GPS'), 'warga: sumber lokasi GPS');
  await shot(w, '01_warga_gps_normal');

  // ---- simulasi di lokasi sendiri
  await w.click('[data-act=trysim]');
  await w.waitForFunction(() => document.querySelector('.simcount'));
  ok(true, 'warga: hitung mundur simulasi pribadi tampil');
  await shot(w, '02_warga_sim_countdown');
  await w.waitForSelector('#overlay:not(.hidden)', { timeout: 15000 });
  const ov = await w.textContent('#overlay');
  ok(ov.includes('SIMULASI DI LOKASI ANDA'), 'warga: layar peringatan simulasi pribadi muncul');
  await shot(w, '03_warga_sim_overlay');
  await w.click('#ovGo');
  await w.waitForTimeout(500);
  const ph = await w.evaluate(() => window.__sempat.S.phase);
  ok(['EVAKUASI', 'AMAN'].includes(ph), `warga: fase ${ph}`);
  let route = await w.evaluate(() => window.__sempat.S.route);
  if (ph === 'EVAKUASI') {
    ok(route && route.ok, `warga: rute ke tempat aman dari posisi GPS (${Math.round(route.distM)} m)`);
    await shot(w, '04_warga_sim_evakuasi');
    // HP "bergerak": GPS bergeser mengikuti rute
    const pts = route.coords;
    const mid = pts[Math.floor(pts.length / 2)];
    await wctx.setGeolocation({ latitude: mid[1], longitude: mid[0], accuracy: 10 });
    await w.waitForTimeout(1200);
    const d2 = await w.evaluate(() => window.__sempat.S.route && window.__sempat.S.route.distM);
    ok(d2 < route.distM - 20, `warga: posisi GPS bergerak -> sisa jarak berkurang (${Math.round(route.distM)} -> ${Math.round(d2)} m)`);
    const end = pts[pts.length - 1];
    await wctx.setGeolocation({ latitude: end[1], longitude: end[0], accuracy: 10 });
    await w.waitForFunction(() => window.__sempat.S.phase === 'AMAN', null, { timeout: 8000 });
  }
  ok(true, 'warga: tiba di tempat aman (status AMAN)');
  await w.waitForFunction(() => document.querySelectorAll('#poskoList .item').length > 0, null, { timeout: 10000 });
  const pk = await w.textContent('#poskoList');
  ok(pk.includes('belum diverifikasi'), 'warga: fasilitas umum OSM tampil sebagai cadangan posko (berlabel belum diverifikasi)');
  await shot(w, '05_warga_aman_fasilitas');
  await w.click('[data-act=endsim]');
  await w.waitForTimeout(300);
  ok((await w.textContent('#stateCard')).includes('Tidak ada peringatan'), 'warga: simulasi pribadi diakhiri');

  // ---- petugas pindah ke wilayah Padang & menambah posko
  const pctx = await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: 'id-ID' });
  const p = await pctx.newPage(); watch(p, 'petugas');
  await p.goto(BASE + '/petugas');
  await p.fill('input[name=u]', 'petugas'); await p.fill('input[name=p]', 'petugas123'); await p.click('#loginf button[type=submit]');
  await p.waitForSelector('#alertCard .state-title');
  await p.click('[data-act=area]');
  await p.waitForSelector('dialog [data-pick=area]');
  await shot(p, '06_petugas_pilih_wilayah');
  await p.click(`dialog [data-pick=area][data-id="${area.id}"]`);
  await p.waitForFunction((id) => document.querySelector('#areaName').textContent.length > 0 && window.location && true, area.id);
  await p.waitForTimeout(1200);
  ok((await p.textContent('#areaName')).includes(area.name), 'petugas: wilayah kerja = peta otomatis warga');
  await p.click('[data-act=addpoint]');
  await p.waitForTimeout(800);
  const pos = await w.evaluate(() => window.__sempat.S.pos);
  // ketuk peta di dekat posisi warga (pakai proyeksi peta)
  const box = await p.locator('#map').boundingBox();
  await p.locator('#map').click({ position: { x: box.width * 0.5, y: box.height * 0.45 } });
  await p.waitForSelector('dialog form');
  log('lokasi titik:', await p.textContent('dialog form p.small.muted'), 'area:', await p.evaluate(() => window.__petugas && window.__petugas.area.meta.id));
  await p.check('dialog input[value=posko_sar]');
  await p.fill('dialog input[name=name]', 'Posko SAR Uji Padang');
  await p.check('dialog input[value=air]');
  await p.click('dialog button[type=submit]');
  await p.waitForFunction(() => document.querySelector('#pointList').textContent.includes('Posko SAR Uji Padang')
    || (document.querySelector('dialog #ptErr') && document.querySelector('dialog #ptErr').textContent), null, { timeout: 8000 });
  const perr = await p.evaluate(() => { const e = document.querySelector('dialog #ptErr'); return e ? e.textContent : ''; });
  ok(!perr, 'petugas: simpan titik tanpa galat ' + perr);
  ok(true, 'petugas: posko ditambahkan di wilayah Padang');
  await shot(p, '07_petugas_padang');

  // ---- petugas simulasi untuk semua HP
  await p.click('[data-act=simulate]');
  await p.waitForSelector('dialog form');
  await p.check('dialog input[name=scope][value=all]');
  await p.click('dialog button[type=submit]');
  await p.waitForFunction(() => document.querySelector('#alertCard').textContent.includes('SIMULASI'));
  await w.waitForSelector('#overlay:not(.hidden)', { timeout: 10000 });
  ok((await w.textContent('#overlay')).includes('PERINGATAN DINI TSUNAMI'), 'warga: peringatan dari petugas sampai lewat SSE');
  await w.click('#ovGo');
  await w.waitForTimeout(800);
  await w.evaluate(() => window.__sempat.refreshPosko(true));
  await w.waitForFunction(() => document.querySelector('#poskoList') && document.querySelector('#poskoList').textContent.includes('Posko SAR Uji Padang'), null, { timeout: 10000 }).catch(() => {});
  const pk2 = await w.textContent('#poskoList');
  log('posko list:', pk2.includes('Posko SAR Uji Padang') ? 'berisi posko petugas' : '(posko petugas tidak terjangkau dari titik ini)');
  await shot(w, '08_warga_alert_petugas');
  // ---- daya tampung: dasbor petugas menampilkan status alokasi; tidak pernah diam-diam dianggap valid
  await p.selectOption('#capMode', 'gedung');
  await p.waitForFunction(() => {
    const t = document.querySelector('#capBody').textContent;
    return /Teralokasi|TIDAK CUKUP|Tidak dihitung|Sedang dihitung|Gagal/.test(t);
  }, null, { timeout: 15000 });
  const capTxt0 = await p.textContent('#capBody');
  ok(/Teralokasi|TIDAK CUKUP|Tidak dihitung|Sedang dihitung|Gagal/.test(capTxt0), 'petugas: status daya tampung tampil eksplisit');
  await p.waitForFunction(() => /Teralokasi|TIDAK CUKUP|Tidak dihitung/.test(document.querySelector('#capBody').textContent), null, { timeout: 120000 });
  const capTxt = await p.textContent('#capBody');
  const capState = await p.evaluate(() => window.__petugas.sync.tables.capacity.gedung);
  if (capState.totals) {
    ok(capTxt.includes('Penduduk') && capTxt.includes('Tanpa tujuan terjangkau'), 'petugas: ringkasan permintaan/teralokasi/tanpa tujuan tampil');
    ok(capState.dests.every((d) => d.allocated <= d.capacity), 'petugas: tidak ada gedung melebihi kapasitas (dari tabel HP)');
  } else ok(capState.status === 'tanpa_penduduk' || capState.status === 'tanpa_gedung', `petugas: alokasi tidak dihitung dengan status jelas (${capState.status})`);
  const wcap = await w.evaluate(() => window.__sempat.S.tables.capacity);
  ok(wcap && wcap.aman && wcap.gedung && typeof wcap.gedung.status === 'string', 'warga: tabel HP membawa status daya tampung');
  await shot(p, '07c_petugas_daya_tampung');
  await p.click('[data-act=endalert]');
  await p.waitForSelector('dialog textarea');
  await p.fill('dialog textarea', 'Latihan selesai');
  await p.click('dialog button[value=ok]');
  await w.waitForFunction(() => document.querySelector('#stateCard').textContent.includes('dicabut'), null, { timeout: 8000 });
  ok(true, 'warga: peringatan dicabut tampil');

  // ---- SIAGA, waktu tiba sangat singkat -> warga di dekat pantai ditawari gedung bertingkat
  await p.click('[data-act=simulate]');
  await p.waitForSelector('dialog form');
  await p.check('dialog input[name=scope][value=all]');
  await p.check('dialog input[name=lvl][value=siaga]');
  await p.fill('dialog input[name=eta]', '2');
  await p.click('dialog button[type=submit]');
  await w.waitForSelector('#overlay:not(.hidden)', { timeout: 10000 });
  ok((await w.textContent('#overlay')).includes('SIAGA'), 'warga: tingkat SIAGA tampil di layar peringatan');
  await w.click('#ovGo');
  await w.waitForFunction(() => window.__sempat.S.tables && window.__sempat.S.tables.scenario && window.__sempat.S.tables.scenario.wave_m === 3, null, { timeout: 10000 });
  // Kebijakan 1 Nov 2026: hanya gedung TERVERIFIKASI yang menjadi tujuan rute; Perlu Verifikasi = info tanpa rute.
  const dests0 = await w.evaluate(() => window.__sempat.S.tables.dests);
  ok((dests0.gedung || []).every((d) => !d.id || d.tier === 'verified'), 'warga: tujuan gedung di tabel HP hanya gedung terverifikasi');
  const u0 = (dests0.unverified || [])[0];
  ok(u0 && u0.min_floor >= 1, `warga: gedung Perlu Verifikasi dikirim sebagai info (${u0 && u0.name}, lantai aman perkiraan ${u0 && u0.min_floor}+)`);
  await wctx.setGeolocation({ latitude: u0.lat - 0.0004, longitude: u0.lon + 0.0002, accuracy: 10 });
  await w.waitForSelector('#navVertical .vbox.unv', { timeout: 10000 });
  const unvTxt = await w.textContent('#navVertical .vbox.unv');
  ok(unvTxt.includes('Bukan rekomendasi') && unvTxt.includes(u0.name), 'warga: tidak sempat & tanpa gedung terverifikasi -> gedung belum diverifikasi tampil TANPA rute');
  ok(await w.locator('#navVertical .vbox.unv [data-act=vertical-on]').count() === 0, 'warga: tidak ada tombol rute ke gedung belum diverifikasi');
  ok(await w.locator('.mk-gedung.unv').count() > 0, 'warga: penanda peta berbeda untuk gedung belum diverifikasi (garis putus + "?")');
  await shot(w, '08a_warga_siaga_unverified');
  // Petugas memverifikasi gedung itu (survei lengkap + elevasi terukur) -> menjadi tujuan dengan rute & lantai.
  const ub = (await (await fetch(`${BASE}/api/buildings/${u0.id}?area=${area.id}`)).json()).building;
  const sv0 = await fetch(`${BASE}/api/buildings/${u0.id}`, { method: 'PUT', headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json' },
    // tanpa elevasi terukur: margin DEM (sedang 3 m) tetap berlaku; dengan elevasi terukur tanah gedung ini di atas
    // genangan Siaga sehingga tidak perlu naik gedung (diuji di tests/test_milestone1.py)
    body: JSON.stringify({ area_id: area.id, levels: ub.levels, levels_confirmed: true, condition: 'baik', structure: 'beton', access: '24jam' }) });
  ok(sv0.status === 200 && (await sv0.json()).tier === 'verified', 'petugas: gedung lolos survei lengkap -> Terverifikasi');
  await w.waitForFunction((id) => { const t = window.__sempat.S.tables; return !!(t && t.dests && (t.dests.gedung || []).some((d) => d.id === id)); }, u0.id, { timeout: 15000 });
  const g0 = await w.evaluate((id) => window.__sempat.S.tables.dests.gedung.find((d) => d.id === id), u0.id);
  ok(g0 && g0.min_floor >= 1 && g0.tier === 'verified', `warga: gedung terverifikasi menjadi tujuan Siaga (${g0 && g0.name}, lantai ${g0 && g0.min_floor}+)`);
  await w.waitForSelector('#navVertical .vbox:not(.unv) [data-act=vertical-on]', { timeout: 10000 });
  ok((await w.textContent('#navVertical')).includes('lantai'), 'warga: tidak sempat ke tanah tinggi -> ditawari gedung + lantai minimum');
  ok(await w.locator('.mk-gedung').count() > 0, 'warga: gedung evakuasi darurat tampil di peta (label L2+/L3+)');
  await w.click('[data-act=vertical-on]');
  await w.waitForFunction(() => window.__sempat.S.route && window.__sempat.S.route.vertical, null, { timeout: 5000 });
  ok((await w.textContent('#navDest')).includes('naik minimal ke lantai'), 'warga: rute ke gedung dengan instruksi lantai');
  await w.evaluate(() => document.querySelector('#navCard').scrollIntoView({ block: 'start' }));
  await shot(w, '08b_warga_siaga_gedung');
  await p.click('[data-act=endalert]');
  await p.waitForSelector('dialog textarea');
  await p.fill('dialog textarea', 'Latihan siaga selesai');
  await p.click('dialog button[value=ok]');
  await w.waitForFunction(() => window.__sempat.S.phase !== 'EVAKUASI', null, { timeout: 8000 });

  // ---- WASPADA: hanya menjauhi pantai, tidak ada rute evakuasi
  await p.click('[data-act=simulate]');
  await p.waitForSelector('dialog form');
  await p.check('dialog input[name=scope][value=all]');
  await p.check('dialog input[name=lvl][value=waspada]');
  await p.click('dialog button[type=submit]');
  await w.waitForSelector('#overlay:not(.hidden)', { timeout: 10000 });
  ok((await w.textContent('#overlay')).includes('WASPADA'), 'warga: peringatan WASPADA');
  await w.click('#ovGo');
  await w.waitForTimeout(500);
  ok(await w.evaluate(() => window.__sempat.S.phase === 'WASPADA'), 'warga: fase WASPADA (tanpa evakuasi)');
  ok((await w.textContent('#stateCard')).includes('Jauhi pantai'), 'warga: instruksi jauhi pantai & sungai');
  ok(await w.isHidden('#navCard'), 'warga: tidak ada rute evakuasi saat Waspada');
  await w.evaluate(() => window.scrollTo(0, 0));
  await shot(w, '08c_warga_waspada');
  await p.click('[data-act=endalert]');
  await p.waitForSelector('dialog textarea');
  await p.fill('dialog textarea', 'Latihan waspada selesai');
  await p.click('dialog button[value=ok]');
  await w.waitForFunction(() => window.__sempat.S.phase !== 'WASPADA', null, { timeout: 8000 });

  // ---- petugas survei gedung
  await p.waitForSelector('#surveyList [data-act=survey]', { timeout: 10000 });
  const sid = await p.getAttribute('#surveyList [data-act=survey]', 'data-id');
  await p.click('#surveyList [data-act=survey]');
  await p.waitForSelector('dialog input[name=levels]');
  await shot(p, '07b_petugas_survei');
  ok(await p.inputValue('dialog input[name=levels]') === '', 'petugas: isian jumlah lantai kosong (data peta tidak otomatis jadi hasil survei)');
  ok(!(await p.isChecked('dialog input[name=ac][value="24jam"]')), 'petugas: akses tidak otomatis terisi "24 jam"');
  await p.fill('dialog input[name=levels]', '5');
  await p.check('dialog input[name=st][value=beton]');
  await p.check('dialog input[name=cond][value=baik]');
  await p.check('dialog input[name=ac][value="24jam"]');
  await p.click('dialog button[type=submit]');
  await p.waitForFunction(() => (document.querySelector('#svErr') || {}).textContent.includes('dihitung langsung'), null, { timeout: 5000 });
  ok(true, 'petugas: jumlah lantai tanpa konfirmasi "dihitung langsung" ditolak');
  await p.check('dialog input[name=lvok]');
  await p.check('dialog input[name=ac][value=jam_kerja]');
  ok(await p.isVisible('dialog input[name=hours]'), 'petugas: akses jam tertentu meminta jam buka sebenarnya');
  await p.click('dialog button[type=submit]');
  await p.waitForFunction(() => (document.querySelector('#svErr') || {}).textContent.includes('jam buka'), null, { timeout: 5000 });
  ok(true, 'petugas: survei "jam tertentu" tanpa jam buka ditolak');
  await p.check('dialog input[name=ac][value="24jam"]');
  await p.click('dialog button[type=submit]');
  await p.waitForSelector('dialog input[name=levels]', { state: 'detached', timeout: 8000 });
  const bv = await (await fetch(`${BASE}/api/buildings/${sid}?area=${area.id}`)).json();
  ok(bv.building.levels === 5 && bv.building.levels_src === 'survey' && bv.building.surveyed && bv.tier === 'verified',
    `petugas: survei gedung tersimpan (5 lantai dihitung langsung, beton, kondisi baik -> ${bv.tier})`);
  await w.waitForFunction((id) => window.__sempat.S.area.buildings.some((b) => b.id === id && b.surveyed && b.tier === 'verified'), sid, { timeout: 8000 });
  ok(true, 'warga: data gedung hasil survei sampai ke HP (SSE "area")');

  // ---- pilih lokasi contoh (tanpa GPS)
  await w.evaluate(() => { localStorage.setItem('sempat_src', JSON.stringify('sim')); });
  await w.reload();
  await w.waitForFunction(() => window.__sempat && window.__sempat.S.sync && window.__sempat.S.sync.meta, null, { timeout: 15000 });
  await w.waitForSelector('#presets:not(.hidden) [data-act=preset]');
  await w.click('#presets [data-act=preset][data-i="2"]'); // Pacitan
  await w.waitForFunction(() => window.__sempat.S.area && window.__sempat.S.area.meta.id.startsWith('live-s00820'), null, { timeout: 30000 });
  ok(true, 'warga: lokasi contoh Pacitan -> peta otomatis dibangun');
  await shot(w, '09_warga_preset_pacitan');

  // ---- perencana: analisis area di layar
  const rctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'id-ID' });
  const r = await rctx.newPage(); watch(r, 'perencana');
  await r.goto(BASE + '/perencana');
  await r.fill('input[name=u]', 'perencana'); await r.fill('input[name=p]', 'perencana123'); await r.click('#loginf button[type=submit]');
  await r.waitForFunction(() => document.querySelectorAll('#stats .stat').length >= 5, null, { timeout: 15000 });
  ok((await r.textContent('#stats')).includes('Gedung evakuasi vertikal') && (await r.textContent('#stats')).includes('Terverifikasi'), 'perencana: jumlah gedung evakuasi vertikal (terverifikasi vs perlu verifikasi) untuk skenario tsunami');
  await r.click('[data-act=analyze]');
  await r.waitForFunction(() => document.querySelector('#analyzeMsg').textContent.startsWith('Selesai'), null, { timeout: 30000 });
  ok(true, 'perencana: analisis area di layar (jalan + bangunan + elevasi)');
  await r.waitForFunction(() => document.querySelectorAll('#recList .item').length > 0, null, { timeout: 15000 });
  ok(true, 'perencana: rekomendasi shelter pada area baru');
  ok((await r.textContent('#recList')).includes('naik minimal lantai'), 'perencana: rekomendasi menyebut lantai aman minimum');
  ok((await r.textContent('#dgSummary')).includes('Sumber jumlah lantai'), 'perencana: ringkasan sumber data jumlah lantai');
  ok((await r.textContent('#recList')).includes('Perlu Verifikasi'), 'perencana: rekomendasi belum disurvei berlabel "Perlu Verifikasi"');
  await r.selectOption('#scnTime', '02:00');
  await r.waitForFunction(() => window.__perencana.plan && window.__perencana.plan.time && window.__perencana.plan.time.label.startsWith('02.00'), null, { timeout: 10000 });
  ok((await r.textContent('#stats')).includes('Jam skenario: 02.00'), 'perencana: jam skenario (02.00) diteruskan ke optimizer');
  await r.selectOption('#scnTime', 'terburuk');
  await r.waitForFunction(() => window.__perencana.plan && window.__perencana.plan.time && window.__perencana.plan.time.mode === 'terburuk', null, { timeout: 10000 });
  // skenario tsunami lebih tinggi -> lebih sedikit gedung layak
  const n10 = await r.evaluate(() => window.__perencana && window.__perencana.plan.suitable_count);
  await r.fill('#wave', '3'); await r.dispatchEvent('#wave', 'input');
  await r.waitForFunction(() => window.__perencana.plan && window.__perencana.plan.wave_m === 3, null, { timeout: 10000 });
  const n3 = await r.evaluate(() => window.__perencana.plan.suitable_count);
  ok(n3 >= n10, `perencana: tinggi rencana 3 m -> ${n3} gedung layak, 10 m -> ${n10}`);
  await r.locator('[data-act=fly]').first().click();
  await r.waitForSelector('#infoBld.vbox', { timeout: 10000 });
  ok((await r.textContent('#infoBld')).includes('Awas'), 'perencana: kelayakan evakuasi vertikal per tingkat (Waspada/Siaga/Awas)');
  await shot(r, '10_perencana_analisis');

  // ---- offline: server dimatikan (setara internet putus untuk halaman maupun service worker)
  await fetch(BASE + '/api/demo/reset', { method: 'POST', headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json' }, body: '{}' });
  await w.waitForTimeout(1500);
  SERVER.kill(); await sleep(800);
  await w.reload();
  await w.waitForFunction(() => window.__sempat && window.__sempat.S.tables, null, { timeout: 20000 });
  await w.waitForTimeout(1000);
  ok(await w.isVisible('.banner.offline'), 'warga: offline -> tetap terbuka dari cache');
  await w.click('[data-act=selfevac]');
  await w.waitForTimeout(400);
  ok(await w.evaluate(() => window.__sempat.S.route && window.__sempat.S.route.ok), 'warga: rute evakuasi tetap dihitung offline');
  await shot(w, '11_warga_offline');
  await browser.close();
  const real = errors.filter((e) => !IGNORE.test(e));
  console.log('\nGalat konsol (diabaikan: ubin peta/jaringan):', errors.length);
  if (real.length) { console.log('GALAT NYATA:\n' + real.join('\n')); process.exitCode = 1; return; }
  console.log('\nE2E SELESAI');
})().catch(async (e) => { console.error(e); console.log('Galat konsol:\n' + errors.filter((x) => !IGNORE.test(x)).join('\n')); process.exitCode = 1; })
  .finally(() => { if (SERVER) SERVER.kill(); process.exit(process.exitCode || 0); });
