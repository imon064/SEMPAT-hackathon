// Dasbor perencana BPBD: siapa yang tidak sempat, shelter vertikal mana yang paling membantu.
/* global maplibregl, qrcode */
import { api, Sync, createMap, icon, esc, $, toast, renderLogin, confirmDialog, store, AREA_KIND } from './common.js';
import { fmtMinutes, fmtDistance, clockTime } from './core.js';

const S = { area: null, meta: null, state: null, m: null, plan: null, busy: false, pending: false, bindex: new Map(),
  marker: null, info: null, sig: '', timer: null };
const num = (n) => Math.round(n).toLocaleString('id-ID');
const BKIND = { rumah: 'Rumah', sekolah: 'Sekolah', ibadah: 'Tempat ibadah', kantor: 'Kantor', hotel: 'Hotel', kesehatan: 'Fasilitas kesehatan', lainnya: 'Bangunan' };
const STATUS_TXT = { sempat: 'Sempat jalan kaki ke tempat aman', terlayani_shelter: 'Sempat ke shelter vertikal', sebagian: 'Sebagian penghuni tidak sempat', tidak_sempat: 'Tidak sempat' };

// ------------------------------------------------------------------ skenario
function params() {
  return { area_id: S.area ? S.area.meta.id : null, eta_min: +$('#eta').value, delay_min: +$('#delay').value, margin_min: +$('#margin').value,
    optimize_k: +$('#k').value, use_registered: $('#useReg').checked };
}
function labels() {
  const p = params();
  $('#etaV').textContent = `${p.eta_min} menit`;
  $('#delayV').textContent = `${p.delay_min} menit`;
  $('#marginV').textContent = `${p.margin_min} menit`;
  $('#kV').textContent = p.optimize_k ? `${p.optimize_k} gedung` : 'tidak';
  const b = p.eta_min - p.delay_min - p.margin_min;
  $('#budgetV').textContent = b > 0 ? `${b} menit` : '0 menit — tidak ada waktu berjalan';
}
function schedule() { labels(); clearTimeout(S.timer); S.timer = setTimeout(calc, 250); }

async function calc() {
  if (S.busy) { S.pending = true; return; }
  S.busy = true; $('#calcStatus').textContent = 'Menghitung…';
  try {
    const t0 = performance.now();
    S.plan = await api.post('/api/plan', params());
    renderPlan();
    $('#calcStatus').textContent = `Dihitung pukul ${clockTime(new Date().toISOString())} dalam ${Math.round(performance.now() - t0)} ms untuk ${num(S.area.meta.counts.buildings)} bangunan.`;
  } catch (e) { $('#calcStatus').textContent = e.message; }
  finally { S.busy = false; if (S.pending) { S.pending = false; calc(); } }
}

// ------------------------------------------------------------------ hasil
const sum = (o) => (o ? (o.umum || 0) + (o.rentan || 0) : 0);
function stat(label, val, sub, cls = '') { return `<div class="stat ${cls}"><div class="small muted">${label}</div><div class="n">${val}</div><div class="small">${sub}</div></div>`; }

function renderPlan() {
  const p = S.plan; if (!p) return;
  const total = sum(p.total_people);
  const pct = (v) => (total ? ` (${Math.round((v / total) * 100)}%)` : '');
  const r0 = sum(p.red_before), r1 = sum(p.red_after_registered), r2 = sum(p.red_after);
  const reg = params().use_registered;
  $('#stats').innerHTML = [
    stat('Penduduk dimodelkan', num(total), `${num(p.total_people.rentan)} di antaranya rentan`),
    stat('Tidak sempat — tanpa shelter', num(r0) + pct(r0), `umum ${num(p.red_before.umum)} · rentan ${num(p.red_before.rentan)}`, r0 ? 'bad' : 'good'),
    stat(reg ? `Dengan ${p.shelters.length} shelter terdaftar` : 'Shelter terdaftar tidak dihitung', num(r1) + pct(r1), `umum ${num(p.red_after_registered.umum)} · rentan ${num(p.red_after_registered.rentan)}`, r1 ? 'bad' : 'good'),
    stat(`+ ${p.recommended.length} rekomendasi`, num(r2) + pct(r2), `umum ${num(p.red_after.umum)} · rentan ${num(p.red_after.rentan)}`, r2 < r1 || !r2 ? 'good' : 'bad'),
  ].join('');
  const max = Math.max(1, ...p.curve.map((c) => c.red_total));
  $('#bars').innerHTML = p.curve.map((c) => `<div style="height:${Math.max(2, (c.red_total / max) * 100)}%"><span>${num(c.red_total)}</span></div>`).join('');
  $('#barsX').innerHTML = p.curve.map((c) => `<span>+${c.k}</span>`).join('');
  $('#barsText').textContent = p.curve.map((c) => `${c.k} gedung: ${c.red_total} orang tidak sempat`).join('; ');

  $('#recList').innerHTML = p.recommended.length ? p.recommended.map((r, i) => `<div class="item">
      <div class="head"><div><div class="title">${i + 1}. ${esc(r.name)}</div>
        <div class="small muted">${esc(BKIND[r.kind] || r.kind)} · ${r.levels} lantai · kapasitas ± ${num(r.capacity)} orang</div></div></div>
      <div>Menampung <strong>${num(r.assigned)}</strong> orang yang tadinya tidak sempat <span class="small muted">(${num(r.assigned_umum)} umum, ${num(r.assigned_rentan)} rentan)</span></div>
      <div class="row"><button class="btn" type="button" data-act="fly" data-id="${esc(r.id)}">${icon('pin')} Lihat</button>
        <button class="btn primary" type="button" data-act="reg" data-id="${esc(r.id)}">Daftarkan sebagai shelter</button></div></div>`).join('')
    : `<p class="muted">${params().optimize_k ? (r1 ? 'Tidak ada gedung kandidat yang bisa membantu warga yang tidak sempat (kandidat: ≥ 2 lantai, di jangkauan waktu).' : 'Semua penghuni sudah sempat pada skenario ini.') : 'Geser “Cari gedung tambahan” untuk mendapat rekomendasi.'}</p>`;

  const assigned = new Map(p.shelters.map((s) => [s.id, s]));
  const regs = (S.state && S.state.shelters) || [];
  $('#regList').innerHTML = regs.length ? regs.map((s) => {
    const a = assigned.get(s.id);
    return `<div class="item"><div class="head"><div><div class="title">${esc(s.name)}</div>
      <div class="small muted">${s.levels} lantai · kapasitas ± ${num(s.capacity)}${a ? ` · menampung ${num(a.assigned)} orang pada skenario ini` : ''}</div></div></div>
      <div class="row"><button class="btn" type="button" data-act="fly" data-id="${esc(s.id)}">${icon('pin')} Lihat</button>
      <button class="btn ghost" type="button" data-act="unreg" data-id="${esc(s.id)}">Batalkan</button></div></div>`;
  }).join('') : '<p class="muted">Belum ada. Daftarkan dari rekomendasi di atas.</p>';

  // warna bangunan
  const st = p.building_status.map((x) => x || 'none');
  for (const r of p.recommended) { const i = S.bindex.get(r.id); if (i != null) st[i] = 'rekom'; }
  for (const s of regs) { const i = S.bindex.get(s.id); if (i != null) st[i] = 'shelter'; }
  S.m.setBuildingStatus(st);
  if (S.info) renderInfoTimes();
}

// ------------------------------------------------------------------ panduan per titik
async function pointInfo(lat, lon, b) {
  if (S.marker) S.marker.remove();
  S.marker = new maplibregl.Marker({ color: '#111827' }).setLngLat([lon, lat]).addTo(S.m.map);
  S.info = { lat, lon, b, data: null };
  $('#infoEmpty').classList.add('hidden');
  const body = $('#infoBody'); body.classList.remove('hidden');
  body.innerHTML = '<p class="muted">Menghitung rute…</p>';
  try {
    const llm = $('#llm') ? $('#llm').checked : false;
    S.info.data = await api.post('/api/point-info', { lat, lon, eta_min: params().eta_min, llm, area_id: S.area.meta.id });
    renderInfo();
  } catch (e) { body.innerHTML = `<p style="color:var(--danger)">${esc(e.message)}</p>`; }
}

function renderInfoTimes() {
  const el = $('#infoTimes'); if (!el || !S.info || !S.info.b || !S.plan) return;
  const i = S.bindex.get(S.info.b.id);
  const tu = S.plan.building_time.umum[i], tr = S.plan.building_time.rentan[i];
  const st = S.plan.building_status[i];
  el.innerHTML = `${tu != null ? `Waktu jalan ke tempat aman: umum <strong>${fmtMinutes(tu)}</strong> · rentan <strong>${fmtMinutes(tr)}</strong>` : 'Tidak ada rute ke tempat aman.'}
    · waktu tersedia ${fmtMinutes(S.plan.budget_s)}${st ? ` · <strong>${esc(STATUS_TXT[st] || st)}</strong>` : ''}`;
}

function renderInfo() {
  const { lat, lon, b, data } = S.info;
  const base = (S.meta && S.meta.public_url) || location.origin;
  const url = `${base}/?lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}`;
  const local = /localhost|127\.0\.0\.1/.test(base);
  const u = data.info.umum;
  if (u && u.ok && u.routes && u.routes[0]) S.m.setRoute(u.routes[0].coords); else S.m.setRoute(null);
  let qrSvg = '';
  try { const q = qrcode(0, 'M'); q.addData(url); q.make(); qrSvg = q.createSvgTag({ cellSize: 4, margin: 2, scalable: true }); } catch { qrSvg = ''; }
  const title = b ? `${b.name || BKIND[b.kind] || 'Bangunan'}` : `Titik ${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  const dist = u && u.ok && u.routes && u.routes[0] ? ` · ${fmtDistance(u.routes[0].distance_m)} ke ${esc(u.routes[0].dest.name || 'zona aman')}` : '';
  $('#infoBody').innerHTML = `
    <div><div class="title" style="font-weight:700">${esc(title)}</div><div class="small muted" id="infoTimes"></div><div class="small muted">${dist}</div></div>
    <pre class="guide" id="guideText">${esc(data.guidance)}</pre>
    <div class="row" style="align-items:flex-start">
      <div class="qr" aria-label="Kode QR menuju rute dari titik ini" role="img">${qrSvg}</div>
      <div class="small grow">Pindai untuk membuka rute evakuasi dari titik ini di HP.<br><code style="word-break:break-all">${esc(url)}</code>
        ${local ? '<br><span style="color:var(--warn)">Alamat ini “localhost”: HP warga tidak bisa membukanya. Jalankan server dengan <code>--host 0.0.0.0</code> agar kode QR memakai alamat IP laptop.</span>' : ''}</div>
    </div>
    <p class="small muted" style="margin:0">Teks: ${data.guidance_source === 'llm' ? 'disusun ulang AI dari fakta di atas — periksa sebelum dicetak' : 'templat baku (tanpa AI)'}</p>
    <div class="row no-print">
      <button class="btn primary" type="button" data-act="print">${icon('print')} Cetak panduan</button>
      <button class="btn" type="button" data-act="copy">Salin teks</button>
      ${S.meta && S.meta.llm ? '<label class="check"><input type="checkbox" id="llm"> Haluskan bahasa dengan AI</label>' : ''}
    </div>`;
  if ($('#llm')) $('#llm').onchange = () => pointInfo(lat, lon, b);
  renderInfoTimes();
}

// ------------------------------------------------------------------ data & asumsi
function renderData() {
  const a = S.area, p = a.params, c = a.meta.counts || {};
  const hz = a.meta.hazard_method === 'polygon'
    ? 'Peta bahaya tsunami resmi (GeoJSON) yang dimuat BPBD.'
    : `<strong>ASUMSI</strong> (belum ada peta bahaya resmi): elevasi &lt; ${p.hazard_elev_threshold_m} m dan jarak pantai &lt; ${p.hazard_max_coast_dist_m / 1000} km. Ganti dengan peta bahaya resmi BPBD/InaRISK bila tersedia.`;
  $('#dataBody').innerHTML = `<dl class="kv">
    <dt>Sumber</dt><dd>${(a.meta.sources || []).map(esc).join('<br>')}</dd>
    <dt>Zona bahaya</dt><dd style="font-weight:400">${hz}</dd>
    <dt>Tempat aman</dt><dd style="font-weight:400">Di luar zona bahaya dan elevasi ≥ ${p.hazard_elev_threshold_m + p.safe_buffer_m} m (ambang + ${p.safe_buffer_m} m), atau ≥ ${p.hazard_max_coast_dist_m / 1000} km dari pantai.</dd>
    <dt>Kecepatan</dt><dd style="font-weight:400">1,0 m/detik (umum), 0,5 m/detik (lansia/difabel) — pedoman MLIT Jepang; tanjakan memperlambat menurut fungsi Tobler; tangga 0,21 m/detik vertikal.</dd>
    <dt>Penduduk</dt><dd style="font-weight:400"><strong>ASUMSI</strong> ${p.persons_per_house} orang per rumah, ${Math.round(p.vulnerable_share * 100)}% rentan. Ganti dengan data kependudukan desa.</dd>
    <dt>Kapasitas shelter</dt><dd style="font-weight:400">luas × (lantai − 1) × ${p.shelter_usable_factor} ÷ ${p.space_per_person_m2} m²/orang (FEMA P-646).</dd>
    <dt>Ukuran data</dt><dd style="font-weight:400">${num(c.nodes || 0)} simpul jalan, ${num(c.edges || 0)} ruas, ${num(c.buildings || 0)} bangunan.</dd>
  </dl>
  <p class="small muted" style="margin:.6rem 0 0">Hasil adalah model untuk perencanaan dan latihan, bukan jaminan keselamatan. Penentuan aman/tidak aman bersifat deterministik (aturan dan perhitungan rute), bukan AI.</p>`;
}

// ------------------------------------------------------------------ aksi
async function onAction(act, el) {
  const id = el.dataset.id;
  if (act === 'fly') {
    const i = S.bindex.get(id); if (i == null) return;
    const b = S.area.buildings[i]; S.m.flyTo(b, 17); pointInfo(b.lat, b.lon, b);
  } else if (act === 'reg') {
    try { await api.post(`/api/shelters/${id}?area=${encodeURIComponent(S.area.meta.id)}`); toast('Shelter didaftarkan. Rute warga kini memakai gedung ini.'); await S.sync.refresh(); calc(); }
    catch (e) { toast(e.message, true); }
  } else if (act === 'unreg') {
    const ok = await confirmDialog({ title: 'Batalkan shelter?', body: '<p>Gedung ini tidak lagi menjadi tujuan rute warga.</p>', okText: 'Batalkan shelter', danger: true });
    if (!ok) return;
    try { await api.del(`/api/shelters/${id}?area=${encodeURIComponent(S.area.meta.id)}`); toast('Shelter dibatalkan.'); await S.sync.refresh(); calc(); }
    catch (e) { toast(e.message, true); }
  } else if (act === 'print') {
    document.body.classList.add('print-info'); window.print(); setTimeout(() => document.body.classList.remove('print-info'), 500);
  } else if (act === 'copy') {
    try { await navigator.clipboard.writeText($('#guideText').textContent); toast('Teks disalin.'); } catch { toast('Tidak bisa menyalin otomatis. Pilih teks lalu salin.', true); }
  } else if (act === 'area') areaDialog();
  else if (act === 'analyze') analyzeView();
  else if (act === 'logout') { api.logout(); location.reload(); }
}

// ------------------------------------------------------------------ area
async function switchArea(id) { await S.sync.setArea(id); store.set('sempat_perencana_area', id); }

async function analyzeView() {
  const b = S.m.map.getBounds();
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  const btn = $('#analyzeBtn'); btn.disabled = true;
  $('#analyzeMsg').innerHTML = '<span class="spinner"></span> Mengunduh jalan, bangunan, dan ketinggian… (± 10–60 detik)';
  try {
    const r = await api.post('/api/analyze', { bbox });
    await switchArea(r.area_id);
    $('#analyzeMsg').textContent = `Selesai: ${r.meta.name} — ${r.meta.counts.buildings} bangunan, ${r.meta.counts.nodes} simpul jalan.`;
  } catch (e) { $('#analyzeMsg').textContent = e.message; }
  finally { btn.disabled = false; }
}

async function areaDialog() {
  let areas = [];
  try { areas = (await api.get('/api/areas')).areas; } catch (e) { toast(e.message, true); }
  const d = document.createElement('dialog');
  d.innerHTML = `<div class="dlg"><h2>Pilih area</h2>
    <p class="small muted">Untuk desa baru: tutup dialog, geser &amp; perbesar peta ke desa tujuan, lalu tekan <strong>Analisis area di layar</strong>.</p>
    <div class="list" style="max-height:55vh;overflow:auto">${areas.map((a) => `<button type="button" class="item" data-id="${esc(a.id)}" style="text-align:left;cursor:pointer;${S.area && a.id === S.area.meta.id ? 'border-color:var(--info);border-width:2px' : ''}">
      <span class="title">${esc(a.name)}</span><span class="small muted">${esc(AREA_KIND(a))}${a.counts ? ` · ${a.counts.buildings} bangunan` : ''}</span></button>`).join('')}</div>
    <div class="actions"><button class="btn" type="button" data-x>Tutup</button></div></div>`;
  document.body.appendChild(d);
  d.addEventListener('close', () => d.remove());
  d.querySelector('[data-x]').onclick = () => d.close();
  d.addEventListener('click', async (ev) => { const b = ev.target.closest('[data-id]'); if (b) { d.close(); await switchArea(b.dataset.id); } });
  d.showModal();
}

// ------------------------------------------------------------------ mulai
function renderConn(on) {
  $('#conn').innerHTML = on ? '<span class="dot"></span>Terhubung' : '<span class="dot off"></span>Terputus';
}

async function startDash(user) {
  $('#who').innerHTML = `${esc(user.name || user.username)} · <button class="btn ghost" style="min-height:32px;padding:.1rem .6rem" data-act="logout" type="button">${icon('logout')} Keluar</button>`;
  $('#root').innerHTML = '';
  $('#root').appendChild($('#dashTpl').content.cloneNode(true));
  ['eta', 'delay', 'margin', 'k'].forEach((id) => $('#' + id).addEventListener('input', schedule));
  $('#useReg').addEventListener('change', schedule);
  S.sync = new Sync({
    areaId: store.get('sempat_perencana_area', null),
    onArea: (area, meta) => {
      S.area = area; S.meta = meta; S.plan = null; S.sig = ''; S.info = null;
      S.bindex = new Map(); area.buildings.forEach((b, i) => S.bindex.set(b.id, i));
      $('#areaName').innerHTML = `Area: <strong>${esc(area.meta.name)}</strong> <button class="btn ghost" style="min-height:32px;padding:.1rem .6rem" data-act="area" type="button">Ganti</button>`;
      $('#banners').innerHTML = area.meta.synthetic
        ? `<div class="banner demo">${icon('info')}<span>Mode uji: <strong>desa sintetis</strong>. Untuk desa nyata: geser peta ke desa tujuan lalu tekan <strong>Analisis area di layar</strong>.</span></div>`
        : (!area.buildings.length ? `<div class="banner offline">${icon('info')}<span>Area ini belum memuat data bangunan (peta otomatis warga). Tekan <strong>Analisis area di layar</strong> untuk mengunduh bangunan.</span></div>` : '');
      $('#eta').value = Math.round((area.params && area.params.default_eta_min) || 20);
      if (!S.m) {
        S.m = createMap($('#map'), area, { buildingsColor: 'plan', hazardRoads: false });
        S.m.map.on('click', (ev) => {
          const f = S.m.map.queryRenderedFeatures(ev.point, { layers: ['bld'] })[0];
          const b = f ? S.area.buildings[S.bindex.get(f.properties.id)] : null;
          if (b) pointInfo(b.lat, b.lon, b); else pointInfo(ev.lngLat.lat, ev.lngLat.lng, null);
        });
        S.m.map.on('mousemove', (ev) => { S.m.map.getCanvas().style.cursor = S.m.map.queryRenderedFeatures(ev.point, { layers: ['bld'] }).length ? 'pointer' : ''; });
      } else S.m.setArea(area);
      S.m.setRoute(null);
      $('#infoEmpty').classList.remove('hidden'); $('#infoBody').classList.add('hidden');
      S.m.setOfficial(area.official_points);
      renderData(); labels();
    },
    onState: (st) => {
      if (S.area && st.area_id !== S.area.meta.id) return;
      S.state = st;
      S.m.setPoints(st.points);
      S.m.setClosures(st.closures);
      const sig = JSON.stringify([st.shelters.map((s) => s.id), st.closures.map((c) => c.edge)]);
      if (sig !== S.sig) { S.sig = sig; S.m.ready.then(calc); } else if (S.plan) renderPlan();
    },
    onConn: renderConn,
  });
  try { await S.sync.start(); renderConn(S.sync.online, S.sync.lastSync); } catch (e) { toast(e.message, true); }
}

async function main() {
  document.addEventListener('click', (ev) => { const b = ev.target.closest('[data-act]'); if (b) { ev.preventDefault(); onAction(b.dataset.act, b); } });
  if (api.token) {
    try { const r = await api.get('/api/auth/me'); if (['perencana', 'admin'].includes(r.user.role)) return startDash(r.user); } catch { api.logout(); }
  }
  renderLogin($('#root'), {
    title: 'Masuk — Perencana BPBD', roles: ['perencana'],
    hint: 'Akun demo: <strong>perencana</strong> / <strong>perencana123</strong>.',
    onDone: startDash,
  });
}
main();
