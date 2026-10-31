// Aplikasi warga: lokasi nyata -> peringatan -> arah ke tempat aman -> posko terdekat.
/* global maplibregl */
import { api, Sync, createMap, covers, icon, esc, $, $$, toast, store, registerSW, STATUS_LABEL, SERVICE_LABEL, KIND_LABEL,
  confirmDialog, enablePush, currentPushSub, pushSupport, localNotify, isIOS, isStandalone } from './common.js';
import { EdgeIndex, routeFromTable, isSafe, isHazard, headingToRoute, nextTurn, compassWord, fmtMinutes, fmtDistance,
  remainingSeconds, verdict, advanceAlong, polylineLength, clockTime, timeAgo, haversine, SPEED } from './core.js';

const DAY = 24 * 3600 * 1000;
// Lokasi contoh untuk mencoba bila juri/warga tidak sedang di pesisir (koordinat kota, bukan titik resmi).
const PRESETS = [
  { name: 'Padang', lat: -0.9420, lon: 100.3620 },
  { name: 'Banda Aceh', lat: 5.5600, lon: 95.3000 },
  { name: 'Pacitan', lat: -8.2010, lon: 111.0950 },
  { name: 'Palu', lat: -0.8840, lon: 119.8590 },
];
const S = {
  area: null, idx: null, meta: null, state: null, tables: null, m: null, sync: null,
  pos: null, src: null, acc: null, gpsMsg: '', watchId: null, follow: true,
  locating: false, locErr: '', locPending: null,
  group: store.get('sempat_group', 'umum'),
  selfEvac: store.get('sempat_selfevac', null),     // {at: ISO}
  localSim: store.get('sempat_localsim', null),     // simulasi pribadi di lokasi sendiri
  simPending: null,                                 // {sim, fireAt}
  ack: store.get('sempat_ack', null),               // id peringatan yang sudah dibaca
  manualSafe: store.get('sempat_manualsafe', null), // kunci evakuasi saat warga menyatakan sudah aman
  preview: false, target: null, atShelter: null,
  route: null, posko: null, poskoPos: null, poskoAt: 0, poskoBusy: false, poskoOffline: false,
  heading: null, online: true, offset: 0, walking: null, phase: null, html: {},
  quake: null, pushSub: null, pushMsg: '',
  vertical: false, amanRoute: null, gedungRoute: null, gedungKey: '',
};
const LEVEL_TXT = { waspada: 'WASPADA · < 0,5 m', siaga: 'SIAGA · 0,5–3 m', awas: 'AWAS · > 3 m' };
const SRC_TXT = { survey: 'diverifikasi petugas', osm: 'jumlah lantai dari OpenStreetMap', osm_height: 'tinggi dari OpenStreetMap',
  google: 'tinggi perkiraan satelit (Google Open Buildings)', upload: 'data tinggi gedung dari perencana' };
const now = () => Date.now() + S.offset;

// ------------------------------------------------------------------ utilitas
const mmss = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
function setHTML(el, html) { if (S.html[el.id] !== html) { el.innerHTML = html; S.html[el.id] = html; return true; } return false; }
const areaReady = () => !!(S.area && S.idx && S.pos && covers(S.area.meta, S.pos, 0));

// Peringatan tsunami yang berlaku untuk HP ini: dari server (BMKG/petugas) atau simulasi pribadi.
function activeAlert() {
  const a = S.state && S.state.alert;
  if (a && a.tsunami) return a;
  if (S.localSim && Date.parse(S.localSim.issued_at) <= now()) return S.localSim;
  return null;
}
function evacKey() {
  const a = activeAlert();
  if (a) return (a.self ? 'sim:' : 'alert:') + a.id;
  if (S.selfEvac) return 'self:' + S.selfEvac.at;
  return null;
}
function etaClock(a) {
  if (!a || a.eta_min == null) return null;
  return clockTime(new Date(Date.parse(a.issued_at) + a.eta_min * 60000).toISOString());
}
function zoneAt(pos) {
  if (!areaReady() || !pos) return null;
  const s = S.idx.nearest(pos.lat, pos.lon);
  if (!s) return null;
  const n = s.t < 0.5 ? s.u : s.v;
  const N = S.area.nodes;
  return { hazard: N.hazard[n] === 1, safe: N.safe[n] === 1, elev: N.elev[n], coast: N.coast[n], perp: s.perp };
}

// ------------------------------------------------------------------ fase
function computePhase() {
  const a = S.state && S.state.alert;
  const a0 = activeAlert();
  // BMKG Waspada (< 0,5 m): cukup menjauhi pantai/sungai, tidak mengungsi ke tempat tinggi.
  if (a0 && a0.level === 'waspada' && !S.selfEvac) return 'WASPADA';
  const key = evacKey();
  if (key) {
    if (S.manualSafe === key) return 'AMAN';
    if (!areaReady()) return 'EVAKUASI';
    if (isSafe(S.area, S.idx, S.pos) || S.atShelter) return 'AMAN';
    // Histeresis: setelah aman, baru kembali ke mode evakuasi bila masuk zona bahaya.
    if (S.phase === 'AMAN' && !isHazard(S.area, S.idx, S.pos)) return 'AMAN';
    return 'EVAKUASI';
  }
  if (a && !a.tsunami) return 'INFO';
  const last = S.state && S.state.last_alert;
  if (last && last.ended_at && last.tsunami && now() - Date.parse(last.ended_at) < DAY) return 'PEMULIHAN';
  return 'NORMAL';
}

// ------------------------------------------------------------------ lokasi & peta otomatis
async function setPos(pos, { center = false } = {}) {
  S.pos = pos;
  if (S.src !== 'gps') store.set('sempat_simpos', pos);
  if (!S.area || !covers(S.area.meta, pos)) ensureArea(pos);
  if (center && S.m) S.m.follow(pos, 16);
  else if (S.follow && S.m && (S.src === 'gps' || S.walking)) S.m.follow(pos);
  update();
}

async function ensureArea(pos) {
  if (S.locating) { S.locPending = pos; return; }
  S.locating = true; S.locErr = ''; update();
  try {
    const r = await api.post('/api/locate', { lat: pos.lat, lon: pos.lon });
    if (r.built) toast(`Peta ${r.meta.name} siap.`);
    if (!S.sync.area || S.sync.areaId !== r.area_id) await S.sync.setArea(r.area_id);
    store.set('sempat_last_area', r.area_id);
    if (S.pushSub) api.post('/api/push/subscribe', { subscription: S.pushSub.toJSON(), lat: pos.lat, lon: pos.lon }).catch(() => {});
  } catch (e) {
    const last = store.get('sempat_last_area', null);
    if (last && !S.sync.area) { try { await S.sync.setArea(last); } catch { /* abaikan */ } }
    S.locErr = e.status === 404 ? 'Peta otomatis tidak aktif di server ini. Pilih lokasi di dalam wilayah peta.' : e.message;
  } finally {
    S.locating = false;
    const p = S.locPending; S.locPending = null;
    update();
    if (p && (!S.area || !covers(S.area.meta, p))) ensureArea(p);
  }
}

// ------------------------------------------------------------------ rute
function trimRoute(coords, pos) {
  let best = { d: Infinity, i: 0, p: coords[0] };
  for (let i = 1; i < coords.length; i++) {
    const [ax, ay] = coords[i - 1], [bx, by] = coords[i];
    const k = Math.cos((pos.lat * Math.PI) / 180);
    const dx = (bx - ax) * k, dy = by - ay, L2 = dx * dx + dy * dy;
    let t = L2 ? (((pos.lon - ax) * k) * dx + (pos.lat - ay) * dy) / L2 : 0;
    t = Math.max(0, Math.min(1, t));
    const q = [ax + t * (bx - ax), ay + t * (by - ay)];
    const d = haversine(pos.lat, pos.lon, q[1], q[0]);
    if (d < best.d) best = { d, i, p: q };
  }
  return [[pos.lon, pos.lat], best.p, ...coords.slice(best.i)];
}

function computeRoute() {
  S.route = null; S.amanRoute = null; S.gedungRoute = null;
  if (!areaReady() || !S.tables || S.tables.area_id !== S.area.meta.id) return;
  if (S.target) {
    let r = null;
    const fresh = S.posko && S.posko.ok && S.poskoPos && haversine(S.pos.lat, S.pos.lon, S.poskoPos.lat, S.poskoPos.lon) < 60;
    const m = fresh && S.posko.routes.find((x) => x.dest.id === S.target.id);
    if (m) {
      const coords = trimRoute(m.coords, S.pos);
      const L = polylineLength(coords), L0 = Math.max(1, m.distance_m);
      r = { ok: true, coords, distM: L, timeS: m.time_s * Math.min(1.2, L / L0), dest: m.dest, src: 'server' };
    } else {
      const t = routeFromTable(S.area, S.idx, S.tables, 'posko', S.group, S.pos);
      r = t.ok ? { ...t, src: 'tabel' } : t;
    }
    S.route = r;
    return;
  }
  if (S.phase === 'EVAKUASI' || S.preview) {
    S.route = S.amanRoute = routeFromTable(S.area, S.idx, S.tables, 'aman', S.group, S.pos);
    // Pilihan terakhir: gedung beton bertingkat terdekat yang cukup tinggi untuk skenario tsunami saat ini.
    const hasG = S.tables.tables && S.tables.tables[`gedung-${S.group}`];
    S.gedungRoute = S.phase === 'EVAKUASI' && hasG ? routeFromTable(S.area, S.idx, S.tables, 'gedung', S.group, S.pos) : null;
    if (S.vertical && S.gedungRoute && S.gedungRoute.ok && S.gedungRoute.dest.type !== 'safe_zone') S.route = { ...S.gedungRoute, vertical: true };
  }
}

// Apakah perlu menawarkan gedung (tidak sempat ke tanah tinggi, atau waktu tiba tidak diketahui)?
function verticalOffer() {
  const g = S.gedungRoute, r = S.amanRoute;
  if (S.phase !== 'EVAKUASI' || S.target || !g || !g.ok || g.dest.type === 'safe_zone') return null;
  if (r && r.ok && g.timeS >= r.timeS - 30) return null;  // tanah tinggi sama cepatnya: tetap ke sana
  const a = activeAlert();
  const rem = a ? remainingSeconds(a, now()) : null;
  const late = !r || !r.ok || (rem != null && r.timeS > rem - 60);
  if (late) return { strong: true, g, rem };
  if (rem == null) return { strong: false, g, rem };
  return null;
}

async function refreshPosko(force = false) {
  if (!areaReady() || S.poskoBusy) return;
  const moved = S.poskoPos ? haversine(S.pos.lat, S.pos.lon, S.poskoPos.lat, S.poskoPos.lon) : Infinity;
  if (!force && moved < 25 && Date.now() - S.poskoAt < 15000) return;
  S.poskoBusy = true;
  const at = { ...S.pos };
  try {
    S.posko = await api.post('/api/route', { lat: at.lat, lon: at.lon, mode: 'posko', group: S.group, alternatives: 6, area_id: S.area.meta.id });
    S.poskoPos = at; S.poskoAt = Date.now(); S.poskoOffline = !!api.lastOffline;
  } catch (e) {
    S.poskoOffline = true;
  } finally {
    S.poskoBusy = false;
    update();
  }
}

// ------------------------------------------------------------------ pembaruan utama
function update() {
  const prev = S.phase;
  S.phase = computePhase();
  if (S.phase !== prev) onPhaseChange(prev, S.phase);
  computeRoute();
  detectArrival();
  renderState(); renderNav(); renderCountdown(); renderLoc(); renderPosko(); renderMap();
}

function onPhaseChange(prev, ph) {
  if (ph === 'AMAN' && prev === 'EVAKUASI') {
    stopWalk();
    vibrate([200, 100, 200]);
    toast('Anda sudah di tempat aman. Tetap di sini.');
    refreshPosko(true);
  }
  if (ph === 'EVAKUASI') { S.preview = false; S.target = null; S.follow = true; if (S.pos && S.m) S.m.follow(S.pos, 16); }
  if (ph !== 'EVAKUASI' && ph !== 'AMAN') S.vertical = false;
  if (ph === 'PEMULIHAN' || ph === 'INFO' || ph === 'AMAN') refreshPosko(true);
}

function detectArrival() {
  if (S.atShelter && S.pos && haversine(S.pos.lat, S.pos.lon, S.atShelter.lat, S.atShelter.lon) > 80) S.atShelter = null;
  const r = S.route;
  if (!r || !r.ok || !S.pos) return;
  if (S.phase === 'EVAKUASI' && ['shelter', 'gedung', 'tanah_tinggi'].includes(r.dest.type) && r.distM < 20) {
    S.atShelter = r.dest; S.phase = computePhase(); onPhaseChange('EVAKUASI', S.phase); computeRoute();
  }
  if (S.target && !S.target.arrived && r.distM < 20) {
    S.target.arrived = true; stopWalk(); vibrate([200, 100, 200]);
    toast(`Anda sudah sampai di ${S.target.name}.`);
  }
}

// ------------------------------------------------------------------ tampilan: kartu status
function zoneHTML() {
  const z = zoneAt(S.pos);
  if (!z) return '';
  const km = z.coast >= 1e8 ? 'jauh dari pantai' : `${fmtDistance(z.coast)} dari pantai`;
  const rule = S.area.meta.hazard_method === 'rule' ? ' <span class="small" style="font-weight:400">(perkiraan: ketinggian &lt; 20 m dan &lt; 3 km dari pantai)</span>' : '';
  if (z.hazard) return `<div class="zone danger">${icon('warning')}<span>Lokasi Anda di <strong>zona bahaya tsunami</strong> — ketinggian ± ${Math.round(z.elev)} m, ${km}.${rule}</span></div>`;
  if (z.safe) return `<div class="zone safe">${icon('check')}<span>Lokasi Anda di luar zona bahaya tsunami — ketinggian ± ${Math.round(z.elev)} m, ${km}.</span></div>`;
  return `<div class="zone unknown">${icon('info')}<span>Lokasi Anda di perbatasan zona bahaya — ketinggian ± ${Math.round(z.elev)} m, ${km}. Saat peringatan, naik ke tempat lebih tinggi.</span></div>`;
}

function STEP0(q = 'Gempa kuat atau lama?') {
  return `<div class="steps0"><strong>${q}</strong> Jika sulit berdiri atau guncangan ± 20 detik, jangan tunggu peringatan — langsung ke tempat tinggi.
    <div class="row" style="margin-top:.6rem"><button class="btn danger" data-act="selfevac">${icon('warning')} Saya merasakan gempa kuat</button></div></div>`;
}

function notifHTML() {
  const sup = pushSupport();
  if (S.pushSub) return `<div class="row small"><span class="grow">${icon('bell')} Notifikasi peringatan <strong>aktif</strong> di HP ini.</span><button class="btn" data-act="testpush">Kirim notifikasi uji</button></div>`;
  if (!sup.ok && !sup.local) return `<p class="small muted" style="margin:0">${icon('bell')} ${esc(sup.why)}</p>`;
  if ('Notification' in window && Notification.permission === 'denied') return `<p class="small muted" style="margin:0">${icon('bell')} Notifikasi diblokir. Izinkan lewat ikon gembok di bilah alamat.</p>`;
  return `<div class="row"><button class="btn" data-act="notif">${icon('bell')} Aktifkan notifikasi peringatan</button>${S.pushMsg ? `<span class="small muted">${esc(S.pushMsg)}</span>` : ''}</div>`;
}

function quakeHTML() {
  const q = S.quake;
  if (!q) return '';
  const mag = q.magnitude != null ? `M${Number(q.magnitude).toFixed(1).replace('.', ',')}` : 'Gempa';
  const dist = S.pos && q.lat != null ? ` · ± ${fmtDistance(haversine(S.pos.lat, S.pos.lon, q.lat, q.lon))} dari Anda` : '';
  return `<div class="quake"><strong>Gempa terbaru ${esc(mag)}</strong> — ${esc(q.region || '')}<br>
    <span class="small">${esc(q.local_time || '')}${dist} · ${esc(q.potensi || '')}</span><br><span class="small muted">Sumber: BMKG</span></div>`;
}

function renderState() {
  const el = $('#stateCard');
  const a = activeAlert();
  const sa = S.state && S.state.alert;
  const ph = S.phase;
  let cls = 'info', h = '';
  if (S.simPending) {
    const left = Math.max(0, Math.ceil((S.simPending.fireAt - Date.now()) / 1000));
    cls = 'warn';
    h = `<div class="state-title">${icon('bell')} Simulasi akan dimulai</div>
      <div class="simcount" aria-live="off">${left}</div>
      ${S.simPending.push
        ? '<p><strong>Kunci layar HP Anda sekarang.</strong> Notifikasi peringatan (berlabel SIMULASI) akan masuk seperti kejadian nyata.</p>'
        : `<p><strong>Tetap buka layar ini.</strong> Peringatan muncul saat hitungan habis.</p><p class="small muted">${esc(S.pushMsg || 'Agar notifikasi tetap masuk saat layar terkunci, buka SEMPAT lewat alamat https lalu izinkan notifikasi.')}</p>`}
      <button class="btn" data-act="cancelsim">Batalkan</button>`;
  } else if (ph === 'EVAKUASI') {
    cls = 'danger';
    const sim = a && a.source === 'SIMULASI';
    h = `<div class="state-title">${icon('warning')} ${a ? esc(a.headline || 'PERINGATAN DINI TSUNAMI') : 'MODE EVAKUASI MANDIRI'}</div>
      ${sim ? `<p><span class="tag sim-dark">SIMULASI${a.self ? ' DI LOKASI ANDA' : ''} — bukan kejadian nyata</span></p>` : ''}
      ${a && a.level ? `<p><span class="lvl ${esc(a.level)}">${esc(LEVEL_TXT[a.level] || a.level)}</span>${a.wave_m ? ` <span class="small">perkiraan tinggi ± ${esc(String(a.wave_m).replace('.', ','))} m</span>` : ''}</p>` : ''}
      <p><strong>Pergi sekarang ke tempat tinggi. Jauhi pantai dan sungai.</strong> Jangan menunggu air laut surut. Jangan kembali sampai peringatan dicabut.</p>
      ${a ? `<p class="small muted">${esc(alertLine(a))} · ${esc(a.source === 'BMKG' ? 'Sumber: BMKG' : a.source)}</p>` : '<p class="small muted">Anda memulai evakuasi karena merasakan gempa kuat. Ini langkah yang benar.</p>'}
      ${!S.pos || S.locating ? `<p><strong>${S.locating ? 'Menyiapkan peta di sekitar Anda…' : 'Lokasi belum diketahui.'}</strong> Sementara itu: berjalan menjauhi pantai, naik ke tanah setinggi mungkin atau lantai 3 ke atas gedung beton yang kokoh.</p>` : ''}
      ${a && a.self ? '<button class="btn" data-act="endsim">Akhiri simulasi</button>' : ''}
      ${!a && S.selfEvac ? '<button class="btn" data-act="endself">Akhiri mode evakuasi mandiri</button>' : ''}`;
  } else if (ph === 'AMAN') {
    cls = 'safe';
    const inShelter = S.atShelter;
    const z = zoneAt(S.pos);
    h = `<div class="state-title">${icon('check')} ${z && z.safe && !S.route ? 'Lokasi Anda aman dari tsunami' : 'Anda di tempat aman'}</div>
      ${a && a.source === 'SIMULASI' ? `<p><span class="tag sim-dark">SIMULASI${a.self ? ' DI LOKASI ANDA' : ''}</span></p>` : ''}
      ${inShelter && inShelter.type === 'tanah_tinggi' ? `<p><strong>Anda di tanah lebih tinggi dari perkiraan tsunami (± ${esc(Math.round(inShelter.elev))} m).</strong> Bila masih bisa, terus naik atau menjauh dari pantai dan sungai. Jangan turun sampai peringatan dicabut.</p>`
        : inShelter ? `<p><strong>Naik minimal ke lantai ${esc(inShelter.min_floor || 3)} ${esc(inShelter.name)} — lebih tinggi lebih baik${inShelter.levels ? ` (gedung ${inShelter.levels} lantai)` : ''}.</strong> Jangan ke basement. Jangan turun sampai peringatan dicabut.</p>`
        : '<p><strong>Tetap di sini sampai peringatan dicabut.</strong> Jangan kembali ke pantai atau rumah dulu — gelombang bisa datang lebih dari sekali.</p>'}
      ${zoneHTML()}
      <p class="small muted">Butuh bantuan medis, air, atau tempat berteduh? Lihat posko terdekat di bawah.</p>
      <div class="row">${S.manualSafe === evacKey() ? '<button class="btn" data-act="unsafe">Ternyata saya belum aman</button>' : ''}
      ${a && a.self ? '<button class="btn" data-act="endsim">Akhiri simulasi</button>' : ''}
      ${!a && S.selfEvac ? '<button class="btn" data-act="endself">Akhiri mode evakuasi mandiri</button>' : ''}</div>`;
  } else if (ph === 'WASPADA') {
    cls = 'warn';
    const z = zoneAt(S.pos);
    const sim = a && a.source === 'SIMULASI';
    const near = z && z.coast < 1e8 ? ` Anda ± ${esc(fmtDistance(z.coast))} dari pantai.` : '';
    h = `<div class="state-title">${icon('warning')} ${esc(a.headline || 'PERINGATAN DINI TSUNAMI — WASPADA')}</div>
      ${sim ? `<p><span class="tag sim-dark">SIMULASI${a.self ? ' DI LOKASI ANDA' : ''} — bukan kejadian nyata</span></p>` : ''}
      <p><span class="lvl waspada">${LEVEL_TXT.waspada}</span></p>
      <p><strong>Jauhi pantai dan tepi sungai/muara sekarang.</strong> Tidak perlu mengungsi ke bukit atau gedung tinggi.${near}</p>
      <p>Pantau informasi resmi — status bisa naik menjadi Siaga/Awas.</p>
      <p class="small muted">${esc(alertLine(a))} · ${esc(a.source === 'BMKG' ? 'Sumber: BMKG' : a.source)}</p>
      ${a.self ? '<button class="btn" data-act="endsim">Akhiri simulasi</button>' : ''}
      ${STEP0('Merasakan gempa kuat atau lama?')}`;
  } else if (ph === 'INFO') {
    cls = 'warn';
    h = `<div class="state-title">${icon('info')} Info gempa — tidak berpotensi tsunami</div>
      <p>${esc(alertLine(sa))}</p>
      <p>Waspadai gempa susulan. Jauhi bangunan yang retak.</p>
      <p class="small muted">${esc(sa.source === 'BMKG' ? 'Sumber: BMKG' : sa.source)} · ${clockTime(sa.issued_at)}</p>
      ${STEP0()}`;
  } else if (ph === 'PEMULIHAN') {
    const l = S.state.last_alert;
    h = `<div class="state-title">${icon('info')} Peringatan telah dicabut</div>
      <p>Dicabut pukul ${clockTime(l.ended_at)}${l.ended_by ? ` oleh ${esc(l.ended_by)}` : ''}.${l.ended_note ? ` Catatan: “${esc(l.ended_note)}”` : ''}</p>
      <p>Pulang hanya jika petugas menyatakan lingkungan Anda aman. Hindari jalan dan jembatan yang rusak.</p>
      ${STEP0('Gempa susulan kuat atau lama?')}`;
  } else {
    cls = 'safe';
    const bm = S.meta && S.meta.bmkg;
    h = `<div class="state-title">${icon('check')} Tidak ada peringatan tsunami aktif</div>
      <p class="small muted">${bm && bm.enabled ? `Memantau data gempa terbuka BMKG${bm.last_check ? ` · diperiksa ${timeAgo(bm.last_check, now())}` : ''}.` : 'Server ini tidak memantau BMKG. Peringatan dikirim oleh petugas.'}</p>
      ${!S.pos ? `<div class="zone unknown">${icon('locate')}<span>Di mana Anda? ${S.src === 'gps' ? esc(S.gpsMsg || 'Menunggu GPS…') : 'Ketuk peta di bawah atau pilih lokasi contoh.'}</span></div>
        ${S.src !== 'gps' && window.isSecureContext ? '<button class="btn" data-act="usegps">Pakai GPS ponsel</button>' : ''}` : ''}
      ${S.locating ? '<p class="loc-status"><span class="spinner"></span> Mengunduh peta jalan &amp; ketinggian di sekitar Anda…</p>' : ''}
      ${zoneHTML()}
      <div class="row">
        <button class="btn primary" data-act="trysim">${icon('bell')} Coba simulasi di lokasi saya</button>
        <button class="btn" data-act="preview">${S.preview ? 'Sembunyikan rute' : 'Kenali rute saya'}</button>
      </div>
      ${STEP0()}
      ${notifHTML()}
      ${quakeHTML()}`;
  }
  el.className = `card state ${cls}`;
  setHTML(el, h);
}

function alertLine(a) {
  if (!a) return '';
  const mag = a.magnitude != null ? `M${Number(a.magnitude).toFixed(1).replace('.', ',')}` : '';
  return [`Gempa ${mag}`.trim(), a.region, a.issued_at ? `pukul ${clockTime(a.issued_at)}` : ''].filter(Boolean).join(' — ');
}

// ------------------------------------------------------------------ tampilan: navigasi
function renderNav() {
  const card = $('#navCard');
  const show = !!(S.pos && !S.simPending && (S.phase === 'EVAKUASI' || S.preview || S.target) && (areaReady() || S.locating));
  card.classList.toggle('hidden', !show);
  $('#slowToggle').checked = S.group === 'rentan';
  if (!show) return;
  const r = S.route;
  const actions = [];
  $('#navWarn').classList.add('hidden');
  if (S.locating || !areaReady()) {
    $('#navDist').innerHTML = '<span class="spinner"></span>';
    $('#navTime').textContent = 'Mengunduh peta jalan & ketinggian di sekitar Anda…';
    $('#navDir').textContent = 'Sekali saja, ± 10–30 detik. Sementara itu, bergerak menjauhi pantai.';
    $('#navTurn').textContent = ''; $('#navDest').textContent = '';
    setVerdict(null, true); setHTML($('#navActions'), '');
    return;
  }
  if (!r || !r.ok) {
    $('#navDist').textContent = 'Rute tidak ditemukan';
    $('#navTime').textContent = r && r.reason ? r.reason : '';
    $('#navDir').textContent = S.phase === 'EVAKUASI' ? 'Naik ke lantai teratas gedung beton kokoh terdekat atau ke tanah tertinggi.' : '';
    $('#navTurn').textContent = ''; $('#navDest').textContent = '';
    setVerdict(null);
    if (S.target) actions.push('<button class="btn" data-act="stoptarget">Kembali</button>');
    setHTML($('#navActions'), actions.join(''));
    renderVertical();
    return;
  }
  if (S.target && S.target.arrived) {
    $('#navDist').textContent = 'Sudah sampai';
    $('#navTime').textContent = S.target.name;
    $('#navDir').textContent = 'Lapor ke petugas posko.';
    $('#navTurn').textContent = ''; $('#navDest').textContent = '';
    setVerdict(null, true);
    setHTML($('#navActions'), '<button class="btn" data-act="stoptarget">Selesai</button>');
    return;
  }
  const hd = headingToRoute(r.coords);
  if (hd != null) {
    const rel = S.heading != null ? hd - S.heading : hd;
    $('#arrowSvg').style.transform = `rotate(${rel}deg)`;
    $('#navDir').textContent = S.heading != null ? 'Ikuti panah.' : `Berjalan ke arah ${compassWord(hd)}. Panah mengikuti peta (utara di atas).`;
  } else $('#navDir').textContent = '';
  $('#navDist').textContent = fmtDistance(r.distM);
  $('#navTime').innerHTML = `<strong>± ${esc(fmtMinutes(r.timeS))}</strong> jalan kaki${S.group === 'rentan' ? ' (kecepatan lansia/difabel)' : ''}`;
  const t = nextTurn(r.coords.slice(1));
  $('#navTurn').textContent = t && t.distM < r.distM - 15 ? `Belok ${t.dir} dalam ${fmtDistance(t.distM)}` : 'Lurus terus sampai tujuan';
  const d = r.dest;
  let dest;
  if (d.type === 'tanah_tinggi') {
    dest = `Tujuan: <strong>tanah lebih tinggi terdekat</strong> (± ${esc(Math.round(d.elev))} m) — di atas perkiraan tsunami + galat peta. Pilihan darurat: terus naik bila bisa.`;
  } else if (d.type === 'shelter' || d.type === 'gedung') {
    dest = `Tujuan: <strong>${esc(d.name)}</strong> — ${d.type === 'shelter' ? 'gedung evakuasi resmi' : 'gedung bertingkat (pilihan darurat)'}, <strong>naik minimal ke lantai ${esc(d.min_floor || 3)}</strong>${d.levels ? ` dari ${d.levels} lantai` : ''}. Jangan ke basement.
      <br><span class="srcnote">${d.verified ? 'Diverifikasi petugas' : `Belum diverifikasi petugas — ${esc(SRC_TXT[d.levels_src] || 'data peta')}`}</span>`;
  }
  else if (d.type === 'safe_zone') dest = `Tujuan: <strong>tempat aman</strong>${d.elev != null ? ` (ketinggian ± ${Math.round(d.elev)} m)` : ''} — terus naik bila masih bisa.`;
  else dest = `Tujuan: <strong>${esc(d.name)}</strong> · ${esc(KIND_LABEL[d.kind] || '')} <span class="chip ${esc(d.status)}">${esc(STATUS_LABEL[d.status] || d.status)}</span>`;
  $('#navDest').innerHTML = dest;
  if (S.phase === 'EVAKUASI') setVerdict(r.timeS);
  else if (S.preview) setVerdict(null, false, 'Ini rute latihan. Coba jalani bersama keluarga agar hafal.');
  else setVerdict(null, true);
  if (S.target) {
    actions.push('<button class="btn" data-act="stoptarget">Batalkan arah ke posko</button>');
    if (r.src === 'tabel' && S.target.id && r.dest.id !== S.target.id) {
      $('#navWarn').textContent = 'Tidak terhubung ke server: diarahkan ke posko terdekat menurut data terakhir.';
      $('#navWarn').classList.remove('hidden');
    }
  }
  if (S.phase === 'EVAKUASI') actions.push('<button class="btn" data-act="manualsafe">Saya sudah di tempat aman</button>');
  setHTML($('#navActions'), actions.join(''));
  renderVertical();
}

function renderVertical() {
  const el = $('#navVertical');
  if (S.vertical && S.route && S.route.vertical) {
    const hg = S.route.dest.type === 'tanah_tinggi';
    setHTML(el, `<div class="vbox"><h3>${hg ? 'Menuju tanah lebih tinggi' : 'Menuju gedung'} — pilihan darurat</h3>
      <p class="small" style="margin:0 0 .4rem">${hg ? 'Hanya bila tidak sempat ke zona aman. Setelah sampai, terus naik bila masih bisa.' : 'Hanya bila tidak sempat ke tanah tinggi. Begitu sampai, langsung naik tangga — jangan pakai lift.'}</p>
      <button class="btn" data-act="vertical-off">Kembali ke rute tanah tinggi</button></div>`);
    return;
  }
  const o = verticalOffer();
  if (!o) { setHTML(el, ''); return; }
  const d = o.g.dest;
  if (d.type === 'tanah_tinggi') {
    setHTML(el, `<div class="vbox"><h3>${o.strong ? 'Tidak sempat ke tempat aman?' : 'Pilihan darurat bila waktu habis'}</h3>
      <p style="margin:0"><strong>Tanah lebih tinggi terdekat</strong> (± ${esc(Math.round(d.elev))} m) · ± ${esc(fmtMinutes(o.g.timeS))} · ${esc(fmtDistance(o.g.distM))}</p>
      <p class="srcnote" style="margin:.2rem 0 .5rem">Di atas perkiraan tsunami ± ${esc(String(S.tables.scenario ? S.tables.scenario.wave_m : '').replace('.', ','))} m ditambah galat peta ketinggian. Tidak ada gedung layak yang lebih dekat.</p>
      <button class="btn ${o.strong ? 'danger' : ''}" data-act="vertical-on">${icon('arrow')} Arahkan ke sana</button></div>`);
    return;
  }
  const src = d.verified ? 'Diverifikasi petugas' : `Belum diverifikasi petugas (${esc(SRC_TXT[d.levels_src] || 'data peta')}) — periksa kondisi gedung`;
  setHTML(el, `<div class="vbox ${d.verified ? 'ver' : ''}"><h3>${o.strong ? 'Tidak sempat ke tanah tinggi?' : 'Pilihan darurat bila waktu habis'}</h3>
    <p style="margin:0"><strong>${esc(d.name)}</strong> · ± ${esc(fmtMinutes(o.g.timeS))} · ${esc(fmtDistance(o.g.distM))}</p>
    <p style="margin:.2rem 0"><strong>Naik minimal ke lantai ${esc(d.min_floor)}</strong> (gedung ${esc(d.levels)} lantai). Lebih tinggi lebih baik.</p>
    <p class="srcnote" style="margin:0 0 .5rem">${src}</p>
    <button class="btn ${o.strong ? 'danger' : ''}" data-act="vertical-on">${icon('arrow')} Arahkan ke gedung ini</button></div>`);
}

function setVerdict(routeS, hide = false, text = null) {
  const v = $('#navVerdict');
  if (hide) { v.classList.add('hidden'); return; }
  v.classList.remove('hidden');
  if (text) { v.className = 'verdict unknown'; v.textContent = text; return; }
  const a = activeAlert();
  const rem = a ? remainingSeconds(a, now()) : null;
  const vd = routeS == null ? { level: 'unknown', text: 'Rute belum tersedia.' } : verdict(routeS, rem);
  if (S.route && S.route.vertical && rem != null) {
    if (vd.level === 'late' && rem > 0) vd.text = 'Waktu sangat mepet. Bila gelombang terlihat, masuk gedung beton terdekat mana pun dan naik setinggi mungkin.';
    else if (vd.level === 'ok') vd.text = 'Anda diperkirakan sempat sampai. Terus bergerak, lalu naik.';
  }
  v.className = `verdict ${vd.level}`;
  v.innerHTML = `${icon(vd.level === 'ok' ? 'check' : vd.level === 'unknown' ? 'info' : 'warning')}<span>${esc(vd.text)}</span>`;
}

function renderCountdown() {
  const el = $('#countdown');
  const a = activeAlert();
  const show = S.phase === 'EVAKUASI' || (S.phase === 'AMAN' && evacKey());
  el.classList.toggle('hidden', !show);
  if (!show) return;
  if (a) {
    const rem = remainingSeconds(a, now());
    const sim = a.source === 'SIMULASI' ? '<span class="tag sim">SIMULASI</span>' : '';
    if (rem == null) setHTML(el, `<span>Waktu tiba gelombang belum diketahui</span>${sim}`);
    else if (rem > 0) setHTML(el, `<span>Gelombang tiba ± pukul ${etaClock(a)}</span><span>sisa <strong style="font-size:1.25rem">${mmss(rem)}</strong></span>${sim}`);
    else setHTML(el, `<span>Perkiraan waktu tiba sudah lewat — tetap di tempat tinggi</span>${sim}`);
  } else setHTML(el, '<span>Evakuasi mandiri — waktu tiba gelombang belum diketahui. Bergegas.</span>');
}

// ------------------------------------------------------------------ tampilan: lokasi
function renderLoc() {
  $$('#locCard [data-src]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.src === (S.src === 'qr' ? 'sim' : S.src))));
  let st = '', hint = '';
  if (S.src === 'gps') {
    st = S.pos ? `GPS · akurasi ± ${Math.round(S.acc || 0)} m` : 'Mencari sinyal GPS…';
    hint = S.gpsMsg;
  } else if (S.src === 'qr') {
    st = 'Lokasi dari kode QR'; hint = 'Lokasi diambil dari kode QR. Ketuk peta untuk mengubah.';
  } else {
    st = S.pos ? 'Lokasi dipilih di peta' : 'Belum ada lokasi';
    hint = 'Ketuk peta di lokasi mana pun di Indonesia, atau coba lokasi pesisir:';
  }
  if (S.locating) st = 'Menyiapkan peta sekitar…';
  if (S.locErr) hint = S.locErr;
  $('#locStatus').innerHTML = S.locating ? `<span class="spinner"></span> ${esc(st)}` : esc(st);
  $('#locHint').textContent = hint;
  const pre = $('#presets');
  const showPre = S.src !== 'gps';
  pre.classList.toggle('hidden', !showPre);
  if (showPre) setHTML(pre, PRESETS.map((p, i) => `<button type="button" class="btn" data-act="preset" data-i="${i}">${esc(p.name)}</button>`).join('')
    + (S.defaultArea ? `<button type="button" class="btn" data-act="presetdemo">${esc(S.defaultArea.synthetic ? 'Desa sintetis (uji)' : S.defaultArea.name)}</button>` : ''));
  $('#areaName').textContent = S.area ? S.area.meta.name : '';
  const walkable = S.src !== 'gps' && areaReady() && S.route && S.route.ok && !(S.target && S.target.arrived) && (S.phase === 'EVAKUASI' || S.target);
  $('#simRow').classList.toggle('hidden', !walkable && !S.walking);
  $('#walkBtn').textContent = S.walking ? 'Hentikan simulasi berjalan' : 'Simulasikan berjalan';
  $('#recenter').classList.toggle('hidden', S.follow || !S.pos);
}

// ------------------------------------------------------------------ tampilan: posko
function poskoItem(it, i, verified) {
  const d = it.dest;
  const svc = (d.services || []).map((s) => `<span class="svc">${esc(SERVICE_LABEL[s] || s)}</span>`).join('');
  return `<div class="item">
    <div class="head"><div><div class="title">${i === 0 && verified ? 'Terdekat: ' : ''}${esc(d.name)}</div>
      <div class="small muted">${esc(KIND_LABEL[d.kind] || 'Titik bantuan')}${d.capacity ? ` · kapasitas ${d.capacity} orang` : ''}</div></div>
      <span class="chip ${esc(d.status)}">${esc(STATUS_LABEL[d.status] || d.status)}</span></div>
    <div><strong>± ${esc(fmtMinutes(it.timeS))}</strong> jalan kaki · ${esc(fmtDistance(it.distM))}</div>
    ${svc ? `<div class="services">${svc}</div>` : ''}
    ${verified && d.updated_at ? `<div class="small muted">Status diperbarui petugas ${esc(timeAgo(d.updated_at, now()))}</div>` : ''}
    ${!verified ? '<div class="small muted">Dari peta OpenStreetMap — belum diverifikasi petugas sebagai posko.</div>' : ''}
    <button class="btn ${i === 0 && verified ? 'safe' : ''}" data-act="go" data-id="${esc(d.id)}" data-name="${esc(d.name)}">${icon('pin')} Arahkan ke sini</button>
  </div>`;
}

function renderPosko() {
  const card = $('#poskoCard');
  const show = ['AMAN', 'PEMULIHAN', 'INFO'].includes(S.phase) && !S.target && !S.simPending;
  card.classList.toggle('hidden', !show);
  if (!show) return;
  const list = $('#poskoList');
  if (!areaReady()) { setHTML(list, '<p class="muted">Tentukan lokasi Anda dulu untuk melihat posko terdekat.</p>'); return; }
  let items = [], note = '';
  if (!S.poskoOffline && S.posko) {
    if (!S.posko.ok && S.posko.code === 'in_hazard') {
      setHTML(list, `<div class="verdict tight">${icon('warning')}<span>${S.atShelter ? 'Anda di gedung evakuasi di dalam zona bahaya. Tetap di lantai atas sampai peringatan dicabut — jangan berjalan ke posko dulu.' : esc(S.posko.reason)}</span></div>`);
      return;
    }
    items = (S.posko.routes || []).map((r) => ({ dest: r.dest, timeS: r.time_s, distM: r.distance_m }));
  } else {
    const t = S.tables ? routeFromTable(S.area, S.idx, S.tables, 'posko', S.group, S.pos) : { ok: false };
    if (t.ok) items = [{ dest: t.dest, timeS: t.timeS, distM: t.distM }];
    note = `<p class="small muted">${icon('offline')} Offline — menampilkan tujuan terdekat dari data terakhir${S.tables ? ` (${clockTime(S.tables.generated_at)})` : ''}.</p>`;
  }
  const ver = items.filter((x) => x.dest.verified !== false).slice(0, 3);
  const osm = items.filter((x) => x.dest.verified === false).slice(0, 3);
  let html = note;
  if (ver.length) html += ver.map((it, i) => poskoItem(it, i, true)).join('');
  else {
    const closed = ((S.state && S.state.points) || []).filter((p) => ['penuh', 'tutup'].includes(p.status)).length;
    html += `<p>Belum ada posko dari petugas yang buka dan bisa dijangkau aman dari lokasi Anda.${closed ? ` (${closed} titik lain sedang penuh/tutup.)` : ''}</p>`;
  }
  if (osm.length) html += `<h3 style="margin:.4rem 0 0;font-size:1rem">Fasilitas umum terdekat (peta OSM)</h3>` + osm.map((it, i) => poskoItem(it, i, false)).join('');
  setHTML(list, html);
}

// ------------------------------------------------------------------ peta
function renderMap() {
  const m = S.m; if (!m) return;
  m.setMe(S.pos, S.src === 'gps' ? S.acc : null);
  const r = S.route;
  const showRoute = areaReady() && r && r.ok && (S.phase === 'EVAKUASI' || S.preview || S.target);
  m.setRoute(showRoute ? r.coords : null);
  m.setDest(showRoute ? r.dest : null);
  // Gedung evakuasi darurat di sekitar (maks. 15 terdekat, 2 km) selama evakuasi.
  let list = [];
  if (S.phase === 'EVAKUASI' && areaReady() && S.tables && S.tables.dests && S.tables.dests.gedung) {
    list = S.tables.dests.gedung.filter((g) => g.lat != null).map((g) => ({ g, d: haversine(S.pos.lat, S.pos.lon, g.lat, g.lon) }))
      .filter((x) => x.d < 2000).sort((a, b) => a.d - b.d).slice(0, 15).map((x) => x.g);
  }
  const key = list.map((g) => `${g.id}:${g.min_floor}`).join(',');
  if (key !== S.gedungKey) {
    S.gedungKey = key;
    m.setGedung(list, (g) => popup({ ...g, kind: null }, `<br>Naik minimal ke <strong>lantai ${esc(g.min_floor)}</strong> (gedung ${esc(g.levels)} lantai)<br><span style="color:#4b5563">${g.verified ? 'Diverifikasi petugas' : esc('Belum diverifikasi — ' + (SRC_TXT[g.levels_src] || 'data peta'))}</span>`));
  }
}

function popup(p, extra = '') {
  new maplibregl.Popup({ closeButton: true, maxWidth: '260px' }).setLngLat([p.lon, p.lat])
    .setHTML(`<strong>${esc(p.name)}</strong><br>${esc(KIND_LABEL[p.kind] || '')}${p.status && p.status !== 'osm' ? ` · ${esc(STATUS_LABEL[p.status] || p.status)}` : ''}${(p.services || []).length ? `<br><span style="color:#4b5563">${p.services.map((s) => esc(SERVICE_LABEL[s] || s)).join(', ')}</span>` : ''}${extra}`)
    .addTo(S.m.map);
}

// ------------------------------------------------------------------ peringatan layar penuh
function maybeOverlay() {
  const a = activeAlert();
  const ov = $('#overlay');
  if (!(a && S.ack !== a.id)) { ov.classList.add('hidden'); return; }
  if (!ov.classList.contains('hidden') && ov.dataset.id === a.id) return;
  const sim = a.source === 'SIMULASI';
  const wsp = a.level === 'waspada';
  ov.dataset.id = a.id;
  ov.className = 'alert-overlay' + (sim ? ' sim' : '') + (wsp ? ' waspada' : '');
  ov.innerHTML = `
    ${sim ? `<span class="tag sim">SIMULASI${a.self ? ' DI LOKASI ANDA' : ''} — BUKAN KEJADIAN NYATA</span>` : ''}
    <h1 id="ovTitle">${icon('warning')} ${esc(a.headline || 'PERINGATAN DINI TSUNAMI')}</h1>
    <p class="meta">${esc(alertLine(a))}</p>
    <p class="action" id="ovAction">${esc(a.instruction || 'Segera menjauh dari pantai dan naik ke tempat tinggi.')}</p>
    ${a.eta_min != null ? `<p class="meta">Perkiraan gelombang tiba ± pukul <strong>${etaClock(a)}</strong> (± ${Math.round(a.eta_min)} menit setelah gempa).</p>` : ''}
    <span class="source">${esc(a.source === 'BMKG' ? 'Sumber: BMKG' : 'Sumber: simulasi/latihan')}</span>
    ${a.level ? `<p class="meta"><strong>Tingkat: ${esc(LEVEL_TXT[a.level] || a.level)}</strong></p>` : ''}
    <button class="btn xl block" id="ovGo">${wsp ? `${icon('check')} Saya mengerti — menjauh dari pantai` : `${icon('arrow')} Tunjukkan jalan ke tempat aman`}</button>`;
  $('#ovGo').onclick = () => {
    S.ack = a.id; store.set('sempat_ack', a.id);
    ov.classList.add('hidden');
    enableCompass();
    if (!S.pos && S.src === 'gps') startGPS();
    S.follow = true; if (S.pos && S.m) S.m.follow(S.pos, 16);
    update();
    $('#navCard').scrollIntoView({ block: 'start' });
  };
  $('#ovGo').focus();
  vibrate(wsp ? [400, 200, 400] : [700, 250, 700, 250, 700]);
  if (!wsp) siren();
}

function vibrate(p) { try { navigator.vibrate && navigator.vibrate(p); } catch { /* abaikan */ } }

let audioCtx = null;
function unlockAudio() {
  try { if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)(); if (audioCtx.state === 'suspended') audioCtx.resume(); } catch { /* abaikan */ }
}
function siren() {
  try {
    unlockAudio(); if (!audioCtx) return;
    const t0 = audioCtx.currentTime, o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = 'square';
    for (let i = 0; i < 6; i++) { o.frequency.setValueAtTime(880, t0 + i * 0.5); o.frequency.linearRampToValueAtTime(600, t0 + i * 0.5 + 0.45); }
    g.gain.setValueAtTime(0.08, t0); g.gain.setValueAtTime(0, t0 + 3);
    o.connect(g); g.connect(audioCtx.destination); o.start(t0); o.stop(t0 + 3.05);
  } catch { /* abaikan */ }
}

// ------------------------------------------------------------------ simulasi pribadi
async function trySim() {
  if (!S.pos) { toast('Tentukan lokasi Anda dulu (GPS atau ketuk peta).', true); return; }
  // minta izin notifikasi di ketukan ini (syarat browser)
  if (!S.pushSub && pushSupport().ok && 'Notification' in window && Notification.permission !== 'denied') {
    try { S.pushSub = await enablePush(S.meta && S.meta.push_key, S.pos); } catch (e) { S.pushMsg = e.message; }
  } else if (!S.pushSub && 'Notification' in window && Notification.permission === 'default') {
    try { await Notification.requestPermission(); } catch { /* abaikan */ }
  }
  const delay = 10;
  let sim = null, push = false;
  try {
    const r = await api.post('/api/sim/self', { lat: S.pos.lat, lon: S.pos.lon, delay_s: delay, endpoint: S.pushSub ? S.pushSub.endpoint : null });
    sim = r.sim; push = r.push;
  } catch {
    const at = new Date(now() + delay * 1000).toISOString();
    sim = { id: 'self-local-' + Date.now(), source: 'SIMULASI', event: 'Tsunami', magnitude: 7.5, region: 'lepas pantai terdekat dari lokasi Anda',
      tsunami: true, issued_at: at, eta_min: 20, headline: 'PERINGATAN DINI TSUNAMI', self: true,
      instruction: 'Segera menjauh dari pantai dan sungai, naik ke tempat tinggi atau lantai atas gedung kokoh. Jangan menunggu air laut surut. Jangan kembali sampai peringatan dicabut.' };
  }
  S.localSim = sim; store.set('sempat_localsim', sim);
  S.simPending = { sim, fireAt: Date.now() + delay * 1000, push };
  S.simTimer = setTimeout(() => fireSim(sim, push), delay * 1000);
  update();
}
function fireSim(sim, pushed) {
  if (!S.simPending || S.simPending.sim.id !== sim.id) return;
  S.simPending = null;
  if (!pushed) localNotify(`${sim.headline} — SIMULASI`, '[SIMULASI] Segera menjauh dari pantai, naik ke tempat tinggi. Ketuk untuk melihat arah.', sim.id);
  maybeOverlay(); update();
}
function endSim() {
  clearTimeout(S.simTimer);
  S.simPending = null; S.localSim = null; store.del('sempat_localsim');
  maybeOverlay(); update();
}

// ------------------------------------------------------------------ kompas & GPS
let compassOn = false;
function enableCompass() {
  if (compassOn || !('DeviceOrientationEvent' in window)) return;
  compassOn = true;
  const handler = (e) => {
    let h = null;
    if (typeof e.webkitCompassHeading === 'number') h = e.webkitCompassHeading;
    else if (e.absolute && e.alpha != null) h = (360 - e.alpha) % 360;
    if (h == null) return;
    const ang = (screen.orientation && screen.orientation.angle) || 0;
    S.heading = (h + ang) % 360;
    renderNav();
  };
  const add = () => { window.addEventListener('deviceorientationabsolute', handler); window.addEventListener('deviceorientation', handler); };
  if (typeof DeviceOrientationEvent.requestPermission === 'function') {
    DeviceOrientationEvent.requestPermission().then((r) => { if (r === 'granted') add(); }).catch(() => {});
  } else add();
}

function startGPS() {
  stopGPS();
  if (!('geolocation' in navigator)) { S.gpsMsg = 'Perangkat ini tidak mendukung GPS. Pilih lokasi di peta.'; update(); return; }
  if (!window.isSecureContext) {
    S.gpsMsg = 'Browser memblokir GPS pada alamat http:// selain localhost. Buka SEMPAT lewat alamat https://, atau pilih lokasi di peta.';
    update(); return;
  }
  S.gpsMsg = 'Mengaktifkan GPS…';
  let first = true;
  S.watchId = navigator.geolocation.watchPosition((p) => {
    S.acc = p.coords.accuracy;
    S.gpsMsg = S.acc > 100 ? 'Sinyal GPS lemah. Rute tetap dihitung, tetapi periksa posisi di peta.' : '';
    setPos({ lat: p.coords.latitude, lon: p.coords.longitude }, { center: first });
    first = false;
  }, (err) => {
    S.gpsMsg = err.code === 1 ? 'Izin lokasi ditolak. Aktifkan izin lokasi di pengaturan browser, atau pilih lokasi di peta.'
      : 'GPS belum mendapat posisi. Coba di tempat terbuka, atau pilih lokasi di peta.';
    update();
  }, { enableHighAccuracy: true, maximumAge: 3000, timeout: 20000 });
}
function stopGPS() { if (S.watchId != null) { navigator.geolocation.clearWatch(S.watchId); S.watchId = null; } }

function setSource(src) {
  stopWalk();
  S.src = src; store.set('sempat_src', src);
  if (src === 'gps') { S.pos = null; startGPS(); update(); return; }
  stopGPS(); S.gpsMsg = '';
  const p = store.get('sempat_simpos', null);
  if (p) setPos(p, { center: true }); else update();
}

// ------------------------------------------------------------------ simulasi berjalan
function startWalk() {
  if (S.walking) return;
  enableCompass();
  S.follow = true;
  S.walking = setInterval(() => {
    const r = S.route;
    if (!r || !r.ok || !(S.phase === 'EVAKUASI' || S.target) || (S.target && S.target.arrived)) { stopWalk(); update(); return; }
    const adv = advanceAlong(r.coords, SPEED[S.group] * 10 * 0.25);
    setPos({ lat: adv.lat, lon: adv.lon });
  }, 250);
  renderLoc();
}
function stopWalk() { if (S.walking) { clearInterval(S.walking); S.walking = null; } }

// ------------------------------------------------------------------ aksi
async function onAction(act, el) {
  unlockAudio();
  if (act === 'selfevac') {
    S.selfEvac = { at: new Date(now()).toISOString() }; store.set('sempat_selfevac', S.selfEvac);
    enableCompass();
    if (!S.pos && S.src === 'gps') startGPS();
    vibrate(300);
  } else if (act === 'endself') {
    const ok = await confirmDialog({ title: 'Akhiri evakuasi mandiri?', body: '<p>Akhiri hanya jika guncangan tidak kuat atau petugas menyatakan aman.</p>', okText: 'Akhiri' });
    if (!ok) return;
    S.selfEvac = null; store.del('sempat_selfevac');
  } else if (act === 'trysim') { await trySim(); return; }
  else if (act === 'cancelsim' || act === 'endsim') endSim();
  else if (act === 'preview') {
    S.preview = !S.preview;
    if (S.preview && !S.pos) toast('Tentukan lokasi Anda dulu (GPS atau ketuk peta).');
    if (S.preview && S.pos && S.m) { S.follow = true; S.m.follow(S.pos, 16); }
  } else if (act === 'notif') {
    try {
      S.pushSub = await enablePush(S.meta && S.meta.push_key, S.pos);
      toast(S.pushSub ? 'Notifikasi aktif — tetap masuk walau SEMPAT ditutup.' : 'Notifikasi aktif selama SEMPAT terbuka.');
      S.pushMsg = '';
    } catch (e) { S.pushMsg = e.message; toast(e.message, true); }
  } else if (act === 'testpush') {
    try { await api.post('/api/push/test', { endpoint: S.pushSub.endpoint }); toast('Notifikasi uji dikirim. Cek bilah notifikasi HP.'); }
    catch (e) { toast(e.message, true); }
  } else if (act === 'manualsafe') {
    const ok = await confirmDialog({ title: 'Sudah di tempat aman?', body: '<p>Pastikan Anda di tanah tinggi jauh dari pantai dan sungai, atau di lantai atas gedung beton kokoh (minimal lantai yang ditunjukkan SEMPAT).</p>', okText: 'Ya, saya sudah aman' });
    if (!ok) return;
    S.manualSafe = evacKey(); store.set('sempat_manualsafe', S.manualSafe); stopWalk();
  } else if (act === 'unsafe') {
    S.manualSafe = null; store.del('sempat_manualsafe');
  } else if (act === 'go') {
    S.target = { mode: 'posko', id: el.dataset.id, name: el.dataset.name, arrived: false };
    S.preview = false;
    await refreshPosko(true);
    $('#navCard').scrollIntoView({ block: 'start' });
  } else if (act === 'vertical-on') {
    S.vertical = true; S.follow = true; if (S.pos && S.m) S.m.follow(S.pos, 17);
  } else if (act === 'vertical-off') {
    S.vertical = false;
  } else if (act === 'stoptarget') {
    S.target = null; stopWalk();
  } else if (act === 'usegps') { setSource('gps'); return; }
  else if (act === 'preset') {
    const p = PRESETS[+el.dataset.i];
    S.src = 'sim'; store.set('sempat_src', 'sim'); stopGPS(); stopWalk();
    S.atShelter = null; S.target = null;
    await setPos({ lat: p.lat, lon: p.lon }, { center: true });
    return;
  } else if (act === 'presetdemo') {
    S.src = 'sim'; store.set('sempat_src', 'sim'); stopGPS(); stopWalk();
    await S.sync.setArea(S.defaultArea.id);
    const c = S.area.meta.center;
    const b = S.area.buildings.filter((x) => x.hazard && x.residents > 0)[0];
    await setPos(b ? { lat: b.lat, lon: b.lon } : { lat: c[1], lon: c[0] }, { center: true });
    return;
  }
  update();
}

// ------------------------------------------------------------------ pesan dari service worker (push)
function onSWMessage(ev) {
  const m = ev.data || {};
  const d = m.data || {};
  if (m.type === 'push') {
    if (d.type === 'sim' && d.alert) {
      if (!S.localSim || S.localSim.id !== d.alert.id) { S.localSim = d.alert; store.set('sempat_localsim', d.alert); }
      if (S.simPending && S.simPending.sim.id === d.alert.id) { clearTimeout(S.simTimer); S.simPending = null; }
      maybeOverlay(); update();
    } else if (d.type === 'quake') loadQuake();
    else S.sync && S.sync.refresh().then(() => { maybeOverlay(); update(); }).catch(() => {});
  }
}

async function loadQuake() {
  try { const r = await api.get('/api/quake'); S.quake = r.quake; if (S.meta) S.meta.bmkg = r.bmkg; renderState(); } catch { /* abaikan */ }
}

// ------------------------------------------------------------------ mulai
function renderConn(on, last) {
  S.online = on;
  $('#conn').innerHTML = on ? '<span class="dot"></span>Terhubung' : `<span class="dot off"></span>Offline${last ? ` · data ${clockTime(last)}` : ''}`;
  const b = $('#banners');
  const off = b.querySelector('.offline');
  if (!on && !off) b.insertAdjacentHTML('afterbegin', `<div class="banner offline">${icon('offline')}<span>Tidak terhubung. Panah dan rute tetap bekerja memakai data terakhir yang tersimpan di ponsel.</span></div>`);
  if (on && off) off.remove();
}

async function main() {
  registerSW();
  document.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-act]');
    if (b) { ev.preventDefault(); onAction(b.dataset.act, b); }
  });
  $('#slowToggle').addEventListener('change', (ev) => {
    S.group = ev.target.checked ? 'rentan' : 'umum'; store.set('sempat_group', S.group);
    S.poskoAt = 0; if (S.target || ['AMAN', 'PEMULIHAN', 'INFO'].includes(S.phase)) refreshPosko(true);
    update();
  });
  $$('#locCard [data-src]').forEach((b) => b.addEventListener('click', () => setSource(b.dataset.src)));
  $('#walkBtn').addEventListener('click', () => { if (S.walking) { stopWalk(); renderLoc(); } else startWalk(); });
  $('#recenter').addEventListener('click', () => { S.follow = true; if (S.pos) S.m.follow(S.pos, 16); renderLoc(); });
  if ('serviceWorker' in navigator) navigator.serviceWorker.addEventListener('message', onSWMessage);

  // Peta dasar langsung tampil (Indonesia), sebelum lokasi diketahui
  S.m = createMap($('#map'), null);
  S.m.map.on('dragstart', (e) => { if (e.originalEvent) { S.follow = false; renderLoc(); } });
  S.m.map.on('click', (ev) => {
    if (S.src === 'gps') { toast('Untuk memilih lokasi sendiri, ketuk “Pilih di peta”.'); return; }
    stopWalk();
    S.src = 'sim'; store.set('sempat_src', 'sim');
    S.atShelter = null; if (S.target) S.target.arrived = false;
    setPos({ lat: ev.lngLat.lat, lon: ev.lngLat.lng });
    refreshPosko(true);
  });

  S.sync = new Sync({
    areaId: false,
    onArea: (area, meta) => {
      const first = !S.area;
      S.area = area; S.meta = meta || S.meta; S.idx = new EdgeIndex(area);
      S.tables = null; S.posko = null; S.poskoPos = null;
      const demo = $('#banners').querySelector('.demo');
      if (area.meta.synthetic && !demo) $('#banners').insertAdjacentHTML('beforeend', `<div class="banner demo">${icon('info')}<span>Mode uji: <strong>desa sintetis</strong>, bukan desa nyata.</span></div>`);
      if (!area.meta.synthetic && demo) demo.remove();
      const src = (area.meta.sources || []).join(' · ');
      $('#foot').innerHTML = `Peta: ${esc(area.meta.name)} — ${esc(src)} · Peringatan resmi: BMKG · SEMPAT ${esc((meta && meta.version) || '')}<br>Perkiraan waktu adalah hasil model, bukan jaminan. Ikuti arahan petugas.`;
      S.m.setArea(area, { fit: first && !S.pos });
      S.m.setOfficial(area.official_points);
      S.m.setFacilities(area.facilities || [], (p) => popup(p, '<br><span style="color:#4b5563">Data OpenStreetMap, belum diverifikasi petugas</span>'));
      if (S.pos) S.m.follow(S.pos, 16);
    },
    onState: (st) => {
      if (st.area_id !== (S.area && S.area.meta.id)) return;
      if (!st._offline) S.offset = Date.parse(st.server_time) - Date.now();
      const prevAlert = S.state && S.state.alert;
      S.state = st;
      if (prevAlert && !st.alert) { toast('Peringatan telah dicabut.'); if (S.selfEvac) { S.selfEvac = null; store.del('sempat_selfevac'); } }
      if (S.selfEvac && now() - Date.parse(S.selfEvac.at) > 3 * 3600 * 1000) { S.selfEvac = null; store.del('sempat_selfevac'); }
      if (S.localSim && now() - Date.parse(S.localSim.issued_at) > 2 * 3600 * 1000) { S.localSim = null; store.del('sempat_localsim'); }
      S.m.setPoints(st.points.filter((p) => p.status !== 'tutup'), (p) => popup(p));
      S.m.setClosures(st.closures);
      maybeOverlay();
      update();
    },
    onTables: (tb) => { S.tables = tb; if (S.target || S.phase === 'AMAN') S.poskoAt = 0; update(); },
    onAlert: () => { maybeOverlay(); update(); },
    onConn: (on, last) => { renderConn(on, last); update(); },
    onQuake: (q) => { S.quake = q; renderState(); },
  });
  try {
    await S.sync.start();
    S.meta = S.sync.meta || S.meta;
    S.defaultArea = S.meta && S.meta.area ? { id: S.meta.area.id, name: S.meta.area.name, synthetic: !!S.meta.area.synthetic } : null;
    renderConn(S.sync.online, S.sync.lastSync);
  } catch (e) {
    $('#stateCard').innerHTML = `<div class="state-title">${icon('offline')} Tidak dapat memuat data</div><p>${esc(e.message)}</p><p><strong>Saat gempa kuat: langsung ke tempat tinggi, jauhi pantai dan sungai.</strong></p>`;
    return;
  }
  loadQuake();
  S.pushSub = await currentPushSub();

  // Sumber lokasi awal: kode QR > pilihan terakhir > GPS (bila https) > pilih di peta
  const q = new URLSearchParams(location.search);
  const qlat = parseFloat(q.get('lat')), qlon = parseFloat(q.get('lon'));
  const qAlert = q.get('alert');
  if (qAlert && S.localSim && S.localSim.id === qAlert) S.simPending = null;
  if (q.toString()) history.replaceState(null, '', location.pathname);
  if (isFinite(qlat) && isFinite(qlon)) { S.src = 'qr'; S.preview = true; await setPos({ lat: qlat, lon: qlon }, { center: true }); }
  else {
    const canGPS = window.isSecureContext && 'geolocation' in navigator;
    S.src = store.get('sempat_src', null) || (canGPS ? 'gps' : 'sim');
    const last = store.get('sempat_last_area', null);
    const sp = store.get('sempat_simpos', null);
    if (S.src === 'gps') startGPS();
    else if (sp) {
      if (last) { try { await S.sync.setArea(last); } catch { /* area lama sudah tidak ada */ } }
      await setPos(sp, { center: true });
    } else update();
  }
  maybeOverlay();
  if (isIOS() && !isStandalone() && !store.get('sempat_ios_hint')) {
    toast('Di iPhone: Bagikan → “Tambahkan ke Layar Utama” agar notifikasi peringatan bisa masuk.', false, 7000);
    store.set('sempat_ios_hint', 1);
  }
  setInterval(() => {
    if (S.simPending) { if (Date.now() >= S.simPending.fireAt) fireSim(S.simPending.sim, S.simPending.push); else renderState(); }
    if (S.phase === 'EVAKUASI' || S.phase === 'AMAN') { renderCountdown(); if (S.phase === 'EVAKUASI' && S.route) { setVerdict(S.route.ok ? S.route.timeS : null); renderVertical(); } }
    if (S.localSim && !S.simPending && Date.parse(S.localSim.issued_at) <= now() && S.phase === 'NORMAL') { maybeOverlay(); update(); }
  }, 1000);
  setInterval(() => { if (S.target || ['AMAN', 'PEMULIHAN', 'INFO'].includes(S.phase)) refreshPosko(); }, 15000);
  setInterval(loadQuake, 5 * 60 * 1000);
}

// Kait untuk uji otomatis/demo (tidak memengaruhi pemakaian biasa).
window.__sempat = { S, update, refreshPosko, setPos };
main();
