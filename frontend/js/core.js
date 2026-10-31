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

// Rute dari tabel: pilih ujung ruas terbaik lalu ikuti pointer "next" sampai tujuan.
export function routeFromTable(area, idx, bundle, mode, group, pos) {
  const key = `${mode}-${group}`;
  const tab = bundle && bundle.tables && bundle.tables[key];
  if (!tab) return { ok: false, reason: 'Data rute belum tersedia.' };
  const s = idx.nearest(pos.lat, pos.lon);
  if (!s) return { ok: false, reason: 'Posisi di luar peta.' };
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
  return { ok: true, coords, timeS: best.total, distM: polylineLength(coords), dest, snapped: s };
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

// Penilaian apakah sempat: dibandingkan sisa waktu sebelum gelombang tiba.
export function verdict(routeS, remainingS) {
  if (remainingS == null) return { level: 'unknown', text: 'Perkiraan waktu tiba gelombang belum tersedia. Tetap bergegas.' };
  if (remainingS <= 0) return { level: 'late', text: 'Perkiraan waktu tiba sudah lewat. Tetap di tempat tinggi dan terus naik.' };
  if (routeS <= remainingS - 120) return { level: 'ok', text: 'Anda diperkirakan sempat. Terus bergerak, jangan berhenti.' };
  if (routeS <= remainingS) return { level: 'tight', text: 'Waktunya mepet. Bergegas sekarang.' };
  return { level: 'late', text: 'Waktu tidak cukup ke tempat tinggi. Naik ke lantai teratas gedung kokoh terdekat.' };
}
