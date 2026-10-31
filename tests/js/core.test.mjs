// Jalankan: node tests/js/core.test.mjs  (setelah membuat tests/js/area.json lewat test runner Python)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EdgeIndex, routeFromTable, isSafe, nextTurn, headingToRoute, verdict, fmtMinutes, fmtDistance,
  polylineLength, advanceAlong, compassWord, tablesStale, pickOption, routeFromLock, capacityNote, allocStamp,
  OVERRIDE_RADIUS_M, isLate, deadlinePolicy, remainingSeconds, DEFAULT_DEADLINE, tierOf } from '../../frontend/js/core.js';

const dir = new URL('.', import.meta.url);
const area = JSON.parse(readFileSync(new URL('area.json', dir)));
const bundle = JSON.parse(readFileSync(new URL('tables.json', dir)));
const cases = JSON.parse(readFileSync(new URL('cases.json', dir)));
const idx = new EdgeIndex(area);

let n = 0, nPen = 0;
for (const c of cases) {
  const r = routeFromTable(area, idx, bundle, c.mode, c.group, { lat: c.lat, lon: c.lon });
  assert.equal(r.ok, true, 'rute harus ditemukan');
  // waktu dari tabel di klien harus sama dengan rute server (toleransi pembulatan 1,5 detik)
  assert.ok(Math.abs(r.timeS - c.server_time_s) <= 1.5,
    `waktu klien ${r.timeS.toFixed(1)} vs server ${c.server_time_s}`);
  assert.equal(r.dest.type, c.server_dest_type);
  if (c.server_dest_id) assert.equal(r.dest.id, c.server_dest_id);
  if (r.dest.pen_s) nPen++;
  const last = r.coords[r.coords.length - 1];
  assert.ok(isSafe(area, idx, { lat: last[1], lon: last[0] }) || r.dest.type !== 'safe_zone');
  n++;
}
assert.ok(n >= 3);
// Ada rute yang berakhir di gedung berpenalti kapasitas: waktu klien = waktu server tanpa penalti.
assert.ok(nPen >= 1, 'kasus penalti kapasitas harus teruji');
assert.equal(tablesStale({ valid_until: 100 }, 101000), true);
assert.equal(tablesStale({ valid_until: null }, 101000), false);

// ---- alokasi kapasitas: hash pilihan opsi identik dengan Python, rute override = route() server ----
const ac = JSON.parse(readFileSync(new URL('alloc_cases.json', dir)));
for (const v of ac.pick) assert.equal(pickOption(v.key, v.origin, v.counts), v.expect, `pickOption ${JSON.stringify(v)}`);
const bundle2 = JSON.parse(readFileSync(new URL('tables2.json', dir)));
let nOv = 0;
const optsSeen = new Set();
for (const [b, list] of [[bundle, ac.bundle1], [bundle2, ac.bundle2]]) {
  for (const c of list) {
    const r = routeFromTable(area, idx, b, c.mode, c.group, { lat: c.lat, lon: c.lon }, { deviceKey: c.device_key });
    assert.equal(r.ok, true);
    assert.ok(r.allocation && r.lock, 'posisi asal override harus memakai alokasi khusus');
    assert.equal(r.allocation.origin, c.origin);
    assert.equal(r.allocation.option, c.option, 'opsi HP = opsi server untuk device key yang sama');
    assert.equal(r.dest.type, c.server_dest_type);
    if (c.server_dest_id) assert.equal(r.dest.id, c.server_dest_id);
    assert.ok(Math.abs(r.timeS - c.server_time_s) <= 0.11, `waktu ${r.timeS} vs ${c.server_time_s}`);
    assert.equal(r.coords.length, c.server_n_coords);
    optsSeen.add(`${c.origin}|${c.option}`);
    // Setelah berjalan menjauh dari bangunan, jalur TETAP terkunci ke tujuan alokasi yang sama.
    const mid = r.coords[Math.floor(r.coords.length / 2)];
    const r2 = routeFromTable(area, idx, b, c.mode, c.group, { lat: mid[1], lon: mid[0] }, { deviceKey: c.device_key, lock: r.lock });
    assert.equal(r2.ok, true);
    assert.equal(r2.locked, true);
    assert.equal(r2.dest.type, r.dest.type);
    if (r.dest.id) assert.equal(r2.dest.id, r.dest.id);
    assert.ok(r2.distM <= r.distM + 1 && r2.timeS <= r.timeS + 0.01);
    // Kunci dari hasil alokasi lain tidak dipakai.
    const stale = { ...r.lock, stamp: 'lama' };
    const r3 = routeFromTable(area, idx, b, c.mode, c.group, { lat: c.lat, lon: c.lon }, { deviceKey: c.device_key, lock: stale });
    assert.equal(r3.lock.stamp, r.lock.stamp);
    nOv++;
  }
}
assert.ok(nOv >= 10, 'kasus override harus teruji');
assert.ok(ac.bundle2.length > 0, 'skenario kapasitas mengikat harus punya override');
// Kunci dilepas bila warga menyimpang jauh dari jalur.
const any = ac.bundle2[0];
const rr = routeFromTable(area, idx, bundle2, any.mode, any.group, { lat: any.lat, lon: any.lon }, { deviceKey: any.device_key });
assert.equal(routeFromLock(area, rr.lock, { lat: any.lat + 0.01, lon: any.lon + 0.01 }), null);
assert.equal(OVERRIDE_RADIUS_M, 25);
// Status kapasitas tampil eksplisit.
assert.equal(capacityNote({ capacity: { aman: { status: 'pending' } } }, 'aman').level, 'pending');
assert.equal(capacityNote({ capacity: { aman: { status: 'tanpa_penduduk' } } }, 'aman').level, 'unknown');
assert.equal(capacityNote({ capacity: { aman: { status: 'gagal' } } }, 'aman').level, 'error');
assert.equal(capacityNote({ capacity: { aman: { status: 'kurang' } } }, 'aman').level, 'short');
assert.equal(capacityNote({}, 'aman').level, 'unknown');
assert.equal(capacityNote({ capacity: { aman: { status: 'ok' } } }, 'aman'), null);
assert.equal(allocStamp({ capacity: { aman: { status: 'pending' } } }, 'aman'), null);
// Tanpa hasil alokasi (pending) tidak ada override: rute = tabel.
const pend = { ...bundle2, overrides: {}, capacity: { aman: { status: 'pending' }, gedung: { status: 'pending' } } };
const rp = routeFromTable(area, idx, pend, any.mode, any.group, { lat: any.lat, lon: any.lon }, { deviceKey: any.device_key, lock: rr.lock });
assert.equal(rp.ok, true); assert.ok(!rp.lock && !rp.locked);

// fungsi bantu
assert.equal(fmtMinutes(30), 'kurang dari 1 menit');
assert.equal(fmtMinutes(400), '7 menit');
assert.equal(fmtDistance(523), '520 m');
assert.equal(fmtDistance(1530), '1,5 km');
assert.equal(compassWord(44), 'timur laut');
assert.equal(verdict(300, 1000).level, 'ok');
assert.equal(verdict(950, 1000).level, 'tight');
assert.equal(verdict(1200, 1000).level, 'late');
const L = [[112.5, -9.5], [112.501, -9.5], [112.501, -9.499]];
const t = nextTurn(L);
assert.equal(t.dir, 'kiri');               // timur lalu utara = belok kiri
assert.ok(Math.abs(headingToRoute(L) - 90) < 1);
const adv = advanceAlong(L, polylineLength(L) + 5);
assert.equal(adv.arrived, true);
console.log(`OK core.js: ${n} kasus rute (${nPen} ke gedung berpenalti kapasitas), ${nOv} kasus alokasi override (${optsSeen.size} opsi berbeda), ${ac.pick.length} vektor pickOption + fungsi bantu`);

// ---- Batas waktu: SATU definisi (audit D-4). Pada detik ke-start_delay setelah peringatan, keputusan "terlambat"
// HP (isLate, dipakai verdict & tawaran gedung) sama persis dengan alokasi server (waktu tempuh > anggaran). ----
const dl = JSON.parse(readFileSync(new URL('deadline_cases.json', dir)));
assert.deepEqual(deadlinePolicy({ deadline: dl.policy }), { ...DEFAULT_DEADLINE, ...dl.policy });
let nDl = 0;
for (const c of dl.cases) {
  const issued = Date.parse('2026-11-01T00:00:00Z');
  const alert = { issued_at: '2026-11-01T00:00:00Z', eta_min: c.eta_min };
  const rem = remainingSeconds(alert, issued + c.elapsed_s * 1000);
  assert.equal(isLate(c.route_s, rem, dl.policy.margin_s), c.late_alloc, `deadline ${JSON.stringify(c)}`);
  const v = verdict(c.route_s, rem, dl.policy.margin_s);
  assert.equal(v.level === 'ok', !c.late_alloc, `verdict ${JSON.stringify(c)} -> ${v.level}`);
  nDl++;
}
assert.ok(nDl > 100);
assert.equal(tierOf({}), 'unverified');           // tier tak dikenal tidak pernah dianggap terverifikasi
assert.equal(tierOf({ tier: 'verified' }), 'verified');
console.log(`OK batas waktu: ${nDl} kasus HP = alokasi`);
