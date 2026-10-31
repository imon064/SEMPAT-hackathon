// Aplikasi petugas SAR/BPBD: peringatan, titik bantuan, jalan putus.
/* global maplibregl, qrcode */
import { api, Sync, createMap, icon, esc, $, $$, toast, registerSW, renderLogin, confirmDialog, store, AREA_KIND,
  STATUS_LABEL, SERVICE_LABEL, KIND_LABEL } from './common.js';
import { clockTime, timeAgo, remainingSeconds } from './core.js';

const S = { area: null, meta: null, state: null, m: null, mode: null, tmp: null, offset: 0, html: {} };
const STATUSES = ['kosong', 'agak_ramai', 'ramai', 'penuh', 'tutup'];
const LEVEL_TXT = { waspada: 'Waspada (< 0,5 m)', siaga: 'Siaga (0,5–3 m)', awas: 'Awas (> 3 m)' };
const STRUCT = { beton: 'Beton bertulang', baja: 'Rangka baja', bata: 'Bata tanpa rangka beton', ringan: 'Kayu / bambu / semi-permanen', tidak_tahu: 'Tidak tahu' };
const ACCESS = { '24jam': 'Bisa dimasuki 24 jam', jam_kerja: 'Hanya jam kerja/ibadah', tidak: 'Tidak bisa dimasuki umum' };
const SRC = { survey: 'Survei petugas', osm: 'OpenStreetMap (jumlah lantai)', osm_height: 'OpenStreetMap (tinggi)', google: 'Google Open Buildings 2.5D (perkiraan)', upload: 'Unggahan perencana', default: 'Tidak ada data' };
const now = () => Date.now() + S.offset;
const setHTML = (el, h) => { if (S.html[el.id] !== h) { el.innerHTML = h; S.html[el.id] = h; } };
const mmss = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

// ------------------------------------------------------------------ peringatan
function renderAlert() {
  const el = $('#alertCard');
  const a = S.state && S.state.alert;
  const bm = S.meta && S.meta.bmkg;
  const bmLine = bm && bm.enabled
    ? `Pemantauan BMKG aktif${bm.last_check ? ` · diperiksa ${timeAgo(bm.last_check, now())}` : ''}${bm.last_error ? ` · <span style="color:var(--danger)">gagal: ${esc(bm.last_error)}</span>` : ''}`
    : 'Pemantauan BMKG tidak aktif (server dijalankan dengan <code>--no-bmkg</code> atau tanpa internet).';
  if (a) {
    const rem = remainingSeconds(a, now());
    el.className = 'card stack state danger';
    setHTML(el, `<div class="state-title">${icon('warning')} ${esc(a.headline || 'Peringatan aktif')}</div>
      ${a.source === 'SIMULASI' ? '<p style="margin:0"><span class="tag sim-dark">SIMULASI</span></p>' : `<p style="margin:0"><strong>Sumber: ${esc(a.source)}</strong></p>`}
      <dl class="kv">
        <dt>Gempa</dt><dd>M${esc(Number(a.magnitude).toFixed(1).replace('.', ','))} · kedalaman ${esc(Math.round(a.depth_km ?? 0))} km</dd>
        <dt>Lokasi</dt><dd>${esc(a.region || '-')}</dd>
        <dt>Diterbitkan</dt><dd>${clockTime(a.issued_at)}</dd>
        ${a.level ? `<dt>Tingkat</dt><dd>${esc(LEVEL_TXT[a.level] || a.level)}${a.wave_m ? ` · ± ${esc(a.wave_m)} m` : ''}</dd>` : ''}
        ${S.state.scenario && a.level !== 'waspada' ? `<dt>Lantai aman dihitung untuk</dt><dd>tsunami ± ${esc(S.state.scenario.wave_m)} m${S.state.scenario.design ? ' (skenario rencana)' : ''}</dd>` : ''}
        ${a.tsunami && a.eta_min != null ? `<dt>Gelombang tiba</dt><dd id="etaLine">± ${clockTime(new Date(Date.parse(a.issued_at) + a.eta_min * 60000).toISOString())} ${rem != null ? (rem > 0 ? `(sisa ${mmss(rem)})` : '(sudah lewat)') : ''}</dd>` : ''}
      </dl>
      <p class="small" style="margin:0">Semua warga yang membuka SEMPAT menerima layar peringatan dan rute evakuasi.</p>
      <button class="btn danger" data-act="endalert" type="button">Cabut peringatan…</button>
      <p class="small muted" style="margin:0">${bmLine}</p>`);
  } else {
    const l = S.state && S.state.last_alert;
    el.className = 'card stack';
    setHTML(el, `<div class="state-title">${icon('check')} Tidak ada peringatan aktif</div>
      ${l ? `<p class="small muted" style="margin:0">Terakhir: ${esc(l.headline)} (${esc(l.source)}) — dicabut ${clockTime(l.ended_at)} oleh ${esc(l.ended_by || '-')}${l.ended_note ? `: “${esc(l.ended_note)}”` : ''}</p>` : ''}
      <button class="btn" data-act="simulate" type="button">${icon('bell')} Mulai simulasi peringatan…</button>
      <p class="small muted" style="margin:0">${bmLine}</p>`);
  }
}

async function simulateDialog() {
  const eta = (S.meta && S.meta.params && S.meta.params.default_eta_min) || 20;
  const d = document.createElement('dialog');
  d.innerHTML = `<form class="dlg" method="dialog">
    <h2>Simulasi peringatan</h2>
    <p class="small muted">Untuk latihan dan uji sistem. Setiap layar warga menampilkan label <strong>SIMULASI</strong>.</p>
    <fieldset class="radio-cards" style="border:0;padding:0;margin:0"><legend class="sr-only">Jenis</legend>
      <label><input type="radio" name="t" value="1" checked><span>Tsunami</span></label>
      <label><input type="radio" name="t" value="0"><span>Gempa tanpa tsunami</span></label></fieldset>
    <div class="row">
      <label class="field grow">Magnitudo<input type="number" name="mag" value="7.2" step="0.1" min="4" max="9.9" required></label>
      <label class="field grow">Jarak pusat gempa (km)<input type="number" name="dist" value="150" min="5" max="1500" required></label>
    </div>
    <label class="field">Perkiraan gelombang tiba (menit setelah gempa)<input type="number" name="eta" value="${eta}" min="1" max="240" required></label>
    <fieldset class="radio-cards" style="border:0;padding:0;margin:0" id="lvlSet"><legend class="small muted" style="margin-bottom:.3rem">Tingkat ancaman (kategori BMKG)</legend>
      <label><input type="radio" name="lvl" value="waspada"><span>Waspada — jauhi pantai, tanpa evakuasi</span></label>
      <label><input type="radio" name="lvl" value="siaga"><span>Siaga — evakuasi</span></label>
      <label><input type="radio" name="lvl" value="awas" checked><span>Awas — evakuasi penuh</span></label></fieldset>
    <label class="field">Perkiraan tinggi tsunami di pantai (m, opsional)<input type="number" name="wave" min="0.1" max="40" step="0.1" placeholder="kosong = Siaga 3 m · Awas ${esc((S.meta && S.meta.params && S.meta.params.tsunami_design_height_m) || 10)} m (skenario rencana)"></label>
    <fieldset class="radio-cards" style="border:0;padding:0;margin:0"><legend class="small muted" style="margin-bottom:.3rem">Siapa yang menerima?</legend>
      <label><input type="radio" name="scope" value="area" ${S.area && S.area.meta.synthetic ? '' : 'checked'}><span>Warga di sekitar wilayah ini</span></label>
      <label><input type="radio" name="scope" value="all" ${S.area && S.area.meta.synthetic ? 'checked' : ''}><span>Semua HP pengguna SEMPAT (uji/demo)</span></label></fieldset>
    <p class="small" role="alert" id="simErr"></p>
    <div class="actions"><button class="btn" type="button" data-x>Batal</button><button class="btn danger" type="submit">Kirim simulasi ke semua warga</button></div>
  </form>`;
  document.body.appendChild(d);
  const f = d.querySelector('form');
  d.querySelector('[data-x]').onclick = () => d.close();
  d.addEventListener('close', () => d.remove());
  f.onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      await api.post('/api/alerts/simulate', { tsunami: f.t.value === '1', magnitude: parseFloat(f.mag.value), distance_km: parseFloat(f.dist.value), eta_min: parseFloat(f.eta.value),
        area_id: S.area.meta.id, scope: f.scope.value, level: f.lvl.value, wave_m: f.wave.value === '' ? null : parseFloat(f.wave.value) });
      d.close(); toast('Simulasi peringatan terkirim.');
      await S.sync.refresh();
    } catch (e) { d.querySelector('#simErr').textContent = e.message; }
  };
  d.showModal();
}

async function endAlert() {
  const a = S.state && S.state.alert; if (!a) return;
  const note = await confirmDialog({
    title: 'Cabut peringatan?', danger: true, okText: 'Cabut peringatan',
    body: a.source === 'BMKG' ? '<p><strong>Peringatan ini dari BMKG.</strong> Cabut hanya bila BMKG sudah mengakhiri peringatan dini tsunami.</p>' : '<p>Warga akan melihat bahwa peringatan telah berakhir.</p>',
    input: { label: 'Catatan (wajib, tampil ke warga)', min: 3, placeholder: 'contoh: BMKG mengakhiri peringatan dini pukul 15.10' },
  });
  if (!note) return;
  try { await api.post(`/api/alerts/${a.id}/end`, { note }); toast('Peringatan dicabut.'); await S.sync.refresh(); }
  catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ titik bantuan
function renderPoints() {
  const pts = (S.state && S.state.points) || [];
  if (!pts.length) { setHTML($('#pointList'), '<p class="muted">Belum ada titik. Tambahkan posko, titik kumpul, atau dapur umum.</p>'); return; }
  const html = pts.map((p) => `<div class="item" id="pt-${esc(p.id)}">
      <div class="head"><div><div class="title">${esc(p.name)}</div>
        <div class="small muted">${esc(KIND_LABEL[p.kind] || p.kind)}${p.capacity ? ` · kapasitas ${p.capacity}` : ''}${p.sample ? ' · contoh' : ''}</div></div>
        <span class="chip ${esc(p.status)}">${esc(STATUS_LABEL[p.status] || p.status)}</span></div>
      <div class="seg status" role="group" aria-label="Status ${esc(p.name)}">
        ${STATUSES.map((s) => `<button type="button" data-v="${s}" data-act="status" data-id="${esc(p.id)}" aria-pressed="${p.status === s}">${STATUS_LABEL[s]}</button>`).join('')}
      </div>
      ${(p.services || []).length ? `<div class="services">${p.services.map((s) => `<span class="svc">${esc(SERVICE_LABEL[s] || s)}</span>`).join('')}</div>` : ''}
      ${p.note ? `<div class="small">${esc(p.note)}</div>` : ''}
      <div class="row small muted">Diperbarui ${esc(timeAgo(p.updated_at, now()))} oleh ${esc(p.updated_by || '-')}</div>
      <div class="row">
        <button class="btn" type="button" data-act="fly" data-id="${esc(p.id)}">${icon('pin')} Lihat</button>
        <button class="btn" type="button" data-act="edit" data-id="${esc(p.id)}">Ubah</button>
        <button class="btn ghost" type="button" data-act="delpoint" data-id="${esc(p.id)}">Hapus</button>
      </div></div>`).join('');
  setHTML($('#pointList'), html);
}

function pointDialog(p) {
  const kinds = (S.meta && S.meta.point_kinds) || KIND_LABEL;
  const services = (S.meta && S.meta.services) || Object.keys(SERVICE_LABEL);
  const isNew = !p.id;
  const d = document.createElement('dialog');
  d.innerHTML = `<form class="dlg" method="dialog">
    <h2>${isNew ? 'Tambah titik bantuan' : 'Ubah titik'}</h2>
    <fieldset class="radio-cards" style="border:0;padding:0;margin:0"><legend class="small muted" style="margin-bottom:.3rem">Jenis titik</legend>
      ${Object.entries(kinds).map(([k, v]) => `<label><input type="radio" name="kind" value="${k}" ${p.kind === k ? 'checked' : ''} required><span>${esc(v)}</span></label>`).join('')}</fieldset>
    <label class="field">Nama yang mudah dikenali warga<input type="text" name="name" value="${esc(p.name || '')}" minlength="2" maxlength="80" required placeholder="contoh: Posko SAR Balai Desa"></label>
    <div><div class="small muted" style="margin-bottom:.3rem">Status saat ini</div>
      <div class="seg status" role="group">${STATUSES.map((s) => `<button type="button" data-v="${s}" aria-pressed="${(p.status || 'kosong') === s}">${STATUS_LABEL[s]}</button>`).join('')}</div></div>
    <fieldset style="border:0;padding:0;margin:0"><legend class="small muted" style="margin-bottom:.3rem">Layanan tersedia</legend>
      <div class="row">${services.map((s) => `<label class="check"><input type="checkbox" name="svc" value="${s}" ${(p.services || []).includes(s) ? 'checked' : ''}>${esc(SERVICE_LABEL[s] || s)}</label>`).join('')}</div></fieldset>
    <div class="row">
      <label class="field grow">Kapasitas (orang, opsional)<input type="number" name="cap" min="0" max="100000" value="${p.capacity ?? ''}"></label>
    </div>
    <label class="field">Catatan (opsional)<input type="text" name="note" maxlength="200" value="${esc(p.note || '')}" placeholder="contoh: masuk lewat gerbang timur"></label>
    <p class="small muted" style="margin:0">Lokasi: ${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}</p>
    <p class="small" role="alert" id="ptErr" style="color:var(--danger)"></p>
    <div class="actions"><button class="btn" type="button" data-x>Batal</button><button class="btn primary" type="submit">${isNew ? 'Simpan &amp; tampilkan ke warga' : 'Simpan perubahan'}</button></div>
  </form>`;
  document.body.appendChild(d);
  const f = d.querySelector('form');
  let status = p.status || 'kosong';
  $$('.seg button', d).forEach((b) => b.addEventListener('click', () => { status = b.dataset.v; $$('.seg button', d).forEach((x) => x.setAttribute('aria-pressed', String(x === b))); }));
  d.querySelector('[data-x]').onclick = () => d.close();
  d.addEventListener('close', () => { d.remove(); clearTmp(); });
  f.onsubmit = async (ev) => {
    ev.preventDefault();
    const kind = f.kind.value;
    if (!kind) { $('#ptErr', d).textContent = 'Pilih jenis titik.'; return; }
    const body = { kind, name: f.name.value.trim(), status, services: $$('input[name=svc]:checked', d).map((x) => x.value),
      capacity: f.cap.value === '' ? null : parseInt(f.cap.value, 10), note: f.note.value.trim(), lat: p.lat, lon: p.lon };
    if (isNew) body.area_id = S.area.meta.id;
    try {
      if (isNew) await api.post('/api/points', body); else await api.patch(`/api/points/${p.id}`, body);
      d.close(); toast(isNew ? 'Titik ditambahkan. Rute warga diperbarui.' : 'Perubahan disimpan.');
      await S.sync.refresh();
    } catch (e) { $('#ptErr', d).textContent = e.message; }
  };
  d.showModal();
  (isNew ? f.querySelector('input[name=kind]') : f.name).focus();
}

async function setStatus(id, status, btn) {
  const seg = btn.closest('.seg');
  $$('button', seg).forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
  try { await api.patch(`/api/points/${id}`, { status }); toast(`Status: ${STATUS_LABEL[status]}`); await S.sync.refresh(); }
  catch (e) { toast(e.message, true); S.html.pointList = null; renderPoints(); }
}

// ------------------------------------------------------------------ jalan putus
function renderClosures() {
  const cl = (S.state && S.state.closures) || [];
  setHTML($('#closureList'), cl.length ? cl.map((c) => `<div class="item"><div class="head"><div>
      <div class="title">${esc(c.note || 'Jalan putus')}</div>
      <div class="small muted">Ditandai ${esc(timeAgo(c.created_at, now()))} oleh ${esc(c.created_by || '-')}</div></div>
      <button class="btn ghost" type="button" data-act="delclosure" data-id="${esc(c.id)}">Buka kembali</button></div></div>`).join('')
    : '<p class="muted">Tidak ada jalan yang ditandai putus.</p>');
}

// ------------------------------------------------------------------ mode ketuk peta
function setMode(mode) {
  S.mode = mode;
  const b = $('#modeBanner');
  if (!mode) { b.classList.add('hidden'); b.innerHTML = ''; return; }
  const txt = mode === 'point' ? 'Ketuk lokasi titik bantuan di peta' : mode === 'locate' ? 'Ketuk lokasi wilayah kerja di peta (peta jalan di sekitarnya akan diunduh)'
    : mode === 'survey' ? 'Ketuk gedung yang Anda survei (perbesar peta sampai bentuk gedung terlihat)' : 'Ketuk ruas jalan yang putus atau terhalang';
  b.innerHTML = `<span class="grow">${txt}</span>${mode === 'point' ? '<button class="btn" type="button" data-act="gps">Pakai GPS saya</button>' : ''}<button class="btn" type="button" data-act="cancelmode">Batal</button>`;
  b.classList.remove('hidden');
  $('#map').scrollIntoView({ block: 'center', behavior: 'smooth' });
}
function clearTmp() { if (S.tmp) { S.tmp.remove(); S.tmp = null; } }

// ------------------------------------------------------------------ wilayah kerja
async function switchArea(id) {
  await S.sync.setArea(id);
  store.set('sempat_petugas_area', id);
}
async function locateArea(lat, lon) {
  toast('Menyiapkan peta wilayah… (sekali saja, ± 10–30 detik)', false, 6000);
  try {
    const r = await api.post('/api/locate', { lat, lon });
    await switchArea(r.area_id);
    toast(`Wilayah kerja: ${r.meta.name}`);
  } catch (e) { toast(e.message, true); }
}
async function areaDialog() {
  let areas = [];
  try { areas = (await api.get('/api/areas')).areas; } catch (e) { toast(e.message, true); }
  const d = document.createElement('dialog');
  d.innerHTML = `<div class="dlg"><h2>Pilih wilayah kerja</h2>
    <p class="small muted">Titik bantuan dan jalan putus berlaku untuk warga di wilayah yang sama. Peta otomatis dibuat saat warga atau petugas membuka SEMPAT di lokasi baru.</p>
    <div class="row"><button class="btn primary" type="button" data-pick="gps">${icon('locate')} Wilayah di sekitar saya (GPS)</button>
      <button class="btn" type="button" data-pick="map">Ketuk lokasi di peta</button></div>
    <div class="list" style="max-height:50vh;overflow:auto">${areas.map((a) => `<button type="button" class="item" data-pick="area" data-id="${esc(a.id)}" style="text-align:left;cursor:pointer;${S.area && a.id === S.area.meta.id ? 'border-color:var(--info);border-width:2px' : ''}">
      <span class="title">${esc(a.name)}</span><span class="small muted">${esc(AREA_KIND(a))}${a.counts ? ` · ${a.counts.nodes} simpul jalan` : ''}</span></button>`).join('')}</div>
    <div class="actions"><button class="btn" type="button" data-x>Tutup</button></div></div>`;
  document.body.appendChild(d);
  d.addEventListener('close', () => d.remove());
  d.querySelector('[data-x]').onclick = () => d.close();
  d.addEventListener('click', async (ev) => {
    const b = ev.target.closest('[data-pick]'); if (!b) return;
    ev.stopPropagation();
    if (b.dataset.pick === 'area') { d.close(); await switchArea(b.dataset.id); }
    else if (b.dataset.pick === 'map') { d.close(); S.m.map.flyTo({ center: S.m.map.getCenter(), zoom: Math.min(S.m.map.getZoom(), 11) }); setMode('locate'); }
    else if (b.dataset.pick === 'gps') {
      if (!navigator.geolocation || !window.isSecureContext) { toast('GPS butuh alamat https:// atau localhost. Ketuk di peta saja.', true); return; }
      d.close();
      navigator.geolocation.getCurrentPosition((p) => locateArea(p.coords.latitude, p.coords.longitude),
        () => toast('Gagal membaca GPS. Ketuk lokasi di peta.', true), { enableHighAccuracy: true, timeout: 15000 });
    }
  });
  d.showModal();
}

// ------------------------------------------------------------------ survei gedung (evakuasi vertikal tsunami)
function renderSurveyList() {
  const el = $('#surveyList'); if (!el || !S.area) return;
  const bl = S.area.buildings.filter((b) => b.kind !== 'rumah' && ((b.levels || 1) >= 2 || b.verified));
  const ver = bl.filter((b) => b.verified).length;
  const todo = bl.filter((b) => !b.verified).sort((a, b) => (b.levels - a.levels) || (b.hazard - a.hazard)).slice(0, 6);
  setHTML(el, `<p class="small" style="margin:0">${ver} dari ${bl.length} gedung bertingkat sudah disurvei.${bl.length ? '' : ' Belum ada gedung bertingkat tercatat di peta wilayah ini — ketuk gedung di peta untuk menambah data.'}</p>
    ${todo.length ? `<div class="small muted">Prioritas (paling tinggi, belum disurvei):</div>${todo.map((b) => `<button type="button" class="item" data-act="survey" data-id="${esc(b.id)}" style="text-align:left;cursor:pointer">
      <span class="title">${esc(b.name || 'Gedung tanpa nama')}</span><span class="small muted">${b.levels} lantai · ${esc(SRC[b.levels_src] || b.levels_src)}${b.hazard ? ' · di zona bahaya' : ''}</span></button>`).join('')}` : ''}`);
}

async function surveyDialog(bid) {
  let v;
  try { v = await api.get(`/api/buildings/${encodeURIComponent(bid)}?area=${encodeURIComponent(S.area.meta.id)}`); } catch (e) { toast(e.message, true); return; }
  const b = v.building, L = v.levels;
  const row = (k) => `<tr><td>${esc(LEVEL_TXT[k])}</td><td>${L[k].depth_m > 0 ? `± ${esc(String(L[k].depth_m).replace('.', ','))} m` : '–'}</td><td>${L[k].suitable ? `<strong>Layak</strong> · lantai ${L[k].min_floor}+` : esc(L[k].reason || 'Tidak tergenang')}</td></tr>`;
  const d = document.createElement('dialog');
  d.innerHTML = `<form class="dlg" method="dialog">
    <h2>Survei gedung: ${esc(b.name || 'tanpa nama')}</h2>
    <p class="small muted" style="margin:0">Data saat ini: ${esc(b.levels)} lantai — ${esc(SRC[b.levels_src] || b.levels_src)}${b.height_m ? ` (tinggi ± ${esc(b.height_m)} m)` : ''} · tanah ± ${esc(Math.round(b.elev))} m${b.hazard ? ' · zona bahaya' : ''}</p>
    <table class="small" style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left">Tingkat</th><th style="text-align:left">Genangan</th><th style="text-align:left">Evakuasi vertikal</th></tr></thead>
      <tbody>${row('waspada')}${row('siaga')}${row('awas')}</tbody></table>
    <p class="small muted" style="margin:0">Awas memakai skenario rencana ± ${esc(v.design_m)} m. Lantai minimum: genangan 2 m → lantai 3, 3 m → lantai 4 (pedoman Jepang).</p>
    <div class="row">
      <label class="field grow">Jumlah lantai di atas tanah<input type="number" name="levels" min="1" max="200" value="${esc(b.levels)}" required></label>
      <label class="field grow">Lantai basement<input type="number" name="basement" min="0" max="10" value="${esc(b.basement || 0)}"></label>
    </div>
    <fieldset class="radio-cards" style="border:0;padding:0;margin:0"><legend class="small muted" style="margin-bottom:.3rem">Struktur utama</legend>
      ${Object.entries(STRUCT).map(([k, t]) => `<label><input type="radio" name="st" value="${k}" ${(b.structure || 'tidak_tahu') === k ? 'checked' : ''}><span>${esc(t)}</span></label>`).join('')}</fieldset>
    <fieldset class="radio-cards" style="border:0;padding:0;margin:0"><legend class="small muted" style="margin-bottom:.3rem">Akses ke lantai atas saat darurat</legend>
      ${Object.entries(ACCESS).map(([k, t]) => `<label><input type="radio" name="ac" value="${k}" ${(b.access || '24jam') === k ? 'checked' : ''}><span>${esc(t)}</span></label>`).join('')}</fieldset>
    <div class="row">
      <label class="check"><input type="checkbox" name="roof" ${b.roof ? 'checked' : ''}> Atap datar bisa dipakai</label>
      <label class="check"><input type="checkbox" name="slf" ${b.slf ? 'checked' : ''}> Punya Sertifikat Laik Fungsi (SLF)</label>
    </div>
    <label class="field">Catatan (opsional)<input type="text" name="note" maxlength="200" value="${esc((b.survey && b.survey.note) || '')}" placeholder="contoh: tangga darurat di sisi timur, kunci dipegang marbot"></label>
    <p class="small muted" style="margin:0">Bangunan kayu/bata tanpa rangka beton dan gedung yang tidak bisa dimasuki tidak akan disarankan ke warga.</p>
    <p class="small" role="alert" id="svErr" style="color:var(--danger)"></p>
    <div class="actions">${b.verified ? '<button class="btn ghost" type="button" data-clear>Hapus survei</button>' : ''}<button class="btn" type="button" data-x>Batal</button><button class="btn primary" type="submit">Simpan survei</button></div>
  </form>`;
  document.body.appendChild(d);
  const f = d.querySelector('form');
  d.querySelector('[data-x]').onclick = () => d.close();
  d.addEventListener('close', () => d.remove());
  const clr = d.querySelector('[data-clear]');
  if (clr) clr.onclick = async () => {
    try { await api.del(`/api/buildings/${encodeURIComponent(bid)}/survey?area=${encodeURIComponent(S.area.meta.id)}`); d.close(); toast('Survei dihapus.'); } catch (e) { $('#svErr', d).textContent = e.message; }
  };
  f.onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      const r = await api.put(`/api/buildings/${encodeURIComponent(bid)}`, { area_id: S.area.meta.id, levels: parseInt(f.levels.value, 10), basement: parseInt(f.basement.value || '0', 10),
        structure: f.st.value, access: f.ac.value, roof: f.roof.checked, slf: f.slf.checked, note: f.note.value.trim() });
      d.close();
      const lv = r.levels;
      toast(lv.awas.suitable ? `Tersimpan. Layak evakuasi vertikal (Awas: lantai ${lv.awas.min_floor}+).` : lv.siaga.suitable ? `Tersimpan. Layak untuk Siaga (lantai ${lv.siaga.min_floor}+), tidak untuk Awas.` : `Tersimpan. Tidak disarankan untuk evakuasi vertikal: ${lv.awas.reason || lv.siaga.reason}`, false, 6000);
    } catch (e) { $('#svErr', d).textContent = e.message; }
  };
  d.showModal();
}

async function onMapClick(ev) {
  const pos = { lat: ev.lngLat.lat, lon: ev.lngLat.lng };
  if (S.mode === 'locate') { setMode(null); locateArea(pos.lat, pos.lon); return; }
  if (S.mode === 'survey') {
    const bid = S.m.buildingAt(ev.point);
    if (!bid) { toast('Tidak ada gedung di titik itu. Perbesar peta lalu ketuk bentuk gedung.', true); return; }
    setMode(null); surveyDialog(bid); return;
  }
  if (S.mode === 'point') {
    setMode(null);
    S.tmp = new maplibregl.Marker({ color: '#1d4ed8' }).setLngLat([pos.lon, pos.lat]).addTo(S.m.map);
    pointDialog({ ...pos, status: 'kosong', services: [] });
  } else if (S.mode === 'closure') {
    setMode(null);
    S.tmp = new maplibregl.Marker({ color: '#b91c1c' }).setLngLat([pos.lon, pos.lat]).addTo(S.m.map);
    const d = document.createElement('dialog');
    d.innerHTML = `<form class="dlg" method="dialog"><h2>Tandai jalan putus</h2>
      <p>Ruas jalan terdekat dari titik yang diketuk akan ditutup untuk semua rute.</p>
      <label class="field">Keterangan (opsional)<input type="text" name="note" maxlength="200" placeholder="contoh: jembatan ambruk, pohon tumbang"></label>
      <p class="small" role="alert" id="clErr" style="color:var(--danger)"></p>
      <div class="actions"><button class="btn" type="button" data-x>Batal</button><button class="btn danger" type="submit">Tutup ruas ini</button></div></form>`;
    document.body.appendChild(d);
    const f = d.querySelector('form');
    d.querySelector('[data-x]').onclick = () => d.close();
    d.addEventListener('close', () => { d.remove(); clearTmp(); });
    f.onsubmit = async (e) => {
      e.preventDefault();
      try { await api.post('/api/closures', { ...pos, note: f.note.value.trim(), area_id: S.area.meta.id }); d.close(); toast('Ruas ditutup. Rute warga dihitung ulang.'); await S.sync.refresh(); }
      catch (err) { $('#clErr', d).textContent = err.message; }
    };
    d.showModal(); f.note.focus();
  }
}

// ------------------------------------------------------------------ aksi
async function onAction(act, el) {
  const id = el.dataset.id;
  const pts = (S.state && S.state.points) || [];
  if (act === 'simulate') simulateDialog();
  else if (act === 'endalert') endAlert();
  else if (act === 'addpoint') setMode('point');
  else if (act === 'addclosure') setMode('closure');
  else if (act === 'surveymode') setMode('survey');
  else if (act === 'survey') surveyDialog(id);
  else if (act === 'cancelmode') setMode(null);
  else if (act === 'area') areaDialog();
  else if (act === 'gps') {
    if (!navigator.geolocation || !window.isSecureContext) { toast('GPS tidak tersedia di alamat ini. Ketuk peta saja.', true); return; }
    navigator.geolocation.getCurrentPosition((p) => { setMode(null); pointDialog({ lat: p.coords.latitude, lon: p.coords.longitude, status: 'kosong', services: [] }); },
      () => toast('Gagal membaca GPS. Ketuk lokasi di peta.', true), { enableHighAccuracy: true, timeout: 15000 });
  } else if (act === 'status') setStatus(id, el.dataset.v, el);
  else if (act === 'fly') { const p = pts.find((x) => x.id === id); if (p) { S.m.flyTo(p, 17); $('#map').scrollIntoView({ block: 'center' }); } }
  else if (act === 'edit') { const p = pts.find((x) => x.id === id); if (p) pointDialog(p); }
  else if (act === 'delpoint') {
    const p = pts.find((x) => x.id === id);
    const ok = await confirmDialog({ title: 'Hapus titik?', body: `<p>“${esc(p ? p.name : '')}” tidak akan muncul lagi di aplikasi warga. Jika hanya sementara penuh/tutup, ubah statusnya saja.</p>`, okText: 'Hapus', danger: true });
    if (!ok) return;
    try { await api.del(`/api/points/${id}`); toast('Titik dihapus.'); await S.sync.refresh(); } catch (e) { toast(e.message, true); }
  } else if (act === 'delclosure') {
    const ok = await confirmDialog({ title: 'Buka kembali ruas ini?', body: '<p>Pastikan jalan sudah benar-benar bisa dilalui.</p>', okText: 'Buka kembali' });
    if (!ok) return;
    try { await api.del(`/api/closures/${id}`); toast('Ruas dibuka kembali.'); await S.sync.refresh(); } catch (e) { toast(e.message, true); }
  } else if (act === 'reset') {
    const ok = await confirmDialog({ title: 'Reset data demo?', body: '<p>Peringatan aktif dicabut, titik dan jalan putus buatan dihapus, titik contoh dipulihkan.</p>', okText: 'Reset', danger: true });
    if (!ok) return;
    try { await api.post('/api/demo/reset'); toast('Data demo dikembalikan.'); await S.sync.refresh(); } catch (e) { toast(e.message, true); }
  } else if (act === 'logout') { api.logout(); location.reload(); }
}

// ------------------------------------------------------------------ mulai
function renderConn(on, last) {
  $('#conn').innerHTML = on ? '<span class="dot"></span>Terhubung' : `<span class="dot off"></span>Terputus${last ? ` · data ${clockTime(last)}` : ''}`;
  const b = $('#banners'), off = b.querySelector('.offline');
  if (!on && !off) b.insertAdjacentHTML('afterbegin', `<div class="banner offline">${icon('offline')}<span>Tidak terhubung ke server. Perubahan belum bisa dikirim; coba lagi saat sinyal kembali.</span></div>`);
  if (on && off) off.remove();
}

function renderAll() { if (!$('#alertCard')) return; renderAlert(); renderPoints(); renderClosures(); }

// Kode QR untuk membuka aplikasi warga di HP (juri/warga cukup memindai).
function renderQR() {
  const el = $('#openPhone'); if (!el || typeof qrcode === 'undefined') return;
  const base = (S.meta && S.meta.public_url) || location.origin;
  let svg = '';
  try { const q = qrcode(0, 'M'); q.addData(base + '/'); q.make(); svg = q.createSvgTag({ cellSize: 4, margin: 2, scalable: true }); } catch { /* abaikan */ }
  const https = base.startsWith('https://');
  el.innerHTML = `<div class="row" style="align-items:flex-start"><div class="qr" role="img" aria-label="Kode QR aplikasi warga">${svg}</div>
    <div class="small grow"><strong>Buka aplikasi warga di HP</strong><br><code style="word-break:break-all">${esc(base)}/</code><br>
    ${https ? 'GPS dan notifikasi di HP bisa dipakai.' : '<span style="color:var(--warn)">Alamat ini bukan https: di HP, GPS &amp; notifikasi diblokir browser. Pakai alamat https (deploy atau <code>run.py --tunnel</code>).</span>'}</div></div>`;
}

async function startDash(user) {
  $('#who').innerHTML = `${esc(user.name || user.username)} · <button class="btn ghost" style="min-height:32px;padding:.1rem .6rem" data-act="logout" type="button">${icon('logout')} Keluar</button>`;
  $('#root').innerHTML = '';
  $('#root').appendChild($('#dashTpl').content.cloneNode(true));
  S.sync = new Sync({
    areaId: store.get('sempat_petugas_area', null),
    onArea: (area, meta) => {
      S.area = area; S.meta = meta; S.html = {};
      $('#areaName').innerHTML = `Wilayah: <strong>${esc(area.meta.name)}</strong> <button class="btn ghost" style="min-height:32px;padding:.1rem .6rem" data-act="area" type="button">Ganti</button>`;
      if (!S.m) { S.m = createMap($('#map'), area); S.m.map.on('click', onMapClick); }
      else S.m.setArea(area);
      S.m.setOfficial(area.official_points);
      S.m.setFacilities(area.facilities || []);
      renderQR();
      renderSurveyList();
    },
    onState: (st) => {
      if (S.area && st.area_id !== S.area.meta.id) return;
      if (!st._offline) S.offset = Date.parse(st.server_time) - Date.now();
      S.state = st;
      S.m.setPoints(st.points, (p) => { const el = document.getElementById(`pt-${p.id}`); if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.style.outline = '3px solid var(--info)'; setTimeout(() => { el.style.outline = ''; }, 1500); } });
      S.m.setClosures(st.closures);
      renderAll();
    },
    onAlert: () => renderAll(),
    onConn: renderConn,
  });
  try { await S.sync.start(); renderConn(S.sync.online, S.sync.lastSync); } catch (e) { toast(e.message, true); }
  setInterval(() => { if (S.state && S.state.alert) renderAlert(); }, 1000);
  setInterval(() => { S.html.pointList = null; S.html.closureList = null; renderPoints(); renderClosures(); }, 30000);
}

async function main() {
  registerSW();
  document.addEventListener('click', (ev) => { const b = ev.target.closest('[data-act]'); if (b) { ev.preventDefault(); onAction(b.dataset.act, b); } });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && S.mode) setMode(null); });
  if (api.token) {
    try { const r = await api.get('/api/auth/me'); return startDash(r.user); } catch { api.logout(); }
  }
  renderLogin($('#root'), {
    title: 'Masuk — Petugas Lapangan', roles: ['petugas', 'perencana'],
    hint: 'Akun demo: <strong>petugas</strong> / <strong>petugas123</strong>. Ganti kata sandi sebelum dipakai sungguhan.',
    onDone: startDash,
  });
}
window.__petugas = S;
main();
