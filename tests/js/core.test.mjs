// Jalankan: node tests/js/core.test.mjs  (setelah membuat tests/js/area.json lewat test runner Python)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EdgeIndex, routeFromTable, isSafe, nextTurn, headingToRoute, verdict, fmtMinutes, fmtDistance,
  polylineLength, advanceAlong, compassWord } from '../../frontend/js/core.js';

const dir = new URL('.', import.meta.url);
const area = JSON.parse(readFileSync(new URL('area.json', dir)));
const bundle = JSON.parse(readFileSync(new URL('tables.json', dir)));
const cases = JSON.parse(readFileSync(new URL('cases.json', dir)));
const idx = new EdgeIndex(area);

let n = 0;
for (const c of cases) {
  const r = routeFromTable(area, idx, bundle, c.mode, c.group, { lat: c.lat, lon: c.lon });
  assert.equal(r.ok, true, 'rute harus ditemukan');
  // waktu dari tabel di klien harus sama dengan rute server (toleransi pembulatan 1,5 detik)
  assert.ok(Math.abs(r.timeS - c.server_time_s) <= 1.5,
    `waktu klien ${r.timeS.toFixed(1)} vs server ${c.server_time_s}`);
  assert.equal(r.dest.type, c.server_dest_type);
  const last = r.coords[r.coords.length - 1];
  assert.ok(isSafe(area, idx, { lat: last[1], lon: last[0] }) || r.dest.type !== 'safe_zone');
  n++;
}
assert.ok(n >= 3);

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
console.log(`OK core.js: ${n} kasus rute + fungsi bantu`);
