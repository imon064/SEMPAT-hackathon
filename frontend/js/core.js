// Logika murni SEMPAT di sisi klien (tanpa DOM) — dapat diuji dengan Node.
// Rute dihitung dari tabel "langkah berikutnya" hasil Dijkstra multi-sumber di server,
// sehingga tetap berjalan tanpa internet selama tabel terakhir tersimpan.

export const SPEED = { umum: 1.0, rentan: 0.5 };
const R = 6371008.8;
const rad = (d) => (d * Math.PI) / 180;

export function haversine(lat1, lon1, lat2, lon2) {
  const dp = rad(lat2 - lat1), dl = rad(lon2 - lon1);
  const a = Math.sin(dp / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function bearing(lat1, lon1, lat2, lon2) {
  const p1 = rad(lat1), p2 = rad(lat2), dl = rad(lon2 - lon1);
  const x = Math.sin(dl) * Math.cos(p2);
  const y = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360;
}

export class Proj {
  constructor(lat0, lon0) {
    this.lat0 = lat0; this.lon0 = lon0;
    this.kx = rad(1) * R * Math.cos(rad(lat0));
    this.ky = rad(1) * R;
  }
  toXY(lat, lon) { return [(lon - this.lon0) * this.kx, (lat - this.lat0) * this.ky]; }
  toLatLon(x, y) { return [this.lat0 + y / this.ky, this.lon0 + x / this.kx]; }
}

function projectOnSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const L2 = dx * dx + dy * dy;
  let t = L2 <= 1e-12 ? 0 : ((px - ax) * dx + (py - ay) * dy) / L2;
  t = Math.max(0, Math.min(1, t));
  const qx = ax + t * dx, qy = ay + t * dy;
  return { t, d: Math.hypot(px - qx, py - qy), qx, qy };
}

// Indeks spasial ruas jalan untuk "snapping" posisi ke jalan terdekat.
export class EdgeIndex {
  constructor(area, cell = 80) {
    const [lon0, lat0] = area.meta.center;
    this.area = area;
    this.proj = new Proj(lat0, lon0);
    this.cell = cell;
    const { lat, lon } = area.nodes;
    this.xy = lat.map((la, i) => this.proj.toXY(la, lon[i]));
    this.cells = new Map();
    const { u, v } = area.edges;
    for (let e = 0; e < u.length; e++) {
      const [ax, ay] = this.xy[u[e]], [bx, by] = this.xy[v[e]];
      const x0 = Math.floor(Math.min(ax, bx) / cell), x1 = Math.floor(Math.max(ax, bx) / cell);
      const y0 = Math.floor(Math.min(ay, by) / cell), y1 = Math.floor(Math.max(ay, by) / cell);
      for (let i = x0; i <= x1; i++) for (let j = y0; j <= y1; j++) {
        const k = i + ':' + j;
        if (!this.cells.has(k)) this.cells.set(k, []);
        this.cells.get(k).push(e);
      }
    }
  }
  nearest(lat, lon, maxRadius = 20000) {
    const [x, y] = this.proj.toXY(lat, lon);
    const ci = Math.floor(x / this.cell), cj = Math.floor(y / this.cell);
    const { u, v } = this.area.edges;
    let best = null;
    const seen = new Set();
    const maxRing = Math.ceil(maxRadius / this.cell);
    for (let ring = 0; ring <= maxRing; ring++) {
      for (let i = ci - ring; i <= ci + ring; i++) for (let j = cj - ring; j <= cj + ring; j++) {
        if (ring && Math.abs(i - ci) !== ring && Math.abs(j - cj) !== ring) continue;
        const list = this.cells.get(i + ':' + j);
        if (!list) continue;
        for (const e of list) {
          if (seen.has(e)) continue;
          seen.add(e);
          const [ax, ay] = this.xy[u[e]], [bx, by] = this.xy[v[e]];
          const p = projectOnSegment(x, y, ax, ay, bx, by);
          if (!best || p.d < best.d) best = { ...p, e };
        }
      }
      if (best && best.d <= ring * this.cell) break;
    }
    if (!best) return null;
    const [qlat, qlon] = this.proj.toLatLon(best.qx, best.qy);
    return { e: best.e, u: u[best.e], v: v[best.e], t: best.t, perp: best.d, lat: qlat, lon: qlon };
  }
}

// Apakah posisi berada di zona aman (sama dengan definisi tujuan di server).
export function isSafe(area, idx, pos) {
  const s = idx.nearest(pos.lat, pos.lon);
  if (!s || s.perp > 80) return false;
  const node = s.t < 0.5 ? s.u : s.v;
  return area.nodes.safe[node] === 1;
}

export function isHazard(area, idx, pos) {
  const s = idx.nearest(pos.lat, pos.lon);
  if (!s) return false;
  const node = s.t < 0.5 ? s.u : s.v;
  return area.nodes.hazard[node] === 1;
}

// ---------------------------------------------------------------- alokasi kapasitas (sama dengan server)
// Server mengalokasikan penduduk model ke gedung berkapasitas (backend/allocation.py). Sebagian besar asal cukup
// diwakili tabel (offset dual pen_s per tujuan). Bangunan yang alokasinya tidak bisa diwakili tabel (dipecah ke
// beberapa tujuan / seri) dikirim sebagai `overrides`; HP di bangunan itu memilih satu opsi dengan hash stabil ID
// perangkat (porsi = jumlah orang) dan MENGUNCI jalurnya sampai hasil alokasi berganti.
export const OVERRIDE_RADIUS_M = 25;   // sama dengan Engine.OVERRIDE_RADIUS_M
export const LOCK_LEAVE_M = 150;       // menyimpang lebih jauh dari jalur terkunci -> kunci dilepas

// FNV-1a 32-bit atas UTF-8 "key|originKey" -> indeks opsi berbobot jumlah orang. SAMA dengan allocation.pick_option.
export function pickOption(key, originKey, counts) {
  const tot = counts.reduce((a, b) => a + b, 0);
  if (tot <= 0) return 0;
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(`${key}|${originKey}`)) {
    h ^= b;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  const u = (h / 4294967296) * tot;
  let acc = 0;
  for (let i = 0; i < counts.length; i++) {
    acc += counts[i];
    if (u < acc) return i;
  }
  return counts.length - 1;
}

// Penanda hasil alokasi: kunci rute hanya berlaku untuk hasil yang sama.
export function allocStamp(bundle, mode) {
  const c = bundle && bundle.capacity && bundle.capacity[mode];
  return c && c.computed_at ? `${bundle.area_id || ''}|${mode}|${c.computed_at}` : null;
}

export function overrideAt(area, bundle, mode, group, s) {
  const recs = bundle && bundle.overrides && bundle.overrides[`${mode}-${group}`];
  if (!recs || !recs.length || !s) return null;
  // Yang terdekat di ruas yang sama dalam radius (seri: urutan daftar) — sama dengan Engine._override_at.
  const L = area.edges.len[s.e];
  let best = null, bd = Infinity;
  for (const r of recs) {
    if (r.e !== s.e) continue;
    const d = Math.abs(r.t - s.t) * L;
    if (d <= OVERRIDE_RADIUS_M && d < bd) { best = r; bd = d; }
  }
  return best;
}

function lockDest(area, bundle, mode, opt) {
  const list = (bundle.dests && bundle.dests[mode]) || [];
  const last = opt.nodes[opt.nodes.length - 1];
  if (opt.d >= 0 && list[opt.d] && list[opt.d].lat != null) return list[opt.d];
  const safe = area.nodes.safe[last] === 1;
  return { type: safe ? 'safe_zone' : 'tanah_tinggi', name: safe ? 'Zona aman' : 'Tanah lebih tinggi',
    lat: area.nodes.lat[last], lon: area.nodes.lon[last], elev: area.nodes.elev[last] };
}

// Rute sepanjang jalur terkunci dari posisi sekarang (titik jalur terdekat lalu sisa jalur).
export function routeFromLock(area, lock, pos) {
  if (!lock || !lock.nodes || !lock.nodes.length) return null;
  let bi = 0, bd = Infinity;
  for (let i = 0; i < lock.nodes.length; i++) {
    const x = lock.nodes[i];
    const d = haversine(pos.lat, pos.lon, area.nodes.lat[x], area.nodes.lon[x]);
    if (d < bd) { bd = d; bi = i; }
  }
  if (bd > LOCK_LEAVE_M) return null;
  const coords = [[pos.lon, pos.lat], ...lock.nodes.slice(bi).map((x) => [area.nodes.lon[x], area.nodes.lat[x]])];
  if (lock.dest && lock.dest.lat != null && lock.d >= 0) coords.push([lock.dest.lon, lock.dest.lat]);
  const L = polylineLength(coords);
  // Waktu: waktu jalur alokasi dikali porsi jarak tersisa (perkiraan; jalur penuh = waktu server).
  const timeS = lock.s * Math.min(1, L / Math.max(1, lock.len));
  return { ok: true, coords, timeS, distM: L, dest: lock.dest, allocation: lock.allocation, locked: true };
}

// Rute dari tabel: pilih ujung ruas terbaik lalu ikuti pointer "next" sampai tujuan.
// opt.deviceKey: ID perangkat (memilih opsi asal yang dipecah); opt.lock: kunci yang tersimpan (lihat res.lock).
export function routeFromTable(area, idx, bundle, mode, group, pos, opt = {}) {
  const key = `${mode}-${group}`;
  const tab = bundle && bundle.tables && bundle.tables[key];
  if (!tab) return { ok: false, reason: 'Data rute belum tersedia.' };
  const s = idx.nearest(pos.lat, pos.lon);
  if (!s) return { ok: false, reason: 'Posisi di luar peta.' };
  if (mode === 'aman' || mode === 'gedung') {
    const stamp = allocStamp(bundle, mode);
    const lk = opt.lock;
    if (lk && stamp && lk.stamp === stamp && lk.group === group) {
      const r = routeFromLock(area, lk, pos);
      if (r) return r;
    }
    const rec = overrideAt(area, bundle, mode, group, s);
    if (rec) {
      const allocation = { origin: rec.o, unresolved: rec.unres || 0 };
      if (!rec.opts || !rec.opts.length) {
        return { ok: false, reason: 'Tidak ada tujuan yang masih memiliki kapasitas dan terjangkau dari sini.', allocation };
      }
      const counts = rec.opts.map((o) => o.n);
      const i = opt.deviceKey ? pickOption(opt.deviceKey, rec.o, counts) : counts.indexOf(Math.max(...counts));
      const o = rec.opts[i];
      Object.assign(allocation, { option: i, shares: counts });
      const dest = lockDest(area, bundle, mode, o);
      const coords = [[pos.lon, pos.lat], [s.lon, s.lat], ...o.nodes.map((x) => [area.nodes.lon[x], area.nodes.lat[x]])];
      if (o.d >= 0 && dest.lat != null) coords.push([dest.lon, dest.lat]);
      const distM = polylineLength(coords);
      const lock = { stamp, mode, group, o: rec.o, d: o.d, nodes: o.nodes, s: o.s, len: distM, dest, allocation };
      return { ok: true, coords, timeS: o.s, distM, dest, snapped: s, allocation, lock, locked: true };
    }
  }
  const sp = SPEED[group];
  const ec = bundle.edge_cost && bundle.edge_cost[group];
  let opts;
  if (ec) {
    // Sama persis dengan server: akses tegak lurus pada kecepatan datar + potongan ruas berarah.
    opts = [[s.u, s.perp / sp + s.t * ec.vu[s.e]], [s.v, s.perp / sp + (1 - s.t) * ec.uv[s.e]]];
  } else {
    const L = area.edges.len[s.e];
    opts = [[s.u, (s.perp + s.t * L) / sp], [s.v, (s.perp + (1 - s.t) * L) / sp]];
  }
  let best = null;
  for (const [node, c] of opts) {
    const tt = tab.time[node];
    if (tt < 0) continue;
    if (!best || c + tt < best.total) best = { node, total: c + tt };
  }
  if (!best) return { ok: false, reason: mode === 'aman' ? 'Tidak ada tempat aman yang terjangkau dari sini.' : 'Belum ada posko yang terjangkau dengan aman.' };
  const coords = [[pos.lon, pos.lat], [s.lon, s.lat]];
  let x = best.node, guard = 0;
  coords.push([area.nodes.lon[x], area.nodes.lat[x]]);
  while (tab.next[x] !== -1 && guard++ < 100000) {
    x = tab.next[x];
    coords.push([area.nodes.lon[x], area.nodes.lat[x]]);
  }
  const di = tab.dest[x];
  let dest;
  const list = (bundle.dests && bundle.dests[mode]) || [];
  if (di >= 0 && list[di]) {
    dest = list[di];
    // Tujuan gabungan tanpa titik tetap (mis. "tanah lebih tinggi"): titik akhir = simpul terakhir.
    if (dest.lat == null) dest = { ...dest, lat: area.nodes.lat[x], lon: area.nodes.lon[x], elev: area.nodes.elev[x] };
    else coords.push([dest.lon, dest.lat]);
  } else {
    dest = { type: 'safe_zone', name: 'Zona aman', lat: area.nodes.lat[x], lon: area.nodes.lon[x], elev: area.nodes.elev[x] };
  }
  // Penalti kapasitas (server) hanya untuk memilih tujuan yang masih muat; waktu tempuh yang tampil tanpa penalti.
  const timeS = best.total - (dest.pen_s || 0);
  return { ok: true, coords, timeS, distM: polylineLength(coords), dest, snapped: s };
}

// Status daya tampung untuk ditampilkan ke warga: null bila alokasi sudah dihitung dan semua tertampung.
export function capacityNote(bundle, mode) {
  const c = bundle && bundle.capacity && bundle.capacity[mode];
  if (!c) return { level: 'unknown', text: 'Daya tampung gedung belum diperhitungkan pada data rute ini.' };
  switch (c.status) {
    case 'pending': return { level: 'pending', text: 'Daya tampung gedung sedang dihitung. Sementara ini rute ke tujuan terdekat, belum memperhitungkan kapasitas.' };
    case 'tanpa_penduduk': return { level: 'unknown', text: 'Data penduduk tidak tersedia: daya tampung gedung TIDAK diperhitungkan pada rute ini.' };
    case 'gagal': return { level: 'error', text: 'Perhitungan daya tampung gagal: rute ke tujuan terdekat tanpa memperhitungkan kapasitas.' };
    case 'kurang': return { level: 'short', text: 'Tempat aman yang terjangkau tidak cukup untuk semua warga. Ikuti arahan petugas.' };
    default: return null;
  }
}

// Tabel rute dihitung untuk jam buka gedung pada waktu tertentu; sesudah valid_until (detik unix) bisa usang.
export function tablesStale(bundle, nowMs = Date.now()) {
  return !!(bundle && bundle.valid_until && nowMs / 1000 >= bundle.valid_until);
}

export function polylineLength(coords) {
  let d = 0;
  for (let i = 1; i < coords.length; i++) d += haversine(coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0]);
  return d;
}

// Titik di sepanjang rute pada jarak tertentu dari awal.
export function pointAlong(coords, dist) {
  let acc = 0;
  for (let i = 1; i < coords.length; i++) {
    const [lo1, la1] = coords[i - 1], [lo2, la2] = coords[i];
    const seg = haversine(la1, lo1, la2, lo2);
    if (acc + seg >= dist && seg > 0) {
      const f = (dist - acc) / seg;
      return [lo1 + f * (lo2 - lo1), la1 + f * (la2 - la1)];
    }
    acc += seg;
  }
  return coords[coords.length - 1];
}

// Arah yang harus dituju sekarang: titik 25 m di depan sepanjang rute.
export function headingToRoute(coords) {
  if (!coords || coords.length < 2) return null;
  const [lo, la] = coords[0];
  const total = polylineLength(coords);
  const [tlo, tla] = pointAlong(coords, Math.min(25, total));
  if (haversine(la, lo, tla, tlo) < 1) return null;
  return bearing(la, lo, tla, tlo);
}

// Belokan berikutnya (> 35°) di depan, untuk instruksi teks.
export function nextTurn(coords, minDeg = 35) {
  if (!coords || coords.length < 3) return null;
  let acc = 0;
  for (let i = 1; i < coords.length - 1; i++) {
    const [lo0, la0] = coords[i - 1], [lo1, la1] = coords[i], [lo2, la2] = coords[i + 1];
    acc += haversine(la0, lo0, la1, lo1);
    if (haversine(la1, lo1, la2, lo2) < 3 || haversine(la0, lo0, la1, lo1) < 3) continue;
    const b1 = bearing(la0, lo0, la1, lo1), b2 = bearing(la1, lo1, la2, lo2);
    let d = ((b2 - b1 + 540) % 360) - 180;
    if (Math.abs(d) >= minDeg) return { distM: acc, dir: d > 0 ? 'kanan' : 'kiri', deg: Math.round(d) };
  }
  return null;
}

// Gerakkan posisi sepanjang rute sejauh "meters" (untuk simulasi berjalan).
export function advanceAlong(coords, meters) {
  if (!coords || coords.length < 2) return null;
  const total = polylineLength(coords);
  const [lo, la] = pointAlong(coords, Math.min(meters, total));
  return { lat: la, lon: lo, arrived: meters >= total };
}

const WORDS = ['utara', 'timur laut', 'timur', 'tenggara', 'selatan', 'barat daya', 'barat', 'barat laut'];
export function compassWord(deg) { return WORDS[Math.round(((deg % 360) + 360) % 360 / 45) % 8]; }

export function fmtMinutes(s) {
  if (s == null || !isFinite(s)) return '-';
  if (s < 60) return 'kurang dari 1 menit';
  return `${Math.round(s / 60)} menit`;
}

export function fmtDistance(m) {
  if (m == null || !isFinite(m)) return '-';
  if (m < 1000) return `${Math.round(m / 10) * 10} m`;
  return `${(m / 1000).toFixed(1).replace('.', ',')} km`;
}

export function remainingSeconds(alert, nowMs = Date.now()) {
  if (!alert || alert.eta_min == null || !alert.issued_at) return null;
  return (Date.parse(alert.issued_at) + alert.eta_min * 60000 - nowMs) / 1000;
}

export function timeAgo(iso, nowMs = Date.now()) {
  if (!iso) return '-';
  const s = Math.max(0, (nowMs - Date.parse(iso)) / 1000);
  if (s < 60) return 'baru saja';
  if (s < 3600) return `${Math.floor(s / 60)} menit lalu`;
  if (s < 86400) return `${Math.floor(s / 3600)} jam lalu`;
  return `${Math.floor(s / 86400)} hari lalu`;
}

export function clockTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
}

// ---------------- Batas waktu evakuasi: SATU definisi dengan backend (Engine.deadline_policy) ----------------
// Batas tiba = waktu peringatan + ETA - margin. Alokasi/perencana mengasumsikan warga mulai bergerak start_delay_s
// setelah peringatan (anggaran jalan = ETA - jeda - margin); HP memakai waktu sekarang yang nyata, sehingga pada
// detik ke-start_delay_s keduanya memberi keputusan "terlambat" yang sama (diuji di tests/js/core.test.mjs).
export const DEFAULT_DEADLINE = { margin_s: 120, start_delay_s: 300, default_eta_min: 20 };
export function deadlinePolicy(tables) {
  return { ...DEFAULT_DEADLINE, ...((tables && tables.deadline) || {}) };
}
// Terlambat = tiba setelah batas tiba (sisa waktu dikurangi margin). Dipakai verdict DAN tawaran gedung.
export function isLate(routeS, remainingS, marginS = DEFAULT_DEADLINE.margin_s) {
  return remainingS != null && routeS > remainingS - marginS;
}
// Penilaian: ok = tiba sebelum batas tiba; mepet = di dalam margin (sebelum ETA); terlambat = setelah ETA.
export function verdict(routeS, remainingS, marginS = DEFAULT_DEADLINE.margin_s) {
  if (remainingS == null) return { level: 'unknown', text: 'Perkiraan waktu tiba gelombang belum tersedia. Tetap bergegas.' };
  if (remainingS <= 0) return { level: 'late', text: 'Perkiraan waktu tiba sudah lewat. Tetap di tempat tinggi dan terus naik.' };
  if (!isLate(routeS, remainingS, marginS)) return { level: 'ok', text: 'Anda diperkirakan sempat. Terus bergerak, jangan berhenti.' };
  if (routeS <= remainingS) return { level: 'tight', text: 'Waktunya mepet. Bergegas sekarang.' };
  return { level: 'late', text: 'Waktu tidak cukup ke tempat tinggi. Naik ke lantai teratas gedung kokoh terdekat.' };
}

// ---------------- Tingkat kepastian gedung (SATU definisi dari server: tsunami.verification) ----------------
export const TIER_LABEL = { verified: 'Terverifikasi', unverified: 'Perlu Verifikasi', unsafe: 'Tidak Aman' };
export function tierOf(b) { return (b && TIER_LABEL[b.tier]) ? b.tier : 'unverified'; }
