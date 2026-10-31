// Komponen bersama: API, sinkronisasi real-time, peta, ikon, notifikasi kecil.
/* global maplibregl */

// ---------------- Ikon (SVG inline, tidak bergantung internet) ----------------
const P = {
  warning: '<path d="M12 3 2 21h20L12 3Z" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"/><path d="M12 10v5" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="18" r="1.4" fill="currentColor"/>',
  check: '<circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="m7 12.5 3.2 3.2L17 9" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
  arrow: '<path d="M12 2 20 20 12 15.5 4 20Z" fill="currentColor"/>',
  pin: '<path d="M12 22s7-6.3 7-12a7 7 0 1 0-14 0c0 5.7 7 12 7 12Z" fill="none" stroke="currentColor" stroke-width="2.2"/><circle cx="12" cy="10" r="2.6" fill="currentColor"/>',
  offline: '<path d="M2 8.5a15 15 0 0 1 20 0M5.5 12a10 10 0 0 1 13 0M9 15.5a5 5 0 0 1 6 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="19" r="1.4" fill="currentColor"/><path d="M3 3l18 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
  info: '<circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M12 11v6" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="7.5" r="1.4" fill="currentColor"/>',
  plus: '<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>',
  road: '<path d="M8 3 5 21M16 3l3 18M12 5v2M12 11v2M12 17v2" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" fill="none"/>',
  x: '<path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>',
  people: '<circle cx="9" cy="8" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="17" cy="9" r="2.4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 14.2c2.8.3 5 2.6 5 5.8" fill="none" stroke="currentColor" stroke-width="2"/>',
  building: '<path d="M4 21V5l8-3 8 3v16M9 21v-5h6v5M8 8h2M14 8h2M8 12h2M14 12h2" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
  medical: '<rect x="3" y="3" width="18" height="18" rx="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 8v8M8 12h8" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>',
  locate: '<circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
  bell: '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M10 20a2 2 0 0 0 4 0" fill="none" stroke="currentColor" stroke-width="2"/>',
  up: '<path d="M12 20V5M5 11l7-7 7 7" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
  print: '<path d="M6 9V3h12v6M6 17H4v-6h16v6h-2M7 14h10v7H7z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
  logout: '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
};
export function icon(name, cls = '') {
  return `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${P[name] || ''}</svg>`;
}

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ---------------- Penyimpanan lokal (aman bila diblokir) ----------------
export const store = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* abaikan */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* abaikan */ } },
};

// ---------------- Toast ----------------
export function toast(msg, isErr = false, ms = 3500) {
  let wrap = $('.toast-wrap');
  if (!wrap) { wrap = document.createElement('div'); wrap.className = 'toast-wrap'; wrap.setAttribute('role', 'status'); wrap.setAttribute('aria-live', 'polite'); document.body.appendChild(wrap); }
  const t = document.createElement('div');
  t.className = 'toast' + (isErr ? ' err' : '');
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

// ---------------- API ----------------
export const api = {
  token: store.get('sempat_token'),
  user: store.get('sempat_user'),
  async req(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    let res;
    try {
      res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      throw new Error('Tidak dapat terhubung ke server. Periksa koneksi.');
    }
    // Respons dari salinan offline service worker ditandai agar UI jujur soal status koneksi.
    this.lastOffline = res.headers.get('X-Sempat-Offline') === '1';
    let data = null;
    try { data = await res.json(); } catch { /* bukan JSON */ }
    if (!res.ok) {
      if (res.status === 401 && this.token) { this.logout(); }
      const d = data && data.detail;
      const msg = typeof d === 'string' ? d : Array.isArray(d) ? 'Isian belum lengkap atau tidak valid.' : `Gagal (${res.status})`;
      const err = new Error(msg); err.status = res.status; throw err;
    }
    return data;
  },
  get(p) { return this.req('GET', p); },
  post(p, b = {}) { return this.req('POST', p, b); },
  patch(p, b = {}) { return this.req('PATCH', p, b); },
  put(p, b = {}) { return this.req('PUT', p, b); },
  del(p) { return this.req('DELETE', p); },
  async login(username, password) {
    const r = await this.post('/api/auth/login', { username, password });
    this.token = r.token; this.user = r.user;
    store.set('sempat_token', r.token); store.set('sempat_user', r.user);
    return r.user;
  },
  logout() { this.token = null; this.user = null; store.del('sempat_token'); store.del('sempat_user'); },
};

// ---------------- Sinkronisasi real-time ----------------
// Mengambil area, state, dan tabel rute untuk SATU area; mendengarkan SSE; jatuh ke polling bila SSE terputus.
export class Sync {
  constructor({ areaId = null, onArea, onAreaPatch, onState, onTables, onAlert, onConn, onQuake }) {
    Object.assign(this, { areaId, onArea, onAreaPatch, onState, onTables, onAlert, onConn, onQuake });
    this.patchStats = { patches: 0, full: 0, bytes: 0 };
    this.version = -1; this.tablesVersion = -1; this.online = navigator.onLine; this.lastSync = null;
  }
  q() { return this.areaId ? `?area=${encodeURIComponent(this.areaId)}` : ''; }
  async start() {
    this.meta = await api.get('/api/meta' + this.q()).catch(() => null);
    if (this.areaId !== false) {
      try { await this.loadArea(); } catch (e) {
        if (!this.areaId) throw e;
        // area tersimpan sudah tidak ada -> pakai area bawaan server
        this.areaId = null;
        this.meta = await api.get('/api/meta').catch(() => this.meta);
        await this.loadArea();
      }
    }
    this.openStream();
    this.poll = setInterval(() => this.refresh().catch(() => {}), 20000);
    window.addEventListener('online', () => { this.setConn(true); this.refresh().catch(() => {}); });
    window.addEventListener('offline', () => this.setConn(false));
  }
  async loadArea() {
    this.area = await api.get('/api/area' + this.q());
    this.areaId = this.area.meta.id;
    this.patchStats.full += 1;
    this.version = -1; this.tablesVersion = -1;
    this.onArea && this.onArea(this.area, this.meta);
    await this.refresh();
  }
  async setArea(id) {
    if (id === this.areaId && this.area) return;
    this.areaId = id;
    this.meta = await api.get('/api/meta' + this.q()).catch(() => this.meta);
    await this.loadArea();
  }
  // Tambalan area sejak revisi yang dimiliki klien. Server menjawab 409 bila tidak bisa dipastikan -> muat ulang.
  async patchArea() {
    if (this._patching) { this._patchAgain = true; return this._patching; }
    this._patching = (async () => {
      do {
        this._patchAgain = false;
        const m = this.area.meta;
        let p;
        try {
          p = await api.get(`/api/area/patch?since=${m.rev}&epoch=${encodeURIComponent(m.epoch || '')}` + (this.areaId ? `&area=${encodeURIComponent(this.areaId)}` : ''));
        } catch (e) {
          if (e.status === 409 || e.status === 422) { await this.loadArea(); return; }
          throw e;
        }
        if (api.lastOffline || p.area !== this.areaId || p.epoch !== m.epoch) { await this.loadArea(); return; }
        const B = this.area.buildings;
        const ix = new Map(B.map((b, i) => [b.id, i]));
        for (const b of p.buildings) { const i = ix.get(b.id); if (i != null) B[i] = b; else B.push(b); }
        if (p.removed.length) {
          const gone = new Set(p.removed);
          this.area.buildings = B.filter((b) => !gone.has(b.id));
        }
        m.rev = p.rev;
        if (m.view === 'publik') m.buildings_sent = this.area.buildings.length;
        this.patchStats.patches += 1;
        this.patchStats.bytes += JSON.stringify(p).length;
        if (this.onAreaPatch) this.onAreaPatch(this.area, p.buildings, p.removed);
        else this.onArea && this.onArea(this.area, this.meta);
        this.scheduleCacheRefresh();
      } while (this._patchAgain);
    })().finally(() => { this._patching = null; });
    return this._patching;
  }
  // Salinan offline (service worker) /api/area diperbarui di belakang, digabung untuk beberapa survei beruntun,
  // supaya saat internet putus HP tidak kembali ke data gedung lama. Tidak memicu render ulang.
  scheduleCacheRefresh(ms = 30000) {
    clearTimeout(this._cacheTimer);
    this._cacheTimer = setTimeout(() => {
      const headers = api.token ? { Authorization: `Bearer ${api.token}` } : {};
      fetch('/api/area' + this.q(), { headers }).catch(() => {});
    }, ms);
  }
  setConn(on) {
    if (on !== this.online) { this.online = on; this.onConn && this.onConn(on, this.lastSync); }
  }
  openStream() {
    if (!('EventSource' in window)) return;
    const es = new EventSource('/api/stream');
    es.addEventListener('hello', () => this.setConn(true));
    es.addEventListener('state', (ev) => {
      const d = JSON.parse(ev.data);
      if (this.area && (d.area == null || d.area === this.areaId) && d.version !== this.version) this.refresh().catch(() => {});
    });
    es.addEventListener('alert', (ev) => { const d = JSON.parse(ev.data); this.refresh().then(() => this.onAlert && this.onAlert(d)).catch(() => {}); });
    es.addEventListener('quake', (ev) => { this.onQuake && this.onQuake(JSON.parse(ev.data)); });
    // Data area berubah. Survei satu gedung -> tambalan kecil (hanya gedung yang berubah); perubahan menyeluruh
    // (peta bahaya resmi, tinggi gedung, grid penduduk) -> muat ulang area. Kamera tidak dipindah.
    es.addEventListener('area', (ev) => {
      const d = JSON.parse(ev.data);
      if (!this.area || d.area !== this.areaId) return;
      if (d.rev != null && d.epoch === this.area.meta.epoch && d.rev === this.area.meta.rev) return;
      (d.ids && d.epoch === this.area.meta.epoch ? this.patchArea() : this.loadArea()).catch(() => {});
    });
    es.onerror = () => this.setConn(false);
    es.onopen = () => this.setConn(true);
    this.es = es;
  }
  async refresh() {
    if (!this.area) return;
    try {
      const st = await api.get('/api/state' + this.q());
      st._offline = api.lastOffline;
      this.setConn(!st._offline);
      if (!st._offline) this.lastSync = new Date().toISOString();
      const changed = st.version !== this.version;
      this.version = st.version;
      this.state = st;
      this.onState && this.onState(st);
      if (changed || this.tablesVersion !== st.version) {
        const tb = await api.get('/api/tables' + this.q());
        this.tablesVersion = tb.version;
        this.tables = tb;
        this.onTables && this.onTables(tb);
      }
    } catch (e) {
      this.setConn(false);
      throw e;
    }
  }
}

// Apakah posisi tercakup area (live: minimal 600 m dari tepi; area desa: di dalam batasnya).
export function covers(meta, pos, margin = null) {
  if (!meta || !pos) return false;
  const [w, s, e, n] = meta.bbox;
  if (pos.lat < s || pos.lat > n || pos.lon < w || pos.lon > e) return false;
  const m = margin != null ? margin : (meta.live ? 600 : 0);
  const dy = Math.min(pos.lat - s, n - pos.lat) * 111320;
  const dx = Math.min(pos.lon - w, e - pos.lon) * 111320 * Math.cos(pos.lat * Math.PI / 180);
  return Math.min(dx, dy) >= m;
}

// ---------------- Peta ----------------
export const STATUS_LABEL = { tutup: 'Tutup', kosong: 'Kosong', agak_ramai: 'Agak ramai', ramai: 'Ramai', penuh: 'Penuh', osm: 'Belum diverifikasi' };
export const STATUS_COLOR = { tutup: '#b91c1c', kosong: '#166534', agak_ramai: '#1d4ed8', ramai: '#b45309', penuh: '#b91c1c' };
export const SERVICE_LABEL = { medis: 'Medis', air: 'Air bersih', makanan: 'Makanan', tempat_tidur: 'Tempat tidur', listrik: 'Listrik', komunikasi: 'Komunikasi' };
export const KIND_LABEL = { posko_sar: 'Posko SAR', titik_kumpul: 'Titik kumpul', posko_medis: 'Posko medis', dapur_umum: 'Dapur umum', pengungsian: 'Tempat pengungsian',
  faskes: 'Fasilitas kesehatan', polisi: 'Kantor polisi', damkar: 'Pemadam kebakaran', kantor_pemerintah: 'Kantor pemerintahan', balai_warga: 'Balai warga', titik_kumpul_osm: 'Titik kumpul (OSM)' };
export const AREA_KIND = (m) => (m.synthetic ? 'Desa sintetis (uji)' : m.kind === 'view' ? 'Area analisis' : m.live ? 'Peta otomatis' : 'Desa disiapkan');

// ---------------- Tingkat kepastian gedung (SATU definisi dari server: tsunami.verification) ----------------
// Warna + POLA garis + teks/simbol: status tidak pernah dibedakan oleh warna saja.
export const TIER = {
  verified: { color: '#166534', fill: '#bbf7d0', label: 'Terverifikasi', line: 'solid' },
  unverified: { color: '#b45309', fill: '#fde68a', label: 'Perlu Verifikasi', line: 'dashed' },
  unsafe: { color: '#b91c1c', fill: '#e5e7eb', label: 'Tidak Aman', line: 'dotted' },
};
// Pita waktu evakuasi 3 menit (proposal): 0 = 0–3 menit ... 10 = ≥ 30 menit. Urutan terang->gelap + teks legend.
const TIME_COLORS = ['#1a9850', '#66bd63', '#a6d96a', '#d9ef8b', '#fee08b', '#fdae61', '#f46d43', '#d73027', '#a50026', '#7f0026', '#4d0019'];
const POP_STEPS = [[0, '#fff5eb'], [2, '#fdd0a2'], [5, '#fd8d3c'], [10, '#d94801'], [20, '#7f2704']];

function roadsGeoJSON(area) {
  if (!area) return fc();
  const { u, v, kind } = area.edges; const { lat, lon, hazard, safe } = area.nodes;
  const feats = [];
  for (let e = 0; e < u.length; e++) {
    feats.push({ type: 'Feature', id: e, properties: { e, kind: kind[e], hazard: hazard[u[e]] || hazard[v[e]] ? 1 : 0, safe: safe[u[e]] && safe[v[e]] ? 1 : 0 },
      geometry: { type: 'LineString', coordinates: [[lon[u[e]], lat[u[e]]], [lon[v[e]], lat[v[e]]]] } });
  }
  return { type: 'FeatureCollection', features: feats };
}
// Tinggi 3D: jumlah lantai × tinggi per lantai area (params.height_per_level_m, sama dengan server). Jumlah lantai
// PERKIRAAN (dari tinggi gedung) diberi flag `est` dan digambar pucat; tanpa data lantai (default) TIDAK diekstrusi.
export function buildingsGeoJSON(area) {
  if (!area) return fc();
  const hpl = (area.params && area.params.height_per_level_m) || 4;
  const feats = [];
  area.buildings.forEach((b, i) => {
    if (!b.poly || b.poly.length < 3) return;
    const src = b.levels_src || 'osm';
    const lv = b.levels || 1;
    const est = !!b.levels_est || src === 'google';
    feats.push({ type: 'Feature', id: i,
      // tier = tsunami.verification di server (SATU definisi untuk peta, legend, dan detail ketiga peran)
      properties: { id: b.id, hazard: b.hazard ? 1 : 0, tier: b.tier || 'unverified', kind: b.kind, levels: lv, src,
        surveyed: b.surveyed || b.verified ? 1 : 0, prio: b.survey_prio ? 1 : 0, known: src !== 'default' ? 1 : 0, est: est ? 1 : 0,
        h: lv * hpl, base: Math.min(lv - 1, b.min_level || 0) * hpl, tall: lv >= 2 ? 1 : 0 },
      geometry: { type: 'Polygon', coordinates: [b.poly.concat([b.poly[0]])] } });
  });
  return { type: 'FeatureCollection', features: feats };
}
const fc = (features = []) => ({ type: 'FeatureCollection', features });
const lines = (arr) => fc((arr || []).map((l) => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: l } })));
const polys = (arr) => fc((arr || []).map((r) => ({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [r] } })));
// Grid "run" dari server (backend/mapgrid.py): [i, j, panjang, nilai] -> persegi panjang per run.
export function gridGeoJSON(g) {
  if (!g || !g.runs || !g.origin) return fc();
  const [lon0, lat0] = g.origin;
  return fc(g.runs.map(([i, j, n, v]) => {
    const w = lon0 + i * g.dlon, e = lon0 + (i + n) * g.dlon, s = lat0 + j * g.dlat, nn = lat0 + (j + 1) * g.dlat;
    return { type: 'Feature', properties: { v }, geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, nn], [w, nn], [w, s]]] } };
  }));
}
function popGeoJSON(c) {
  if (!c || !c.count) return fc();
  const h = (c.cell_deg || 1 / 3600) / 2;
  return fc(c.pop.map((p, k) => {
    const lo = c.lon[k], la = c.lat[k];
    return { type: 'Feature', properties: { p }, geometry: { type: 'Polygon', coordinates: [[[lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h], [lo - h, la - h]]] } };
  }));
}

function circle(pos, r, n = 40) {
  const k = 111320, kx = k * Math.cos(pos.lat * Math.PI / 180), ring = [];
  for (let i = 0; i <= n; i++) { const a = (i / n) * 2 * Math.PI; ring.push([pos.lon + (r * Math.cos(a)) / kx, pos.lat + (r * Math.sin(a)) / k]); }
  return ring;
}

const R2X = (window.devicePixelRatio || 1) > 1.5 ? '@2x' : '';
export const BASEMAPS = {
  carto: { tiles: ['a', 'b', 'c', 'd'].map((x) => `https://${x}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}${R2X}.png`), attribution: '© kontributor OpenStreetMap © CARTO' },
  osm: { tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], attribution: '© kontributor OpenStreetMap' },
};
export const Z3D = 16;  // zoom mulai bangunan 3D (2D di bawahnya)

// Kelompok lapisan untuk kontrol & legend. Setiap peran memilih kelompok yang tampil (preset), definisi tetap satu.
const sw = (style) => `<i class="lg-sw" style="${style}"></i>`;
function groupDefs(ctx) {
  const hv = (ctx.area && ctx.area.hazard_view) || {};
  return {
    rute: { label: 'Rute evakuasi', layers: ['route-case', 'route', 'alts'],
      legend: () => [[sw('background:#1d4ed8;height:5px'), 'Rute Anda'], [sw('border-top:3px dashed #6b7280;background:none;height:0'), 'Rute alternatif']] },
    bahaya: { label: 'Zona bahaya', layers: ['hz-rule', 'hz-off', 'hz-off-line'],
      legend: () => (hv.official
        ? [[sw('background:rgba(220,38,38,.25);border:2px solid #b91c1c'), esc(hv.label || 'Zona bahaya — peta resmi')]]
        : [[sw('background:repeating-linear-gradient(45deg,rgba(234,88,12,.35) 0 3px,transparent 3px 6px);border:1px dashed #c2410c'),
          `${esc(hv.label || 'Zona bahaya — perkiraan aturan')}`]]) },
    jalan: { label: 'Akses jalan & jalan putus', layers: ['roads', 'steps', 'closures'],
      legend: () => [[sw('background:#dc2626;opacity:.6;height:5px'), 'Jalan di zona bahaya'], [sw('background:#16a34a;opacity:.6;height:5px'), 'Jalan di zona aman'],
        [sw('border-top:4px dashed #b91c1c;background:none;height:0'), 'Jalan putus/terhalang (laporan petugas)']] },
    bangunan: { label: 'Bangunan (2D)', layers: ['bld'],
      legend: () => (ctx.buildingsColor === 'plan'
        ? [[sw('background:#86c99a'), 'Penghuni sempat'], [sw('background:#93b4f5'), 'Terlayani shelter'], [sw('background:#f3c46b'), 'Sebagian sempat'],
          [sw('background:#e0685e'), 'Tidak sempat'], [sw('background:#1e3a8a'), 'Shelter terdaftar'], [sw('background:#7c3aed'), 'Rekomendasi']]
        : ctx.buildingsColor === 'tier'
          ? [[sw(`background:${TIER.verified.fill}`), 'Isi = tingkat kepastian (lihat Kelayakan)']]
          : [[sw('background:rgba(239,196,191,.9)'), 'Bangunan di zona bahaya'], [sw('background:rgba(214,216,219,.9)'), 'Bangunan lain']]) },
    kelayakan: { label: 'Kelayakan bangunan', layers: ['bld-ver', 'bld-unv', 'bld-unsafe'],
      legend: () => Object.values(TIER).map((t) => [sw(`background:${t.fill};border:2px ${t.line} ${t.color}`), `${t.label}${t === TIER.unverified ? ' — bukan tujuan rute' : t === TIER.unsafe ? ' — tidak pernah dipakai' : ''}`]) },
    bangunan3d: { label: `Bangunan 3D (zoom ≥ ${Z3D})`, layers: ['bld-3d', 'bld-3d-est'], is3d: true,
      legend: () => [[sw(`background:${TIER.verified.color}`), 'Tinggi dari data lantai (OSM/survei)'], [sw(`background:${TIER.verified.color};opacity:.35`), 'Tinggi PERKIRAAN (dari tinggi gedung/satelit)'],
        [sw('background:#d1d5db'), 'Tanpa data lantai: tidak ditinggikan']] },
    penduduk: { label: 'Kepadatan penduduk', layers: ['pop'], available: () => !!(ctx.pop && ctx.pop.count),
      unavailable: 'Belum ada grid penduduk. Model 4 orang/rumah TIDAK dipetakan sebagai kepadatan.',
      legend: () => [...POP_STEPS.map(([v, c], k) => [sw(`background:${c}`), `${k === 0 ? '< 2' : k === POP_STEPS.length - 1 ? `≥ ${v}` : `${v}–${POP_STEPS[k + 1][0]}`} orang/sel`]),
        ['', esc(ctx.pop && ctx.pop.label ? (ctx.pop.synthetic && !/SINTETIS/.test(ctx.pop.label) ? `SINTETIS — ${ctx.pop.label}` : ctx.pop.label) : '')]] },
    waktu: { label: 'Waktu evakuasi (grid)', layers: ['tgrid'], available: () => !!(ctx.tgrid && ctx.tgrid.runs && ctx.tgrid.runs.length),
      unavailable: 'Grid waktu belum dihitung untuk area ini.',
      legend: () => { const g = ctx.tgrid || {}; const b = (g.band_s || 180) / 60; const mb = g.max_bands || 10;
        return [...TIME_COLORS.slice(0, mb + 1).map((c, k) => [sw(`background:${c}`), k < mb ? `${k * b}–${(k + 1) * b} menit` : `≥ ${mb * b} menit`]),
          ['', esc(`${g.label || ''} Sel ${g.cell_m || 30} m; resolusi bukan akurasi. Sel jauh dari jalan kosong.`)]]; } },
    shelter: { label: 'Shelter & kapasitas', layers: ['cap', 'cap-line'], available: () => !!(ctx.cap && ctx.cap.length),
      unavailable: 'Belum ada shelter/gedung terverifikasi berkapasitas.',
      legend: () => [[sw('background:#16a34a;border-radius:50%'), 'Terisi < 70 %'], [sw('background:#f59e0b;border-radius:50%'), 'Terisi 70–99 %'],
        [sw('background:#b91c1c;border-radius:50%;border:2px solid #111'), 'Penuh'], ['', 'Ukuran lingkaran = kapasitas lantai aman (FEMA P-646)']] },
    penanda: { label: 'Penanda (posko, fasilitas, gedung)', layers: [], markers: true,
      legend: () => [[sw('background:#166534;border-radius:50%'), 'Posko / bantuan (petugas)'], [sw('background:#475569;border-radius:50%'), 'Fasilitas umum OSM (belum diverifikasi)'],
        [sw('background:#166534'), 'Gedung evakuasi terverifikasi (L3+ = naik min. lantai 3)'], [sw('background:#fff;border:2px dashed #b45309'), 'Gedung belum diverifikasi (tanpa rute)']] },
  };
}

// Peta: lapisan dasar peta jalan nyata (raster OSM/CARTO) + lapisan SEMPAT dari data area.
// Untuk desa sintetis (koordinatnya di laut), peta dasar dimatikan dan jalan digambar sendiri.
export function createMap(container, area, { interactive = true, buildingsColor = 'hazard', hazardRoads = true,
  groups = null, defaults = null } = {}) {
  let cur = area;
  const ctx = { area, buildingsColor, pop: null, tgrid: null, cap: null };
  const synth = () => !!(cur && cur.meta.synthetic);
  const style = {
    version: 8,
    sources: {
      base: { type: 'raster', tiles: BASEMAPS.carto.tiles, tileSize: 256, maxzoom: 19, attribution: BASEMAPS.carto.attribution },
      sea: { type: 'geojson', data: fc() }, hz: { type: 'geojson', data: fc() }, hzr: { type: 'geojson', data: fc() },
      pop: { type: 'geojson', data: fc() }, tgrid: { type: 'geojson', data: fc() }, water: { type: 'geojson', data: fc() },
      coast: { type: 'geojson', data: fc() }, roads: { type: 'geojson', data: fc() }, bld: { type: 'geojson', data: fc(), maxzoom: 16, buffer: 32 },
      closures: { type: 'geojson', data: fc() }, route: { type: 'geojson', data: fc() }, alts: { type: 'geojson', data: fc() },
      acc: { type: 'geojson', data: fc() }, cap: { type: 'geojson', data: fc() },
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#eef0ea' } },
      { id: 'base', type: 'raster', source: 'base', paint: { 'raster-saturation': -0.25 } },
      { id: 'sea', type: 'fill', source: 'sea', paint: { 'fill-color': '#c9dcec' } },
      // Zona bahaya PERKIRAAN aturan: oranye pucat (legend menyatakan bukan peta resmi). Peta resmi: merah + garis.
      { id: 'hz-rule', type: 'fill', source: 'hzr', paint: { 'fill-color': '#ea580c', 'fill-opacity': 0.16, 'fill-antialias': false } },
      { id: 'hz-off', type: 'fill', source: 'hz', paint: { 'fill-color': '#dc2626', 'fill-opacity': 0.16 } },
      { id: 'hz-off-line', type: 'line', source: 'hz', paint: { 'line-color': '#b91c1c', 'line-width': 1.5 } },
      { id: 'pop', type: 'fill', source: 'pop', layout: { visibility: 'none' },
        paint: { 'fill-color': ['step', ['get', 'p'], ...POP_STEPS.flatMap(([v, c], k) => (k === 0 ? [c] : [v, c]))], 'fill-opacity': 0.55, 'fill-antialias': false } },
      { id: 'tgrid', type: 'fill', source: 'tgrid', layout: { visibility: 'none' },
        paint: { 'fill-color': ['match', ['get', 'v'], ...TIME_COLORS.flatMap((c, k) => [k, c]), '#4d0019'], 'fill-opacity': 0.5, 'fill-antialias': false } },
      { id: 'water', type: 'line', source: 'water', paint: { 'line-color': '#7fb2de', 'line-width': 5 } },
      { id: 'coast', type: 'line', source: 'coast', paint: { 'line-color': '#6b9ac4', 'line-width': 2 } },
      { id: 'bld', type: 'fill', source: 'bld', paint: { 'fill-color': buildingPaint(buildingsColor), 'fill-outline-color': '#6b7280' } },
      { id: 'bld-unsafe', type: 'line', source: 'bld', filter: ['all', ['==', ['get', 'tier'], 'unsafe'], ['==', ['get', 'tall'], 1]],
        paint: { 'line-color': TIER.unsafe.color, 'line-width': 1.5, 'line-dasharray': [0.6, 1.2] } },
      { id: 'bld-unv', type: 'line', source: 'bld', filter: ['==', ['get', 'tier'], 'unverified'], paint: { 'line-color': TIER.unverified.color, 'line-width': 2, 'line-dasharray': [2, 1] } },
      { id: 'bld-ver', type: 'line', source: 'bld', filter: ['==', ['get', 'tier'], 'verified'], paint: { 'line-color': TIER.verified.color, 'line-width': 3 } },
      { id: 'roads', type: 'line', source: 'roads', filter: ['!=', ['get', 'kind'], 'steps'], layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#8d96a0', 'line-width': ['interpolate', ['linear'], ['zoom'], 13, ['case', ['==', ['get', 'kind'], 'road'], 2, 1], 17, ['case', ['==', ['get', 'kind'], 'road'], 7, 3]] } },
      { id: 'steps', type: 'line', source: 'roads', filter: ['==', ['get', 'kind'], 'steps'],
        paint: { 'line-color': '#6b7280', 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 1, 17, 3], 'line-dasharray': [1, 1] } },
      { id: 'closures', type: 'line', source: 'closures', paint: { 'line-color': '#b91c1c', 'line-width': 8, 'line-dasharray': [1, 0.6] } },
      // 3D hanya gedung dengan data lantai, mulai zoom Z3D (performa: bangunan 1 lantai tanpa data tidak diekstrusi).
      { id: 'bld-3d', type: 'fill-extrusion', source: 'bld', minzoom: Z3D - 0.5, layout: { visibility: 'none' },
        filter: ['all', ['==', ['get', 'known'], 1], ['==', ['get', 'est'], 0]],
        paint: { 'fill-extrusion-color': ['match', ['get', 'tier'], 'verified', TIER.verified.color, 'unverified', '#c08a4a', '#9ca3af'],
          'fill-extrusion-height': ['get', 'h'], 'fill-extrusion-base': ['get', 'base'], 'fill-extrusion-opacity': 0.85 } },
      { id: 'bld-3d-est', type: 'fill-extrusion', source: 'bld', minzoom: Z3D - 0.5, layout: { visibility: 'none' },
        filter: ['all', ['==', ['get', 'known'], 1], ['==', ['get', 'est'], 1]],
        paint: { 'fill-extrusion-color': ['match', ['get', 'tier'], 'verified', '#4ade80', 'unverified', '#fcd34d', '#d1d5db'],
          'fill-extrusion-height': ['get', 'h'], 'fill-extrusion-base': ['get', 'base'], 'fill-extrusion-opacity': 0.35 } },
      { id: 'cap', type: 'circle', source: 'cap', layout: { visibility: 'none' },
        paint: { 'circle-radius': ['interpolate', ['linear'], ['sqrt', ['get', 'c']], 0, 5, 40, 22], 'circle-color': ['case', ['>=', ['get', 'r'], 1], '#b91c1c', ['>=', ['get', 'r'], 0.7], '#f59e0b', '#16a34a'],
          'circle-opacity': 0.75, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 } },
      { id: 'cap-line', type: 'circle', source: 'cap', layout: { visibility: 'none' }, filter: ['>=', ['get', 'r'], 1],
        paint: { 'circle-radius': ['interpolate', ['linear'], ['sqrt', ['get', 'c']], 0, 7, 40, 24], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#111827', 'circle-stroke-width': 2 } },
      { id: 'alts', type: 'line', source: 'alts', layout: { 'line-cap': 'round' }, paint: { 'line-color': '#6b7280', 'line-width': 4, 'line-dasharray': [1.5, 1] } },
      { id: 'acc', type: 'fill', source: 'acc', paint: { 'fill-color': '#1d4ed8', 'fill-opacity': 0.12 } },
      { id: 'route-case', type: 'line', source: 'route', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#ffffff', 'line-width': 11 } },
      { id: 'route', type: 'line', source: 'route', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#1d4ed8', 'line-width': 7 } },
    ],
  };
  const opts = { container, style, interactive, attributionControl: false, preserveDrawingBuffer: true, maxZoom: 19, maxPitch: 60 };
  // Kamera awal diatur setelah peta siap (bukan lewat opsi `bounds`), agar tidak menimpa area yang dipilih belakangan.
  if (area) { opts.center = area.meta.center; opts.zoom = 14; }
  else { opts.center = [117.5, -2.5]; opts.zoom = 3.6; }  // Indonesia
  const map = new maplibregl.Map(opts);
  map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), 'top-right');
  const attrib = new maplibregl.AttributionControl({ compact: true });
  map.addControl(attrib);
  const markers = { me: null, dest: null, points: [], official: [], fac: [], gedung: [] };
  // Siap = gaya peta termuat (tidak menunggu ubin peta dasar, yang bisa lambat atau gagal saat sinyal buruk).
  const ready = new Promise((res) => {
    let done = false;
    const f = () => { if (!done) { done = true; res(); } };
    map.once('style.load', f); map.once('load', f);
    if (map.isStyleLoaded()) f();
  });
  // Peta dasar cadangan bila CARTO tidak bisa diakses
  let baseErr = 0, baseOk = false;
  map.on('error', (e) => {
    if (e && e.sourceId === 'base' && !baseOk && ++baseErr === 6) {
      const src = map.getSource('base'); if (src && src.setTiles) src.setTiles(BASEMAPS.osm.tiles);
    }
  });
  map.on('sourcedata', (e) => { if (e.sourceId === 'base' && e.tile) baseOk = true; });

  // ---- kontrol lapisan + legend dinamis (di dalam peta) ----
  const defs = () => groupDefs(ctx);
  const keys = groups || ['rute', 'bahaya', 'jalan', 'bangunan', 'kelayakan', 'bangunan3d', 'penanda'];
  const active = new Set(defaults || keys.filter((k) => k !== 'bangunan3d'));
  const wrap = container.closest('.map-wrap') || container.parentElement;
  const ui = document.createElement('div');
  ui.className = 'lyr-ui';
  ui.innerHTML = `<button type="button" class="btn lyr-btn" aria-expanded="false" aria-controls="lyrPanel">${icon('road')} Lapisan</button>
    <div class="lyr-panel hidden" role="group" aria-label="Lapisan peta"></div>`;
  const legend = document.createElement('div');
  legend.className = 'lyr-legend';
  legend.setAttribute('aria-label', 'Keterangan lapisan aktif');
  container.appendChild(ui); container.appendChild(legend);
  const panel = ui.querySelector('.lyr-panel'), btn = ui.querySelector('.lyr-btn');
  btn.addEventListener('click', () => {
    const open = panel.classList.toggle('hidden') === false;
    btn.setAttribute('aria-expanded', String(open));
    legend.classList.toggle('under-panel', open);  // panel terbuka: legend disembunyikan agar tidak bertumpuk
  });
  // Legend bisa dilipat; bawaan terlipat di layar sempit (HP) agar peta tidak tertutup.
  let legendOpen = store.get('sempat_legend_open', null);
  if (legendOpen == null) legendOpen = !(window.matchMedia && window.matchMedia('(max-width: 640px)').matches);
  legend.addEventListener('click', (ev) => {
    if (!ev.target.closest('.lg-toggle')) return;
    legendOpen = !legendOpen; store.set('sempat_legend_open', legendOpen); renderLegend();
  });
  function applyVisibility() {
    const d = defs();
    for (const k of Object.keys(d)) {
      const on = keys.includes(k) && active.has(k) && (!d[k].available || d[k].available());
      for (const id of d[k].layers) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
      if (d[k].markers) container.classList.toggle('hide-markers', !on && keys.includes(k));
    }
    // 3D: bangunan 2D tetap tampil di zoom jauh; saat 3D aktif kamera dimiringkan (bisa dikembalikan lewat kompas).
    const on3d = keys.includes('bangunan3d') && active.has('bangunan3d');
    if (on3d && map.getPitch() < 20) map.easeTo({ pitch: 50, duration: 400 });
    if (!on3d && map.getPitch() > 0) map.easeTo({ pitch: 0, duration: 300 });
    renderPanel(); renderLegend();
  }
  function renderPanel() {
    const d = defs();
    panel.innerHTML = keys.map((k) => {
      const g = d[k]; const avail = !g.available || g.available();
      return `<label class="lyr-row${avail ? '' : ' off'}"><input type="checkbox" data-lyr="${k}" ${active.has(k) && avail ? 'checked' : ''} ${avail ? '' : 'disabled'}>
        <span>${esc(g.label)}${avail ? '' : `<br><span class="small muted">${esc(g.unavailable || 'Tidak tersedia')}</span>`}</span></label>`;
    }).join('');
    panel.querySelectorAll('input[data-lyr]').forEach((x) => x.addEventListener('change', () => {
      if (x.checked) active.add(x.dataset.lyr); else active.delete(x.dataset.lyr);
      applyVisibility();
    }));
  }
  function renderLegend() {
    const d = defs();
    const html = keys.filter((k) => active.has(k) && (!d[k].available || d[k].available())).map((k) => {
      const items = d[k].legend().filter(([, t]) => t);
      return `<div class="lg-group"><div class="lg-title">${esc(d[k].label)}</div>${items.map(([s, t]) => `<div class="lg-item">${s}<span>${t}</span></div>`).join('')}</div>`;
    }).join('');
    legend.innerHTML = `<button type="button" class="lg-toggle" aria-expanded="${legendOpen}">Keterangan ${legendOpen ? '▾' : '▸'}</button>${legendOpen ? html : ''}`;
    legend.classList.toggle('hidden', !html);
  }

  function applyArea() {
    const a = cur;
    ctx.area = a;
    const sy = synth();
    map.setLayoutProperty('base', 'visibility', sy ? 'none' : 'visible');
    map.getSource('sea').setData(polys(a && a.display && a.display.sea));
    map.getSource('hz').setData(polys(a && a.hazard_polygons));
    map.getSource('hzr').setData(a && a.hazard_view && !a.hazard_view.official ? gridGeoJSON(a.hazard_view) : fc());
    map.getSource('water').setData(sy ? lines(a.water) : fc());
    map.getSource('coast').setData(sy ? lines(a.coastline) : fc());
    map.getSource('roads').setData(roadsGeoJSON(a));
    map.getSource('bld').setData(buildingsGeoJSON(a));
    featCount = a ? a.buildings.length : 0; fullCount += 1;
    statusLast = null;
    // Data nyata: peta dasar sudah memuat jalan; SEMPAT hanya menandai jalan di zona bahaya (merah) dan zona aman (hijau)
    map.setLayoutProperty('roads', 'line-cap', sy ? 'round' : 'butt');
    if (sy) {
      map.setPaintProperty('roads', 'line-color', hazardRoads ? ['case', ['==', ['get', 'hazard'], 1], '#d9827a', '#8d96a0'] : '#9aa3ad');
      map.setPaintProperty('roads', 'line-opacity', 1);
    } else {
      map.setPaintProperty('roads', 'line-color', ['case', ['==', ['get', 'hazard'], 1], '#dc2626', ['==', ['get', 'safe'], 1], '#16a34a', 'rgba(0,0,0,0)']);
      map.setPaintProperty('roads', 'line-opacity', hazardRoads ? 0.45 : 0);
    }
    const src = a ? (a.meta.sources || []).join(' · ') : '';
    attrib.options.customAttribution = [sy ? '' : BASEMAPS.carto.attribution, src].filter(Boolean).join(' · ');
    attrib._updateAttributions && attrib._updateAttributions();
    applyVisibility();
  }
  const fitTo = (a) => { const [w, s, e, n] = a.meta.bbox; map.fitBounds([[w, s], [e, n]], { padding: 30, duration: 0 }); };
  if (cur) fitTo(cur);
  ready.then(applyArea);
  let statusLast = null;
  let featCount = 0, fullCount = 0, patchCount = 0;  // statistik render gedung (dipakai uji performa)
  const BLD_LAYERS = ['bld-3d', 'bld-3d-est', 'bld'];

  const api_ = {
    map, ready, ctx,
    get area() { return cur; },
    async setArea(a, { fit = true } = {}) {
      cur = a;
      if (fit && a) fitTo(a);  // kamera boleh dipindah sebelum gaya peta selesai dimuat
      await ready; applyArea();
    },
    fit() { if (cur) fitTo(cur); },
    layers: { set(k, on) { if (on) active.add(k); else active.delete(k); applyVisibility(); }, active: () => [...active], refresh: () => applyVisibility() },
    setRoute(coords, alts = []) {
      ready.then(() => {
        map.getSource('route').setData(fc(coords ? [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } }] : []));
        map.getSource('alts').setData(fc(alts.map((c) => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: c } }))));
      });
    },
    setMe(pos, acc = null) {
      ready.then(() => map.getSource('acc').setData(pos && acc && acc > 15 ? polys([circle(pos, Math.min(acc, 2000))]) : fc()));
      if (!pos) { if (markers.me) { markers.me.remove(); markers.me = null; } return; }
      if (!markers.me) {
        const el = document.createElement('div'); el.className = 'mk-me'; el.setAttribute('aria-label', 'Posisi Anda');
        markers.me = new maplibregl.Marker({ element: el }).setLngLat([pos.lon, pos.lat]).addTo(map);
      } else markers.me.setLngLat([pos.lon, pos.lat]);
    },
    setDest(d) {
      if (markers.dest) { markers.dest.remove(); markers.dest = null; }
      if (!d) return;
      const el = document.createElement('div'); el.className = 'mk-dest'; el.innerHTML = icon('pin'); el.title = d.name || 'Tujuan';
      markers.dest = new maplibregl.Marker({ element: el, anchor: 'bottom' }).setLngLat([d.lon, d.lat]).addTo(map);
    },
    setPoints(points, onClick) {
      markers.points.forEach((m) => m.remove()); markers.points = [];
      for (const p of points || []) {
        const el = document.createElement('button');
        el.type = 'button'; el.className = `mk-point st-${p.status}`; el.title = `${p.name} — ${STATUS_LABEL[p.status] || p.status}`;
        el.setAttribute('aria-label', el.title);
        el.innerHTML = p.kind === 'posko_medis' ? icon('medical') : icon('people');
        if (onClick) el.addEventListener('click', (ev) => { ev.stopPropagation(); onClick(p); });
        markers.points.push(new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map));
      }
    },
    setFacilities(list, onClick) {
      markers.fac.forEach((m) => m.remove()); markers.fac = [];
      for (const p of list || []) {
        const el = document.createElement('button');
        el.type = 'button'; el.className = 'mk-fac'; el.title = `${p.name} — ${KIND_LABEL[p.kind] || ''} (data OSM)`;
        el.setAttribute('aria-label', el.title);
        el.innerHTML = p.kind === 'faskes' ? icon('medical') : icon('building');
        if (onClick) el.addEventListener('click', (ev) => { ev.stopPropagation(); onClick(p); });
        markers.fac.push(new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map));
      }
    },
    // Gedung evakuasi vertikal: label = lantai minimum yang aman (konservatif). info_only = Perlu Verifikasi, tanpa rute.
    setGedung(list, onClick) {
      markers.gedung.forEach((m) => m.remove()); markers.gedung = [];
      for (const g of list || []) {
        const el = document.createElement('button');
        const ver = g.tier === 'verified' && !g.info_only;
        el.type = 'button'; el.className = `mk-gedung${ver ? ' ver' : ''}${g.info_only ? ' unv' : ''}`;
        el.title = g.info_only
          ? `${g.name} — BELUM DIVERIFIKASI, bukan rekomendasi (tanpa rute)`
          : `${g.name} — naik minimal ke lantai ${g.min_floor} (${g.levels} lantai)${ver ? ', terverifikasi' : ', perlu verifikasi'}`;
        el.setAttribute('aria-label', el.title);
        el.textContent = g.info_only ? `?${g.min_floor}` : `L${g.min_floor}+`;
        if (onClick) el.addEventListener('click', (ev) => { ev.stopPropagation(); onClick(g); });
        markers.gedung.push(new maplibregl.Marker({ element: el }).setLngLat([g.lon, g.lat]).addTo(map));
      }
    },
    // Bangunan di titik layar (2D atau 3D) -> id gedung.
    buildingAt(point) {
      const ls = BLD_LAYERS.filter((id) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none');
      const f = ls.length ? map.queryRenderedFeatures(point, { layers: ls }) : [];
      return f.length ? f[0].properties.id : null;
    },
    setOfficial(list) {
      markers.official.forEach((m) => m.remove()); markers.official = [];
      for (const p of list || []) {
        const el = document.createElement('div'); el.className = 'mk-official'; el.title = p.name; el.innerHTML = icon('up');
        markers.official.push(new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map));
      }
    },
    async setClosures(closures) {
      await ready;
      const feats = [];
      for (const c of closures || []) {
        if (!cur || c.edge >= cur.edges.u.length) continue;
        const u = cur.edges.u[c.edge], v = cur.edges.v[c.edge];
        feats.push({ type: 'Feature', properties: { id: c.id }, geometry: { type: 'LineString', coordinates: [[cur.nodes.lon[u], cur.nodes.lat[u]], [cur.nodes.lon[v], cur.nodes.lat[v]]] } });
      }
      map.getSource('closures').setData(fc(feats));
    },
    // Status perencanaan per bangunan lewat feature-state: TIDAK membangun ulang GeoJSON bangunan (hanya yang berubah).
    async setBuildingStatus(statusArr) {
      await ready;
      const prev = statusLast || [];
      for (let i = 0; i < (statusArr || []).length; i++) {
        const v = statusArr[i] || 'none';
        if (prev[i] !== v) map.setFeatureState({ source: 'bld', id: i }, { status: v });
      }
      statusLast = (statusArr || []).slice();
    },
    // Ganti data satu/beberapa bangunan (mis. hasil survei lewat SSE) tanpa memuat ulang area.
    // Perbarui gedung tertentu tanpa membangun ulang seluruh GeoJSON. `area` = objek area yang SUDAH ditambal
    // (Sync.patchArea). Gedung baru/terhapus menggeser indeks fitur -> bangun ulang penuh (jarang terjadi).
    async patchBuildings(area, changed, removed = []) {
      await ready;
      if (area) cur = area;
      if (!cur) return;
      const src = map.getSource('bld');
      const ix = new Map(cur.buildings.map((b, i) => [b.id, i]));
      const known = (changed || []).every((b) => ix.has(b.id)) && featCount === cur.buildings.length;
      let done = false;
      if (known && !(removed && removed.length) && typeof src.updateData === 'function') {
        const one = { params: cur.params, buildings: [] };
        const update = [];
        for (const b of changed || []) {
          one.buildings = [b];
          const f = buildingsGeoJSON(one).features[0];
          if (!f) continue;
          update.push({ id: ix.get(b.id), newGeometry: f.geometry, addOrUpdateProperties: Object.entries(f.properties).map(([key, value]) => ({ key, value })) });
        }
        try { src.updateData({ update }); done = true; patchCount += 1; } catch (e) { done = false; }
      }
      if (!done) { src.setData(buildingsGeoJSON(cur)); featCount = cur.buildings.length; fullCount += 1; }
      if (statusLast) { const s = statusLast; statusLast = null; api_.setBuildingStatus(s); }
    },
    renderStats() { return { patch: patchCount, full: fullCount }; },
    async setPopulation(cells) { ctx.pop = cells; await ready; map.getSource('pop').setData(popGeoJSON(cells)); applyVisibility(); },
    async setTimeGrid(g) { ctx.tgrid = g; await ready; map.getSource('tgrid').setData(gridGeoJSON(g)); applyVisibility(); },
    // Kapasitas shelter/gedung (dari tabel HP: kapasitas & alokasi server). r = terisi / kapasitas.
    async setCapacity(list) {
      ctx.cap = list; await ready;
      map.getSource('cap').setData(fc((list || []).filter((d) => d.lat != null).map((d) => ({ type: 'Feature',
        properties: { id: d.id, c: d.capacity || 0, r: d.capacity ? (d.allocated || 0) / d.capacity : 1, name: d.name || '' },
        geometry: { type: 'Point', coordinates: [d.lon, d.lat] } }))));
      applyVisibility();
    },
    flyTo(pos, zoom = 16) { map.easeTo({ center: [pos.lon, pos.lat], zoom, duration: 600 }); },
    follow(pos, zoom = null) {
      const z = zoom || Math.max(map.getZoom(), 16);
      map.easeTo({ center: [pos.lon, pos.lat], zoom: z, duration: 500 });
    },
  };
  return api_;
}
// Titik kapasitas untuk peta: kapasitas & alokasi dari ringkasan server, koordinat dari tujuan tabel HP.
export function capacityPoints(tables, mode = 'aman') {
  const cap = tables && tables.capacity && tables.capacity[mode];
  const dests = (tables && tables.dests && tables.dests[mode]) || [];
  if (!cap || !cap.dests) return [];
  const pos = new Map(dests.filter((d) => d.id).map((d) => [d.id, d]));
  return cap.dests.map((d) => { const p = pos.get(d.id) || {}; return { ...d, lat: p.lat, lon: p.lon }; }).filter((d) => d.lat != null);
}

function buildingPaint(mode) {
  if (mode === 'plan') {
    return ['match', ['coalesce', ['feature-state', 'status'], 'none'],
      'sempat', '#86c99a', 'terlayani_shelter', '#93b4f5', 'sebagian', '#f3c46b', 'tidak_sempat', '#e0685e',
      'shelter', '#1e3a8a', 'rekom', '#7c3aed',
      ['match', ['get', 'tier'], 'verified', TIER.verified.fill, 'unverified', TIER.unverified.fill, '#d1d5db']];
  }
  if (mode === 'tier') return ['match', ['get', 'tier'], 'verified', TIER.verified.fill, 'unverified', TIER.unverified.fill, 'rgba(214,216,219,0.85)'];
  return ['case', ['==', ['get', 'hazard'], 1], 'rgba(239,196,191,0.85)', 'rgba(214,216,219,0.85)'];
}

// ---------------- Notifikasi (Web Push) ----------------
function b64ToBytes(b64) {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
export const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
export const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
export function pushSupport() {
  if (!window.isSecureContext) return { ok: false, why: 'Notifikasi butuh alamat https:// (atau localhost).' };
  if (!('serviceWorker' in navigator) || !('Notification' in window)) return { ok: false, why: 'Browser ini tidak mendukung notifikasi.' };
  if (!('PushManager' in window)) {
    if (isIOS() && !isStandalone()) return { ok: false, ios: true, why: 'Di iPhone: ketuk Bagikan → “Tambahkan ke Layar Utama”, lalu buka SEMPAT dari ikon itu.' };
    return { ok: false, local: true, why: 'Browser ini hanya mendukung notifikasi selama SEMPAT terbuka.' };
  }
  return { ok: true };
}
export async function currentPushSub() {
  try { const reg = await navigator.serviceWorker.getRegistration(); return reg ? await reg.pushManager.getSubscription() : null; } catch { return null; }
}
// Minta izin + daftarkan HP ke server. Harus dipanggil dari ketukan pengguna.
export async function enablePush(pushKey, pos) {
  const sup = pushSupport();
  if (!sup.ok && !sup.local) throw new Error(sup.why);
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw new Error('Izin notifikasi ditolak. Aktifkan di pengaturan situs browser.');
  if (!sup.ok || !pushKey) return null; // hanya notifikasi lokal
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  const key = b64ToBytes(pushKey);
  if (sub && sub.options && sub.options.applicationServerKey) {
    const old = new Uint8Array(sub.options.applicationServerKey);
    if (old.length !== key.length || old.some((b, i) => b !== key[i])) { await sub.unsubscribe(); sub = null; }
  }
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  await api.post('/api/push/subscribe', { subscription: sub.toJSON(), lat: pos ? pos.lat : null, lon: pos ? pos.lon : null });
  return sub;
}
// Notifikasi lokal (tanpa server) — dipakai bila push tidak tersedia.
export async function localNotify(title, body, tag, urgent = true) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return false;
  const opts = { body, tag, requireInteraction: urgent, renotify: true, icon: '/static/icons/icon-192.png', vibrate: urgent ? [800, 300, 800, 300, 800] : [200] };
  try {
    const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
    if (reg) await reg.showNotification(title, opts); else new Notification(title, opts);
    return true;
  } catch { return false; }
}

// ---------------- Dialog sederhana ----------------
export function confirmDialog({ title, body, okText = 'Ya', danger = false, input = null }) {
  return new Promise((resolve) => {
    const d = document.createElement('dialog');
    d.innerHTML = `<form method="dialog"><h2>${esc(title)}</h2><div>${body || ''}</div>
      ${input ? `<label class="field">${esc(input.label)}<textarea name="v" required minlength="${input.min || 0}" placeholder="${esc(input.placeholder || '')}"></textarea></label>` : ''}
      <div class="actions"><button class="btn" value="cancel" type="button" data-x>Batal</button>
      <button class="btn ${danger ? 'danger' : 'primary'}" value="ok">${esc(okText)}</button></div></form>`;
    document.body.appendChild(d);
    const f = d.querySelector('form');
    d.querySelector('[data-x]').onclick = () => { d.close(); };
    f.onsubmit = (ev) => { ev.preventDefault(); if (input && !f.v.value.trim()) return; const v = input ? f.v.value.trim() : true; d.close(); resolve(v); };
    d.addEventListener('close', () => { d.remove(); resolve(null); });
    d.showModal();
  });
}

// ---------------- Service worker ----------------
export function registerSW() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// ---------------- Login bersama ----------------
export function renderLogin(root, { title, hint, roles, onDone }) {
  root.innerHTML = `<div class="card stack" style="max-width:420px;margin:2rem auto">
    <h1>${esc(title)}</h1>
    <p class="muted">Masuk dengan akun petugas yang terverifikasi BPBD.</p>
    <form class="stack" id="loginf">
      <label class="field">Nama pengguna<input type="text" name="u" autocomplete="username" required></label>
      <label class="field">Kata sandi<input type="password" name="p" autocomplete="current-password" required></label>
      <button class="btn primary block" type="submit">Masuk</button>
      <p class="small muted" role="alert" id="loginerr"></p>
      ${hint ? `<p class="small muted">${hint}</p>` : ''}
    </form></div>`;
  $('#loginf', root).onsubmit = async (ev) => {
    ev.preventDefault();
    const f = ev.target; $('#loginerr', root).textContent = '';
    try {
      const u = await api.login(f.u.value.trim(), f.p.value);
      if (roles && !roles.includes(u.role) && u.role !== 'admin') { api.logout(); throw new Error('Akun ini tidak memiliki akses ke halaman ini.'); }
      onDone(u);
    } catch (e) { $('#loginerr', root).textContent = e.message; }
  };
}
