// Pengukur performa peramban untuk tools/bench_perf.py (bisa juga dijalankan sendiri terhadap server yang hidup).
//   BASE=http://localhost:8781 AREA=bench5000 LAT=-3.8 LON=102.26 PROFILE=android node tests/perf/perf.js
// Keluaran: satu baris JSON terakhir di stdout. PROFILE=android adalah EMULASI (CPU 4x + 4G lambat), bukan HP nyata.
const { chromium } = require('playwright');

const BASE = process.env.BASE || 'http://localhost:8781';
const AREA = process.env.AREA;
const LAT = parseFloat(process.env.LAT); const LON = parseFloat(process.env.LON);
const PROFILE = process.env.PROFILE || 'desktop';
const PROFILES = {
  desktop: { viewport: { width: 1366, height: 860 }, cpu: 1, net: null, mobile: false },
  // Kelas menengah ala Lighthouse "mobile": CPU 4x lebih lambat, RTT 150 ms, 1,6 Mbps turun / 750 kbps naik.
  android: { viewport: { width: 390, height: 844 }, cpu: 4, net: { latency: 150, downloadThroughput: 1.6e6 / 8, uploadThroughput: 750e3 / 8 }, mobile: true },
};
const P = PROFILES[PROFILE];

async function token(u, pw) {
  const r = await fetch(BASE + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: u, password: pw }) });
  return (await r.json());
}

async function measure(browser, role) {
  const ctx = await browser.newContext({ viewport: P.viewport, isMobile: P.mobile, hasTouch: P.mobile, locale: 'id-ID',
    geolocation: { latitude: LAT, longitude: LON, accuracy: 10 }, permissions: ['geolocation'], serviceWorkers: 'block' });
  const init = { sempat_legend_open: false };
  let url = BASE + '/';
  let obj = 'window.__sempat && window.__sempat.S';
  if (role === 'warga') Object.assign(init, { sempat_src: 'sim', sempat_last_area: AREA, sempat_simpos: { lat: LAT, lon: LON } });
  else {
    const t = await token(role, `${role}123`);
    Object.assign(init, { sempat_token: t.token, sempat_user: t.user, [`sempat_${role}_area`]: AREA });
    url = `${BASE}/${role}`; obj = `window.__${role}`;
  }
  await ctx.addInitScript((kv) => { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, JSON.stringify(v)); }, init);
  const page = await ctx.newPage();
  // Penanda waktu (ms sejak navigasi): area diterima, tabel rute diterima, objek peta dibuat, 'load', 'idle' pertama.
  await page.addInitScript((o) => {
    window.__marks = {};
    const mark = (n) => { if (window.__marks[n] == null) window.__marks[n] = Math.round(performance.now()); };
    const iv = setInterval(() => {
      let S; try { S = eval(o); } catch { return; }
      if (!S) return;
      if (S.area) mark('area');
      if (S.sync && S.sync.tables) mark('tables');
      if (S.m && S.m.map && window.__marks.mapobj == null) { mark('mapobj'); S.m.map.once('load', () => mark('load')); S.m.map.on('idle', () => mark('idle')); }
      if (window.__marks.tables != null && window.__marks.idle != null) clearInterval(iv);
    }, 25);
  }, obj);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const cdp = await ctx.newCDPSession(page);
  if (P.cpu > 1) await cdp.send('Emulation.setCPUThrottlingRate', { rate: P.cpu });
  if (P.net) { await cdp.send('Network.enable'); await cdp.send('Network.emulateNetworkConditions', { offline: false, ...P.net }); }
  await cdp.send('Performance.enable');
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 300000 });
  await page.waitForFunction(([o, a]) => { const S = eval(o); return S && S.m && S.area && S.area.meta.id === a && S.sync && S.sync.tables; }, [obj, AREA], { timeout: 600000, polling: 200 });
  // Peta siap = data (area + tabel rute) ada DAN peta sudah 'idle' pertama kali (semua lapisan SEMPAT tergambar).
  // Ubin peta dasar dari internet tidak ditunggu: di lingkungan uji tanpa internet ubin itu gagal/menggantung.
  await page.waitForFunction(() => window.__marks && window.__marks.idle != null && window.__marks.tables != null, null, { timeout: 600000, polling: 100 });
  const marks = await page.evaluate(() => window.__marks);
  const tReady = Math.max(marks.tables, marks.idle);
  const res = await page.evaluate((a) => {
    const e = performance.getEntriesByType('resource').filter((x) => /\/api\/area(\?|$)/.test(new URL(x.name).pathname + (x.name.includes('?') ? '?' : '')));
    const last = e[e.length - 1];
    return { t_area_ms: last ? Math.round(last.responseEnd) : null, area_transfer_B: last ? last.transferSize : null, n: a };
  }, AREA);
  // Biaya membangun GeoJSON gedung (sekali penuh), diukur di halaman dengan throttling aktif.
  const geo = await page.evaluate(async (o) => {
    const S = eval(o);
    const mod = await import('/static/js/common.js');
    const t = performance.now(); for (let i = 0; i < 5; i++) mod.buildingsGeoJSON(S.area); return { ms: Math.round((performance.now() - t) / 5 * 10) / 10, n: S.area.buildings.length };
  }, obj);
  // 3D: zoom 17 di pusat area, nyalakan lapisan 3D, geser peta 2,5 detik, hitung bingkai.
  let fps = null;
  try {
    fps = await page.evaluate(async ([o, lat, lon]) => {
      const S = eval(o); const m = S.m; const map = m.map;
      if (m.layers && m.layers.set) m.layers.set('bangunan3d', true);
      map.jumpTo({ center: [lon, lat], zoom: 17, pitch: 50 });
      await new Promise((r) => { map.once('idle', r); setTimeout(r, 20000); });
      let frames = 0; let run = true;
      const tick = () => { frames += 1; if (run) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
      const t = performance.now();
      while (performance.now() - t < 2500) { map.panBy([40, 0], { duration: 250 }); await new Promise((r) => setTimeout(r, 250)); }
      run = false;
      return Math.round(frames / ((performance.now() - t) / 1000));
    }, [obj, LAT, LON]);
  } catch (e) { fps = null; }
  const perf = await cdp.send('Performance.getMetrics');
  const heap = (perf.metrics.find((x) => x.name === 'JSHeapUsedSize') || {}).value || 0;
  await ctx.close();
  return { t_area_ms: marks.area != null ? marks.area : res.t_area_ms, t_ready_ms: tReady, area_transfer_B: res.area_transfer_B, geojson_ms: geo.ms, buildings_client: geo.n,
    heap_MB: Math.round(heap / 1048576 * 10) / 10, fps_3d: fps, marks, errors: errors.slice(0, 3) };
}

(async () => {
  const browser = await chromium.launch({ args: ['--enable-webgl', '--ignore-gpu-blocklist'] });
  const out = { profile: PROFILE, area: AREA, roles: {} };
  // Pemanasan: peluncuran pertama Chromium (kompilasi modul, cache shader) tidak dihitung sebagai biaya SEMPAT.
  try { await measure(browser, 'warga'); } catch { /* abaikan */ }
  for (const role of ['warga', 'petugas', 'perencana']) {
    try { out.roles[role] = await measure(browser, role); } catch (e) { out.roles[role] = { error: String(e.message || e).split('\n')[0] }; }
  }
  await browser.close();
  console.log(JSON.stringify(out));
})();
