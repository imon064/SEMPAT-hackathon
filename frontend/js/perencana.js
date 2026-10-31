// Dasbor perencana BPBD: siapa yang tidak sempat, shelter vertikal mana yang paling membantu.
/* global maplibregl, qrcode */
import { api, Sync, createMap, icon, esc, $, toast, renderLogin, confirmDialog, store, AREA_KIND } from './common.js';

// Grid waktu evakuasi (backend/mapgrid.py, sel 30 m, pita 3 menit) dari tabel rute yang sama dengan warga.
async function loadTimeGrid() {
  if (!S.area || !S.m) return;
  const mode = ($('#tgMode') && $('#tgMode').value) || 'aman';
  try { S.m.setTimeGrid(await api.get(`/api/areas/${encodeURIComponent(S.area.meta.id)}/time-grid?mode=${mode}&group=umum`)); }
  catch { S.m.setTimeGrid(null); }
}
import { fmtMinutes, fmtDistance, clockTime } from './core.js';

const S = { area: null, meta: null, state: null, m: null, plan: null, busy: false, pending: false, bindex: new Map(),
  marker: null, info: null, sig: '', timer: null };
const num = (n) => Math.round(n).toLocaleString('id-ID');
const BKIND = { rumah: 'Rumah', apartemen: 'Apartemen/hunian bersama', sekolah: 'Sekolah', ibadah: 'Tempat ibadah', kantor: 'Kantor', hotel: 'Hotel', kesehatan: 'Fasilitas kesehatan', komersial: 'Gedung komersial/kantor', tidak_diketahui: 'Fungsi tidak diketahui', lainnya: 'Bangunan' };
const SRC = { survey: 'Survei petugas', osm: 'OSM (jumlah lantai)', osm_part: 'OSM (bagian gedung)', osm_height: 'OSM (tinggi, perkiraan)', google: 'Google 2.5D (perkiraan)', upload: 'Unggahan perencana', default: 'Tidak ada data' };
const LEVEL_TXT = { waspada: 'Waspada (< 0,5 m)', siaga: 'Siaga (0,5–3 m)', awas: 'Awas (> 3 m)' };
const STATUS_TXT = { sempat: 'Sempat jalan kaki ke tempat aman', terlayani_shelter: 'Sempat ke shelter vertikal', sebagian: 'Sebagian penghuni tidak sempat', tidak_sempat: 'Tidak sempat' };

// ------------------------------------------------------------------ skenario
function params() {
  return { area_id: S.area ? S.area.meta.id : null, eta_min: +$('#eta').value, delay_min: +$('#delay').value, margin_min: +$('#margin').value,
    optimize_k: +$('#k').value, use_registered: $('#useReg').checked, design_height_m: +$('#wave').value,
    scenario_time: ($('#scnTime') && $('#scnTime').value) || 'terburuk' };
}
function labels() {
  const p = params();
  $('#etaV').textContent = `${p.eta_min} menit`;
  $('#delayV').textContent = `${p.delay_min} menit`;
  $('#marginV').textContent = `${p.margin_min} menit`;
  $('#kV').textContent = p.optimize_k ? `${p.optimize_k} gedung` : 'tidak';
  $('#waveV').textContent = `${String(p.design_height_m).replace('.', ',')} m`;
  const b = p.eta_min - p.delay_min - p.margin_min;
  $('#budgetV').textContent = b > 0 ? `${b} menit` : '0 menit — tidak ada waktu berjalan';
}
// Tautan paket bukti membawa area & skenario yang sedang dilihat (ETA, tinggi tsunami).
function evidenceLink() {
  const a = document.getElementById('evLink'); if (!a || !S.area) return;
  const p = new URLSearchParams({ area: S.area.meta.id, eta: $('#eta').value || 20 });
  if ($('#wave').value) p.set('wave', $('#wave').value);
  a.href = `/perencana/bukti?${p}`;
}
function schedule() { labels(); evidenceLink(); clearTimeout(S.timer); S.timer = setTimeout(calc, 250); }

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
  $('#stats').insertAdjacentHTML('beforeend', `<div class="stat" style="grid-column:1/-1"><div class="small muted">Gedung evakuasi vertikal untuk tsunami ${String(p.wave_m).replace('.', ',')} m (lantai aman konservatif, termasuk margin galat elevasi)</div>
    <div class="n">${num(p.destination_count)} <span class="tier-badge t-verified">Terverifikasi</span></div><div class="small">menjadi tujuan rute warga · kapasitas lantai aman ± ${num(p.destination_capacity)} orang</div>
    <div class="small"><strong>${num(p.verification_count)}</strong> <span class="tier-badge t-unverified">Perlu Verifikasi</span> cukup tinggi bila terverifikasi (± ${num(Math.max(0, p.suitable_capacity - p.destination_capacity))} orang) — <strong>bukan</strong> tujuan sampai disurvei petugas${p.unknown_levels ? ` · <strong>${num(p.unknown_levels)}</strong> gedung relevan belum ada data jumlah lantai` : ''}</div>
    <div class="small muted">Jam skenario: ${esc((p.time && p.time.label) || '-')}${p.closed_count ? ` · ${num(p.closed_count)} gedung tutup pada jam ini tidak dihitung` : ''}</div></div>`);
  const al = p.allocation;
  if (al) {
    // Alokasi berkapasitas akhir (model yang SAMA dengan rute warga): tidak ada gedung melebihi kapasitas.
    const t = al.totals || {};
    const ok = al.status === 'ok';
    $('#stats').insertAdjacentHTML('beforeend', `<div class="stat ${t.unresolved ? 'bad' : ''}" style="grid-column:1/-1"><div class="small muted">Alokasi berkapasitas (shelter terdaftar + rekomendasi) · ${esc(String(al.method || '').startsWith('lp') ? (al.optimal ? 'LP optimal (HiGHS)' : 'LP, belum terbukti optimal') : 'heuristik greedy (cadangan)')}${al.timing && al.timing.total_s != null ? ` · ${String(al.timing.total_s.toFixed(1)).replace('.', ',')} s` : ''}</div>
      <div class="small">Ke gedung: <strong>${num(t.to_shelter)}</strong> · ke zona aman (kapasitas tidak diketahui): <strong>${num(t.to_unlimited)}</strong> · tanpa tujuan terjangkau: <strong>${num(t.unresolved)}</strong> · tiba setelah batas waktu: <strong>${num(t.late)}</strong> · kapasitas terpakai ${num(t.capacity_used)} / ${num(t.capacity_total)}</div>
      ${!ok || (al.errors || []).length ? `<div class="small" style="color:var(--danger)">${esc([al.status === 'gagal' ? 'Alokasi gagal / tidak lolos validasi.' : al.status === 'kurang' ? 'Ada warga tanpa tujuan yang terjangkau.' : '', ...(al.errors || [])].filter(Boolean).join(' · '))}</div>` : ''}
      <div class="small muted">Urutan rekomendasi: ${esc(p.recommend_method || '-')}. Penduduk = model (bukan data kependudukan).</div></div>`);
  }
  const max = Math.max(1, ...p.curve.map((c) => c.red_total));
  $('#bars').innerHTML = p.curve.map((c) => `<div style="height:${Math.max(2, (c.red_total / max) * 100)}%"><span>${num(c.red_total)}</span></div>`).join('');
  $('#barsX').innerHTML = p.curve.map((c) => `<span>+${c.k}</span>`).join('');
  $('#barsText').textContent = p.curve.map((c) => `${c.k} gedung: ${c.red_total} orang tidak sempat`).join('; ');

  $('#recList').innerHTML = p.recommended.length ? p.recommended.map((r, i) => `<div class="item">
      <div class="head"><div><div class="title">${i + 1}. ${esc(r.name)}</div>
        <div class="small muted">${esc(BKIND[r.kind] || r.kind)} · ${r.levels} lantai · naik minimal lantai ${r.min_floor} · kapasitas lantai aman ± ${num(r.capacity)} orang</div>
        <div class="small muted">Data lantai: ${esc(SRC[r.levels_src] || r.levels_src)}${r.surveyed ? ' · disurvei petugas' : ' · belum disurvei'}${r.min_floor_nominal != null && r.min_floor_nominal !== r.min_floor ? ` · lantai aman nominal ${r.min_floor_nominal}, konservatif ${r.min_floor} (margin elevasi ${r.elev_margin_m} m)` : ''}</div>
        ${r.tier !== 'verified' ? `<div class="small" style="color:var(--warn,#92400e)"><span class="tier-badge t-unverified">Perlu Verifikasi</span> angka di bawah hanya berlaku SETELAH lolos survei${(r.caveats || []).length ? `: ${esc(r.caveats.join(' · '))}` : ''}</div>` : '<span class="tier-badge t-verified">Terverifikasi</span>'}</div></div>
      <div>${r.requires_verification ? 'Bila terverifikasi, dapat menampung' : 'Menampung'} <strong>${num(r.assigned)}</strong> orang yang tadinya tidak sempat <span class="small muted">(${num(r.assigned_umum)} umum, ${num(r.assigned_rentan)} rentan)</span></div>
      <div class="row"><button class="btn" type="button" data-act="fly" data-id="${esc(r.id)}">${icon('pin')} Lihat</button>
        ${r.tier === 'verified' ? `<button class="btn primary" type="button" data-act="reg" data-id="${esc(r.id)}">Daftarkan sebagai shelter</button>` : '<span class="small muted">Masuk daftar verifikasi: minta petugas survei (kondisi, struktur beton/baja, akses 24 jam, jumlah lantai, elevasi) sebelum didaftarkan.</span>'}</div></div>`).join('')
    : `<p class="muted">${params().optimize_k ? (r1 ? `Tidak ada gedung yang cukup tinggi dan terjangkau untuk tsunami ${String(p.wave_m).replace('.', ',')} m (syarat: jumlah lantai diketahui, bukan rumah/kayu/bata, lantai teratas ≥ lantai aman).` : 'Semua penghuni sudah sempat pada skenario ini.') : 'Geser “Cari gedung tambahan” untuk mendapat rekomendasi.'}</p>`;

  const vl = p.verification_list || [];
  $('#verList').innerHTML = vl.length ? vl.slice(0, 12).map((v) => `<div class="item"><div class="head"><div>
      <div class="title">${esc(v.name)} <span class="tier-badge t-unverified">Perlu Verifikasi</span></div>
      <div class="small muted">${v.levels} lantai · lantai aman ${v.min_floor}+ · bila terverifikasi ± ${num(v.capacity_if_verified)} orang</div>
      <div class="small muted">${esc((v.caveats || []).join(' · '))}</div></div></div>
      <div class="row"><button class="btn" type="button" data-act="fly" data-id="${esc(v.id)}">${icon('pin')} Lihat</button></div></div>`).join('')
    + (vl.length > 12 ? `<p class="small muted">+ ${num(p.verification_count - 12)} gedung lain.</p>` : '')
    : '<p class="muted">Tidak ada gedung Perlu Verifikasi yang cukup tinggi pada skenario ini.</p>';

  const assigned = new Map(p.shelters.map((s) => [s.id, s]));
  const regs = (S.state && S.state.shelters) || [];
  $('#regList').innerHTML = regs.length ? regs.map((s) => {
    const a = assigned.get(s.id);
    return `<div class="item"><div class="head"><div><div class="title">${esc(s.name)}</div>
      <div class="small muted">${s.levels} lantai${a ? ` · naik minimal lantai ${a.min_floor} · kapasitas ± ${num(a.capacity)} · menampung ${num(a.assigned)} orang pada skenario ini` : ''}</div>
      ${a && !a.usable ? `<div class="small" style="color:var(--danger)">Bukan shelter resmi pada skenario tsunami ${String(p.wave_m).replace('.', ',')} m: ${esc(a.reason || 'lantai kurang tinggi')}. Warga tidak diarahkan ke sini sebagai shelter resmi.</div>` : ''}</div></div>
      <div class="row"><button class="btn" type="button" data-act="fly" data-id="${esc(s.id)}">${icon('pin')} Lihat</button>
      <button class="btn ghost" type="button" data-act="unreg" data-id="${esc(s.id)}">Batalkan</button></div></div>`;
  }).join('') : '<p class="muted">Belum ada. Daftarkan dari rekomendasi di atas.</p>';

  // Kapasitas pada peta = angka skenario perencanaan ini (shelter terdaftar + rekomendasi), model alokasi yang sama.
  S.m.setCapacity([...p.shelters.filter((x) => x.usable), ...p.recommended].map((x) => ({ id: x.id, name: x.name, lat: x.lat, lon: x.lon,
    capacity: x.capacity, allocated: x.assigned })));
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
  if (b && b.kind !== 'rumah') {
    $('#infoBody').insertAdjacentHTML('afterbegin', '<div id="infoBld" class="small muted">Memeriksa kelayakan evakuasi vertikal…</div>');
    api.get(`/api/buildings/${encodeURIComponent(b.id)}?area=${encodeURIComponent(S.area.meta.id)}`).then((v) => {
      const L = v.levels, x = v.building;
      const el = $('#infoBld'); if (!el) return;
      el.className = 'vbox' + (v.tier === 'verified' ? ' ver' : '');
      el.innerHTML = `<h3>Evakuasi vertikal: ${esc(x.name || BKIND[x.kind] || 'gedung')}</h3>
        <div class="small">${x.levels} lantai · ${esc(SRC[x.levels_src] || x.levels_src)}${x.height_m ? ` (± ${x.height_m} m${x.levels_est ? `, perkiraan ÷ ${String(v.height_per_level_m || 4).replace('.', ',')} m per lantai` : ''})` : ''} · ${x.elev == null ? 'elevasi tanah tidak tersedia (dihitung 0 m)' : `tanah ± ${Math.round(x.elev)} m${x.elev_src === 'survei' ? ' (diukur petugas)' : x.elev_conf === 'rendah' ? ' (kurang pasti)' : ''}`}${x.basement ? ` · ${x.basement} basement (jangan dipakai berlindung)` : ''}</div>
        <dl class="kv2 small">${['waspada', 'siaga', 'awas'].map((k) => `<dt>${LEVEL_TXT[k]}</dt><dd>${L[k].suitable ? `<strong>Lantai ${L[k].min_floor}+</strong>${L[k].min_floor_nominal !== L[k].min_floor ? ` (nominal ${L[k].min_floor_nominal}, margin ${L[k].elev_margin_m} m)` : ''}, ± ${num(L[k].capacity)} orang${L[k].destination ? '' : ' · belum tujuan (Perlu Verifikasi)'}` : esc(L[k].reason || 'tidak tergenang')}</dd>`).join('')}</dl>
        <div class="small muted">Awas memakai skenario ± ${v.design_m} m. ${v.eligible ? (v.tier === 'verified' ? 'Terverifikasi petugas.' : `<strong>Perlu Verifikasi</strong>: ${esc((v.caveats || []).join(' · '))}.`) : `<strong>Tidak Aman</strong>: ${esc(v.why)}.`}${v.access_now && v.access_now.note ? ` Akses: ${esc(v.access_now.note)}.` : ''}</div>`;
    }).catch(() => { const el = $('#infoBld'); if (el) el.remove(); });
  }
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
    <dt>Tempat aman</dt><dd style="font-weight:400">${a.meta.hazard_method === 'polygon' ? `Di luar poligon bahaya dan ≥ ${p.hazard_edge_buffer_m ?? 50} m dari batasnya.` : `Di luar zona bahaya dan elevasi ≥ ${p.hazard_elev_threshold_m + Math.max(p.safe_buffer_m, p.dem_uncertainty_m || 0)} m (ambang + ${Math.max(p.safe_buffer_m, p.dem_uncertainty_m || 0)} m untuk galat DEM), atau ≥ ${p.hazard_max_coast_dist_m / 1000} km dari pantai.`}</dd>
    <dt>Evakuasi vertikal</dt><dd style="font-weight:400">Genangan = tinggi tsunami − ketinggian tanah (model “bak mandi”, cenderung melebih-lebihkan). Lantai aman = genangan dibulatkan ke atas + 1 (pedoman Kantor Kabinet Jepang 2005: 2 m → lantai 3, 3 m → lantai 4). <strong>Keputusan memakai lantai aman konservatif</strong>: ketinggian tanah dikurangi margin galat elevasi (diukur petugas ${(p.elev_margin_m || {}).tinggi ?? 0} m, kepercayaan sedang ${(p.elev_margin_m || {}).sedang ?? 3} m, rendah ${(p.elev_margin_m || {}).rendah ?? 5} m; elevasi tidak tersedia = 0 m). Margin ini <strong>ASUMSI awal</strong> yang perlu divalidasi BPBD. Hanya gedung <strong>Terverifikasi</strong> yang menjadi tujuan rute & alokasi; gedung Perlu Verifikasi = daftar verifikasi. Tidak disarankan: jumlah lantai tidak diketahui, rumah pribadi, kayu/bata tanpa rangka beton, tidak bisa dimasuki. Skenario rencana: <strong>ASUMSI</strong> ${p.tsunami_design_height_m ?? 10} m; saat peringatan: Waspada 0,5 m · Siaga 3 m · Awas = skenario rencana, kecuali ada perkiraan resmi.</dd>
    <dt>Ruas berisiko</dt><dd style="font-weight:400">Di zona bahaya, terowongan/underpass dibebani ×${p.underpass_penalty ?? 10} dan jembatan &lt; ${(p.bridge_coast_m ?? 1500) / 1000} km dari pantai ×${p.bridge_penalty ?? 3}: rute memilih jalan lain bila ada.</dd>
    <dt>Kecepatan</dt><dd style="font-weight:400">1,0 m/detik (umum), 0,5 m/detik (lansia/difabel) — pedoman MLIT Jepang; tanjakan memperlambat menurut fungsi Tobler; tangga 0,21 m/detik vertikal.</dd>
    <dt>Penduduk</dt><dd style="font-weight:400">${(() => { const pp = a.meta.population || {}; return pp.method === 'grid'
      ? `${pp.synthetic ? '<strong>SINTETIS (uji)</strong> — ' : ''}${esc(pp.label || 'grid penduduk')} · ± ${num(pp.total)} orang di area${pp.virtual_origins ? ` (${num(pp.virtual_origins)} sel tanpa bangunan terunduh menjadi titik asal di jalan terdekat)` : ''}${pp.unassigned ? ` · ${num(pp.unassigned)} orang tidak bisa ditempatkan` : ''}`
      : `<strong>PERKIRAAN MODEL</strong> ${p.persons_per_house} orang per rumah (bukan data kependudukan). Unggah grid penduduk ±30 m untuk mengganti.`; })()} ${Math.round(p.vulnerable_share * 100)}% rentan (ASUMSI).</dd>
    <dt>Kapasitas shelter</dt><dd style="font-weight:400">luas × jumlah lantai di atas lantai aman × ${p.shelter_usable_factor} ÷ ${p.space_per_person_m2} m²/orang (FEMA P-646). Kapasitas membatasi alokasi perencanaan dan rute warga: bila perkiraan penghuni sekitar melebihi kapasitas, sebagian warga diarahkan ke tujuan lain. Luas tidak diketahui = kapasitas 0 (tidak dipakai).</dd>
    <dt>Jumlah lantai</dt><dd style="font-weight:400">building:levels / survei bila ada. Bila hanya tinggi: lantai = tinggi ÷ ${String(p.height_per_level_m ?? 4).replace('.', ',')} m dibulatkan ke bawah (<strong>perkiraan</strong>; tinggi atap/kubah tidak dihitung; tinggi tempat ibadah tidak dipakai). Perkiraan tidak pernah membuat gedung “Terverifikasi”.</dd>
    <dt>Akses &amp; jam buka</dt><dd style="font-weight:400">Shelter resmi wajib disurvei: beton/baja, akses 24 jam. Gedung berjam terbatas memakai jam buka hasil survei pada waktu peringatan (zona waktu area: ${esc((S.meta && S.meta.timezone && `${S.meta.timezone.label}, sumber ${S.meta.timezone.src}`) || '-')}). Akses belum diketahui = “Perlu Verifikasi”, tidak dianggap terbuka.</dd>
    <dt>Ukuran data</dt><dd style="font-weight:400">${num(c.nodes || 0)} simpul jalan, ${num(c.edges || 0)} ruas, ${num(c.buildings || 0)} bangunan.</dd>
  </dl>
  <p class="small muted" style="margin:.6rem 0 0">Hasil adalah model untuk perencanaan dan latihan, bukan jaminan keselamatan. Penentuan aman/tidak aman bersifat deterministik (aturan dan perhitungan rute), bukan AI.</p>`;
}

function renderDG() {
  const a = S.area; if (!a || !$('#dgSummary')) return;
  const nh = a.buildings.filter((b) => b.survey_prio || b.surveyed || b.tier === 'verified');  // gedung relevan (aturan server)
  const cnt = {};
  nh.forEach((b) => { const k = b.levels_src || 'osm'; cnt[k] = (cnt[k] || 0) + 1; });
  const ver = nh.filter((b) => b.tier === 'verified').length;
  const sv = nh.filter((b) => b.surveyed).length;
  $('#dgSummary').innerHTML = nh.length
    ? `<strong>${num(nh.length)}</strong> gedung relevan untuk evakuasi vertikal. Sumber jumlah lantai: ${Object.entries(cnt).sort((x, y) => y[1] - x[1]).map(([k, v]) => `${esc(SRC[k] || k)} ${num(v)}`).join(' · ')}. Disurvei petugas: ${num(sv)} · terverifikasi: ${num(ver)}.`
    : 'Belum ada data gedung relevan di area ini.';
  $('#dgSummary').insertAdjacentHTML('beforeend', `<br>Zona bahaya: ${a.meta.hazard_method === 'polygon' ? 'peta resmi (unggahan)' : '<strong>perkiraan aturan</strong> — unggah peta bahaya resmi bila ada'}.`);
  const [w, s2, e, n] = a.meta.bbox;
  $('#bboxTxt').textContent = `[${[w, s2, e, n].map((v) => v.toFixed(5)).join(', ')}]`;
}

async function uploadGeo(input, kind) {
  const f = input.files && input.files[0]; if (!f) return;
  const msg = $('#dgMsg');
  if (f.size > 15 * 1024 * 1024) { msg.textContent = 'Berkas terlalu besar (maks. 15 MB). Potong ke batas area dulu.'; input.value = ''; return; }
  msg.innerHTML = '<span class="spinner"></span> Membaca berkas…';
  try {
    const geojson = JSON.parse(await f.text());
    const r = await api.post(`/api/areas/${encodeURIComponent(S.area.meta.id)}/${kind}`, { geojson, name: f.name });
    msg.textContent = kind === 'hazard'
      ? `Peta bahaya dipakai: ${r.polygons} poligon · ${num(r.hazard_nodes)} simpul jalan di zona bahaya · ${num(r.safe_nodes)} simpul tujuan aman.`
      : `Tinggi gedung: ${r.updated} diperbarui, ${r.added} gedung baru, ${r.kept} tidak ditimpa (sudah ada data lebih dipercaya), ${r.unmatched} tidak cocok.`;
  } catch (e) { msg.textContent = e instanceof SyntaxError ? 'Berkas bukan GeoJSON yang valid.' : e.message; }
  input.value = '';
}

// Grid penduduk: CSV mentah dikirim apa adanya (bisa puluhan MB). Berlaku untuk SEMUA area yang dicakupnya.
async function uploadPop(input) {
  const f = input.files && input.files[0]; if (!f) return;
  const msg = $('#dgMsg');
  if (f.size > 200 * 1024 * 1024) { msg.textContent = 'Berkas terlalu besar (maks. 200 MB). Potong ke wilayah kerja dulu.'; input.value = ''; return; }
  const gid = (f.name.toLowerCase().replace(/\.csv$/, '').replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'grid').slice(0, 40);
  const q = new URLSearchParams({ gid: gid.length >= 2 ? gid : `grid-${gid}`, name: f.name.slice(0, 120) });
  msg.innerHTML = '<span class="spinner"></span> Mengunggah grid penduduk…';
  try {
    const res = await fetch(`/api/population?${q}`, { method: 'POST', body: f, headers: { 'Content-Type': 'text/csv', Authorization: `Bearer ${api.token}` } });
    const r = await res.json();
    if (!res.ok) throw new Error(typeof r.detail === 'string' ? r.detail : `Gagal (${res.status})`);
    msg.textContent = `Grid penduduk ${r.grid.name}: ${num(r.grid.count)} sel, total ± ${num(r.grid.total)} orang, dipakai di ${r.areas.length} area. Alokasi dihitung ulang.`;
  } catch (e) { msg.textContent = e.message; }
  input.value = '';
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
  } else if (act === 'ee') {
    const msg = $('#dgMsg'); msg.innerHTML = '<span class="spinner"></span> Menghubungi Google Earth Engine… (± 10–60 detik)';
    try {
      const r = await api.post(`/api/areas/${encodeURIComponent(S.area.meta.id)}/heights/ee`, { year: 2023, add_missing: true });
      msg.textContent = `Google Open Buildings 2.5D: ${r.updated} gedung diperbarui, ${r.added} gedung baru (≥ 6 m) ditambahkan.`;
    } catch (e) { msg.textContent = e.message; }
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
  ['eta', 'delay', 'margin', 'k', 'wave'].forEach((id) => $('#' + id).addEventListener('input', schedule));
  $('#hazFile').addEventListener('change', (ev) => uploadGeo(ev.target, 'hazard'));
  $('#hgtFile').addEventListener('change', (ev) => uploadGeo(ev.target, 'heights'));
  $('#popFile').addEventListener('change', (ev) => uploadPop(ev.target));
  $('#tgMode').addEventListener('change', () => loadTimeGrid());
  $('#useReg').addEventListener('change', schedule);
  $('#scnTime').addEventListener('change', schedule);
  S.sync = new Sync({
    areaId: store.get('sempat_perencana_area', null),
    onArea: (area, meta) => {
      const firstLoad = !S.area || S.area.meta.id !== area.meta.id;
      S.area = area; S.meta = meta; S.plan = null; S.sig = ''; S.info = null;
      S.bindex = new Map(); area.buildings.forEach((b, i) => S.bindex.set(b.id, i));
      $('#areaName').innerHTML = `Area: <strong>${esc(area.meta.name)}</strong> <button class="btn ghost" style="min-height:32px;padding:.1rem .6rem" data-act="area" type="button">Ganti</button>`;
      $('#banners').innerHTML = area.meta.synthetic
        ? `<div class="banner demo">${icon('info')}<span>Mode uji: <strong>desa sintetis</strong>. Untuk desa nyata: geser peta ke desa tujuan lalu tekan <strong>Analisis area di layar</strong>.</span></div>`
        : (!area.buildings.length ? `<div class="banner offline">${icon('info')}<span>Area ini belum memuat data bangunan (peta otomatis warga). Tekan <strong>Analisis area di layar</strong> untuk mengunduh bangunan.</span></div>` : '');
      if (firstLoad || !$('#eta').value) $('#eta').value = Math.round((area.params && area.params.default_eta_min) || 20);
      if (firstLoad || !$('#wave').value) $('#wave').value = (area.params && area.params.tsunami_design_height_m) || 10;
      if (!S.m) {
        // Preset perencana: hazard, kepadatan penduduk, waktu evakuasi, kapasitas shelter, distribusi (warna bangunan).
        S.m = createMap($('#map'), area, { buildingsColor: 'plan', hazardRoads: false,
          groups: ['bangunan', 'kelayakan', 'bangunan3d', 'bahaya', 'penduduk', 'waktu', 'shelter', 'jalan', 'penanda'],
          defaults: ['bangunan', 'bahaya', 'shelter', 'penanda', 'penduduk'] });
        S.m.map.on('click', (ev) => {
          const bid = S.m.buildingAt(ev.point);
          const b = bid ? S.area.buildings[S.bindex.get(bid)] : null;
          if (b) pointInfo(b.lat, b.lon, b); else pointInfo(ev.lngLat.lat, ev.lngLat.lng, null);
        });
        S.m.map.on('mousemove', (ev) => { S.m.map.getCanvas().style.cursor = S.m.buildingAt(ev.point) ? 'pointer' : ''; });
      } else S.m.setArea(area);
      api.get(`/api/areas/${encodeURIComponent(area.meta.id)}/population`).then((r) => S.m.setPopulation(r.cells)).catch(() => S.m.setPopulation(null));
      loadTimeGrid();
      S.m.setRoute(null);
      $('#infoEmpty').classList.remove('hidden'); $('#infoBody').classList.add('hidden');
      S.m.setOfficial(area.official_points);
      renderData(); renderDG(); labels(); evidenceLink();
    },
    onAreaPatch: (area, changed, removed) => {
      S.area = area;
      S.bindex = new Map(); area.buildings.forEach((b, i) => S.bindex.set(b.id, i));
      S.m.patchBuildings(area, changed, removed);
      renderData();
      S.m.ready.then(calc);  // hasil survei mengubah tier/kapasitas gedung: hitung ulang rencana
    },
    onTables: () => loadTimeGrid(),  // grid waktu mengikuti versi tabel rute (shelter/jalan putus/peringatan)
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
  const meta = await api.get('/api/meta').catch(() => null);  // akun demo hanya ada di server lokal
  renderLogin($('#root'), {
    title: 'Masuk — Perencana BPBD', roles: ['perencana'],
    hint: meta && meta.demo_accounts
      ? 'Akun demo: <strong>perencana</strong> / <strong>perencana123</strong>.'
      : 'Masuk dengan akun yang diberikan pengelola server.',
    onDone: startDash,
  });
}
window.__perencana = S;
main();
