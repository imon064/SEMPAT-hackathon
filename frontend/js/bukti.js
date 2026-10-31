// Paket Bukti Tsunami Ready (halaman cetak). Data: GET /api/areas/{id}/evidence (backend/evidence.py) + /api/area
// untuk gambar peta. PDF dibuat lewat dialog cetak peramban (Simpan sebagai PDF), tanpa pustaka tambahan.
import { api, esc, store, TIER } from './common.js';

const $ = (s) => document.querySelector(s);
const q = new URLSearchParams(location.search);
const AREA = q.get('area') || store.get('sempat_perencana_area', null);
const nf = (v) => (v == null ? '–' : Number(v).toLocaleString('id-ID'));
const STATUS_CLS = {
  'bukti pendukung tersedia': 'ok', 'perkiraan model, perlu data/validasi resmi': 'est',
  'draf tersedia, perlu pengesahan lembaga': 'draft', 'di luar cakupan SEMPAT': 'out',
  'belum ada data terverifikasi': 'out', 'contoh dari data SINTETIS (bukan bukti)': 'syn',
};
const GROUP = { umum: 'umum (1,0 m/s)', rentan: 'rentan (0,5 m/s)' };

if (q.get('eta')) $('#eta').value = q.get('eta');
if (q.get('wave')) $('#wave').value = q.get('wave');
$('#print').addEventListener('click', () => window.print());
$('#reload').addEventListener('click', () => load());

// ---------- peta SVG (tanpa ubin internet): zona bahaya, jalan, gedung tujuan ----------
function mapSVG(area, ev) {
  const [w, s, e, n] = area.meta.bbox;
  const k = Math.cos(((s + n) / 2) * Math.PI / 180);
  const W = 1000; const H = Math.max(300, Math.min(1300, Math.round(W * (n - s) / ((e - w) * k))));
  const X = (lon) => ((lon - w) / (e - w)) * W;
  const Y = (lat) => ((n - lat) / (n - s)) * H;
  const parts = [];
  const hv = area.hazard_view || {};
  if (hv.official && area.hazard_polygons) {
    for (const r of area.hazard_polygons) parts.push(`<polygon points="${r.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(' ')}" fill="#fecaca" stroke="#991b1b" stroke-width="1.5"/>`);
  } else if (hv.runs && hv.origin) {
    const [lon0, lat0] = hv.origin;
    for (const [i, j, len] of hv.runs) {
      const x0 = X(lon0 + i * hv.dlon); const x1 = X(lon0 + (i + len) * hv.dlon);
      const y1 = Y(lat0 + j * hv.dlat); const y0 = Y(lat0 + (j + 1) * hv.dlat);
      parts.push(`<rect x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${(x1 - x0).toFixed(1)}" height="${(y1 - y0).toFixed(1)}" fill="url(#hzrule)"/>`);
    }
  }
  const { u, v } = area.edges; const { lat, lon, hazard, safe } = area.nodes;
  const road = { hz: [], safe: [], other: [] };
  for (let i = 0; i < u.length; i++) {
    const a = u[i]; const b = v[i];
    const d = `M${X(lon[a]).toFixed(1)} ${Y(lat[a]).toFixed(1)}L${X(lon[b]).toFixed(1)} ${Y(lat[b]).toFixed(1)}`;
    (safe[a] && safe[b] ? road.safe : hazard[a] || hazard[b] ? road.hz : road.other).push(d);
  }
  parts.push(`<path d="${road.other.join('')}" stroke="#9ca3af" stroke-width="1.2" fill="none"/>`);
  parts.push(`<path d="${road.hz.join('')}" stroke="#b91c1c" stroke-width="1.6" fill="none"/>`);
  parts.push(`<path d="${road.safe.join('')}" stroke="#15803d" stroke-width="2.4" fill="none"/>`);
  const reg = new Set((ev.shelters || []).map((x) => x.id));
  ev.buildings.destinations.forEach((d, idx) => {
    const x = X(d.lon); const y = Y(d.lat);
    parts.push(`<rect x="${(x - 9).toFixed(1)}" y="${(y - 9).toFixed(1)}" width="18" height="18" fill="${TIER.verified.fill}" stroke="${TIER.verified.color}" stroke-width="2.5"/>`
      + `<text x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" font-size="11" font-weight="700" text-anchor="middle" fill="#14532d">${idx + 1}</text>`);
  });
  for (const sh of ev.shelters || []) {
    if (ev.buildings.destinations.some((d) => d.id === sh.id)) continue;
    const b = area.buildings.find((x) => x.id === sh.id); if (!b) continue;
    parts.push(`<circle cx="${X(b.lon).toFixed(1)}" cy="${Y(b.lat).toFixed(1)}" r="8" fill="#fff" stroke="#1e3a8a" stroke-width="2.5"/>`);
  }
  void reg;
  // skala 500 m
  const m500 = (500 / (111320 * k)) / (e - w) * W;
  parts.push(`<g transform="translate(${W - m500 - 20},${H - 22})"><rect x="-6" y="-14" width="${m500 + 12}" height="24" fill="#fff" opacity=".85"/><path d="M0 0H${m500.toFixed(1)}" stroke="#111" stroke-width="3"/><text x="${(m500 / 2).toFixed(1)}" y="-3" font-size="11" text-anchor="middle">500 m</text></g>`);
  parts.push(`<g transform="translate(${W - 26},28)"><path d="M0 -16L7 4L0 0L-7 4Z" fill="#111"/><text y="18" font-size="11" text-anchor="middle">U</text></g>`);
  return `<svg class="map" viewBox="0 0 ${W} ${H}" role="img" aria-label="Peta draf zona bahaya, jalan, dan gedung tujuan">
    <defs><pattern id="hzrule" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="8" height="8" fill="#fee2e2"/><path d="M0 0V8" stroke="#f87171" stroke-width="3"/></pattern></defs>
    <rect width="${W}" height="${H}" fill="#f8fafc"/>${parts.join('')}</svg>`;
}

function statusBadge(s) { return `<span class="st ${STATUS_CLS[s] || 'out'}">${esc(s)}</span>`; }

function timeBands(ev) {
  const bs = ev.time_bands.band_s; const budget = ev.scenario.budget_s;
  const rows = [];
  for (const g of ['umum', 'rentan']) {
    const ppl = ev.time_bands.people[g] || {};
    const max = Math.max(1, ...Object.values(ppl));
    const tot = Object.values(ppl).reduce((a, b) => a + b, 0) + (ev.time_bands.no_route[g] || 0);
    rows.push(`<h3>Kelompok ${esc(GROUP[g])}: ${nf(tot)} orang</h3><table><thead><tr><th>waktu tempuh ke zona aman</th><th class="num">orang</th><th>sebaran</th></tr></thead><tbody>`);
    for (let k = 0; k <= 10; k++) {
      const n = ppl[String(k)] || 0; if (!n) continue;
      const late = (k * bs) >= budget;
      const part = !late && k < 10 && (k + 1) * bs > budget;  // pita yang memotong batas anggaran
      const lbl = k === 10 ? `≥ ${10 * bs / 60} menit` : `${k * bs / 60}–${(k + 1) * bs / 60} menit`;
      rows.push(`<tr><td>${lbl}${late ? ' <strong>(melewati anggaran)</strong>' : part ? ` <strong>(sebagian melewati anggaran ${nf(budget / 60)} menit)</strong>` : ''}</td><td class="num">${nf(n)}</td><td><span class="bar${late ? ' late' : ''}" style="width:${Math.max(2, Math.round(n / max * 220))}px"></span></td></tr>`);
    }
    if (ev.time_bands.no_route[g]) rows.push(`<tr><td><strong>tidak ada rute ke zona aman</strong></td><td class="num">${nf(ev.time_bands.no_route[g])}</td><td></td></tr>`);
    rows.push('</tbody></table>');
  }
  return rows.join('');
}

function render(ev, area) {
  const m = ev.model || {}; const snap = m.snapshot || {};
  const sc = ev.scenario;
  const pop = ev.population;
  const syn = ev.area.synthetic;
  const html = [];
  html.push(`<h1>Paket Bukti Pendukung Tsunami Ready</h1>
    <p class="muted">${esc(ev.area.name)} · disusun ${esc(new Date(ev.generated_at).toLocaleString('id-ID'))} oleh ${esc(ev.generated_by)} · SEMPAT ${esc(ev.generated_version)}</p>
    ${syn ? '<div class="synth">DATA SINTETIS: area uji, bukan desa nyata. Isi dokumen ini hanya contoh format, BUKAN bukti.</div>' : ''}
    <div class="disclaimer">${esc(ev.disclaimer)}</div>`);
  html.push(`<h2>Asal data dan versi model</h2><dl class="kv">
    <dt>Area</dt><dd>${esc(ev.area.id)} · bbox ${ev.area.bbox.map((x) => x.toFixed(4)).join(', ')} · zona waktu ${esc(ev.area.timezone || '-')}</dd>
    <dt>Snapshot data</dt><dd>dibangun ${esc(snap.built_at || '-')} · basis OSM ${esc(snap.osm_base || '-')} · elevasi ${esc(snap.elevation || '-')}</dd>
    <dt>Versi model</dt><dd>SEMPAT ${esc(m.sempat)} · skema area ${esc(m.schema)} · parameter ${esc(m.params_sha)} · penduduk: ${esc(pop.label || pop.method)}</dd>
    <dt>Skenario</dt><dd>gelombang tiba ${nf(sc.eta_min)} menit setelah gempa, warga mulai bergerak setelah ${nf(sc.delay_min)} menit, margin ${nf(sc.margin_min)} menit (anggaran jalan ${nf(sc.budget_s / 60)} menit) · tinggi tsunami rencana ${nf(sc.wave_m)} m · ${esc((sc.time && sc.time.label) || '')}</dd>
    <dt>Sumber</dt><dd>${ev.area.sources.map(esc).join(' · ') || '-'}</dd></dl>`);
  html.push(`<h2>Ringkasan per indikator UNESCO-IOC Tsunami Ready</h2>
    <p class="small muted">Status menyatakan ketersediaan BUKTI dari SEMPAT, bukan terpenuhinya indikator. Indikator dipenuhi dan dinilai oleh lembaga yang berwenang.</p>
    <table><thead><tr><th>indikator</th><th>status bukti</th><th>isi dari SEMPAT</th><th>catatan</th></tr></thead><tbody>
    ${ev.indicators.map((x) => `<tr><td><strong>${esc(x.code)}</strong><br>${esc(x.text)}</td><td>${statusBadge(x.status)}</td><td>${esc(x.evidence)}</td><td class="muted">${esc(x.note)}</td></tr>`).join('')}
    </tbody></table>`);
  html.push(`<h2 class="page-break">ASSESS-1 dan PREP-1: peta draf</h2>
    <p>${esc(ev.hazard.label || '')}</p>
    ${mapSVG(area, ev)}
    <div class="legend">
      <span><i class="sw" style="background:${ev.hazard.official ? '#fecaca' : 'repeating-linear-gradient(45deg,#f87171 0 3px,#fee2e2 3px 8px)'}"></i>${ev.hazard.official ? 'zona bahaya (peta resmi)' : 'zona bahaya PERKIRAAN aturan (bukan peta resmi)'}</span>
      <span><i class="sw" style="background:#b91c1c;height:3px"></i>jalan di zona bahaya</span>
      <span><i class="sw" style="background:#15803d;height:4px"></i>jalan di zona aman</span>
      <span><i class="sw" style="background:${TIER.verified.fill};border:2px solid ${TIER.verified.color}"></i>gedung evakuasi vertikal terverifikasi (nomor = tabel ASSESS-3)</span>
      <span><i class="sw" style="border-radius:50%;border:2px solid #1e3a8a"></i>shelter resmi yang belum memenuhi syarat tujuan</span>
    </div>
    <p class="small muted">Gambar disusun dari data area (tanpa peta dasar). Ini bahan peta, bukan peta evakuasi yang disahkan.</p>`);
  const tp = (o) => (o ? o.umum + o.rentan : 0);
  html.push(`<h2>ASSESS-2: orang berisiko</h2>
    <p>${esc(pop.label || pop.method)}${pop.synthetic ? ' <strong>(grid SINTETIS)</strong>' : ''}${pop.virtual_origins ? ` · ${nf(pop.virtual_origins)} titik asal virtual (sel grid tanpa bangunan)` : ''}</p>
    <table><thead><tr><th></th><th class="num">umum</th><th class="num">rentan</th><th class="num">total</th></tr></thead><tbody>
    <tr><td>penduduk dalam area</td><td class="num">${nf(pop.total.umum)}</td><td class="num">${nf(pop.total.rentan)}</td><td class="num">${nf(tp(pop.total))}</td></tr>
    <tr><td>di zona bahaya</td><td class="num">${nf(pop.in_hazard.umum)}</td><td class="num">${nf(pop.in_hazard.rentan)}</td><td class="num">${nf(tp(pop.in_hazard))}</td></tr>
    <tr><td>tidak sempat ke zona aman (jalan kaki, tanpa gedung)</td><td class="num">${nf(ev.reach.late_natural.umum)}</td><td class="num">${nf(ev.reach.late_natural.rentan)}</td><td class="num">${nf(tp(ev.reach.late_natural))}</td></tr>
    <tr><td>tidak sempat setelah shelter resmi dan gedung terverifikasi (alokasi kapasitas)</td><td class="num">${nf(ev.reach.late_with_registered.umum)}</td><td class="num">${nf(ev.reach.late_with_registered.rentan)}</td><td class="num">${nf(tp(ev.reach.late_with_registered))}</td></tr>
    </tbody></table>
    <p class="small muted">Alokasi: ${esc(ev.reach.allocation.method || '-')} · status ${esc(ev.reach.allocation.status || '-')}${ev.reach.allocation.optimal ? ' · optimal' : ''}. Rentan = bagian penduduk dengan kecepatan 0,5 m/s.</p>
    <h3>Sebaran waktu tempuh (pita 3 menit)</h3>${timeBands(ev)}`);
  const b = ev.buildings;
  const tierRow = (t) => `<tr><td><span class="st ${t === 'verified' ? 'ok' : t === 'unverified' ? 'est' : 'out'}">${esc(TIER[t].label)}</span></td><td class="num">${nf(b.tiers[t] || 0)}</td><td class="num">${nf(b.tall_tiers[t] || 0)}</td></tr>`;
  html.push(`<h2 class="page-break">ASSESS-3: sumber daya evakuasi</h2>
    <div class="grid2"><div><table><thead><tr><th>status gedung</th><th class="num">semua</th><th class="num">≥ 2 lantai</th></tr></thead><tbody>
    ${['verified', 'unverified', 'unsafe'].map(tierRow).join('')}</tbody></table>
    <p class="small muted">${nf(b.surveyed)} gedung pernah disurvei petugas · ${nf(b.unknown_levels)} gedung relevan belum diketahui jumlah lantainya.</p></div>
    <div><p class="small">${esc(ev.policy.tiers)}</p></div></div>
    <h3>Gedung evakuasi vertikal terverifikasi untuk skenario ini (${nf(b.destination_count)})</h3>
    ${(b.tiers.verified || 0) > b.destination_count ? `<p class="small">${nf((b.tiers.verified || 0) - b.destination_count)} gedung terverifikasi lainnya tidak menjadi tujuan pada skenario ini (lantai aman konservatif tidak tersedia untuk tinggi tsunami ${nf(sc.wave_m)} m, atau gedung tutup pada kondisi terburuk).</p>` : ''}
    ${b.destinations.length ? `<table><thead><tr><th>#</th><th>gedung</th><th class="num">lantai</th><th class="num">lantai aman (konservatif / nominal)</th><th class="num">kapasitas</th><th>shelter resmi</th><th>survei</th></tr></thead><tbody>
    ${b.destinations.map((d, i) => `<tr><td>${i + 1}</td><td>${esc(d.name)}</td><td class="num">${nf(d.levels)}</td><td class="num">${nf(d.min_floor)} / ${nf(d.min_floor_nominal)}</td><td class="num">${nf(d.capacity)}</td><td>${d.registered ? 'ya' : 'tidak'}</td><td>${esc((d.surveyed_at || '').slice(0, 10))}</td></tr>`).join('')}
    </tbody></table>` : '<p class="err">Belum ada gedung yang lolos verifikasi. Gedung bertingkat tetap berstatus "Perlu Verifikasi" sampai disurvei.</p>'}
    <h3>Daftar verifikasi prioritas (${nf(b.verification_count)} gedung, 40 teratas)</h3>
    ${b.verification.length ? `<table><thead><tr><th>gedung</th><th class="num">lantai</th><th class="num">kapasitas bila lolos</th><th>yang belum terpenuhi</th></tr></thead><tbody>
    ${b.verification.map((v) => `<tr><td>${esc(v.name || v.id)}</td><td class="num">${nf(v.levels)}</td><td class="num">${nf(v.capacity_if_verified)}</td><td class="small">${esc((v.missing || []).join('; '))}</td></tr>`).join('')}
    </tbody></table>` : '<p class="muted">Tidak ada.</p>'}
    <h3>Shelter resmi terdaftar (${nf((ev.shelters || []).length)})</h3>
    ${(ev.shelters || []).length ? `<table><thead><tr><th>shelter</th><th class="num">kapasitas dipakai model</th><th class="num">dialokasikan</th><th>catatan</th></tr></thead><tbody>
    ${ev.shelters.map((s) => `<tr><td>${esc(s.name)}</td><td class="num">${nf(s.capacity)}</td><td class="num">${nf(s.assigned)}</td><td class="small">${esc(s.reason || '')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Belum ada.</p>'}
    <h3>Titik bantuan (${nf(ev.points.length)})</h3>
    ${ev.points.length ? `<table><thead><tr><th>nama</th><th>jenis</th><th>status</th><th class="num">kapasitas</th></tr></thead><tbody>${ev.points.map((x) => `<tr><td>${esc(x.name)}</td><td>${esc(x.kind)}</td><td>${esc(x.status)}</td><td class="num">${nf(x.capacity)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Belum ada.</p>'}`);
  const act = ev.activity;
  html.push(`<h2>PREP-5 dan RESP: kegiatan yang tercatat di SEMPAT</h2>
    <p>${nf(act.survey_count)} survei gedung tercatat${act.first_survey ? ` (${esc(act.first_survey.slice(0, 10))} sampai ${esc(act.last_survey.slice(0, 10))})` : ''}.</p>
    ${act.simulations.length ? `<table><thead><tr><th>waktu</th><th>oleh</th><th>keterangan</th></tr></thead><tbody>${act.simulations.map((s) => `<tr><td>${esc(s.at)}</td><td>${esc(s.user)}</td><td class="small">simulasi peringatan ${esc(s.detail.scope || '')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Belum ada simulasi peringatan yang tercatat.</p>'}
    <p class="small muted">Simulasi di aplikasi tidak menggantikan latihan evakuasi lapangan (PREP-5).</p>`);
  const mg = ev.policy.elev_margin_m || {};
  html.push(`<h2>Kebijakan keselamatan dan parameter</h2><dl class="kv">
    <dt>Status gedung</dt><dd>${esc(ev.policy.tiers)}</dd>
    <dt>Kedalaman & lantai aman</dt><dd>${esc(ev.policy.depth)} Tinggi per lantai ${nf(ev.policy.floor_height_m)} m.</dd>
    <dt>Margin elevasi</dt><dd>tinggi ${nf(mg.tinggi)} m · sedang ${nf(mg.sedang)} m · rendah ${nf(mg.rendah)} m · tidak ada data: ${nf(mg.tidak_ada)} m dan gedung tidak bisa terverifikasi. ${esc(ev.policy.note)}</dd>
    <dt>Batas waktu</dt><dd>${esc(ev.policy.deadline)}</dd>
    <dt>Kecepatan</dt><dd>${Object.entries(sc.speeds_mps).map(([g, v]) => `${esc(g)} ${String(v).replace('.', ',')} m/s`).join(' · ')}</dd></dl>
    <h2>Keterbatasan</h2><ul>${ev.limitations.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
    <footer>SEMPAT bukan sistem peringatan dini resmi. Peringatan resmi: BMKG. Indikator: UNESCO/IOC Tsunami Ready Recognition Programme (IOC Manuals and Guides 74). Dokumen dibuat otomatis dari data area dan parameter model di atas; periksa ulang sebelum dipakai dalam dokumen resmi.</footer>`);
  $('#doc').innerHTML = html.join('');
  document.title = `Paket Bukti Tsunami Ready · ${ev.area.name}`;
  window.__bukti = { ev, ready: true };
}

async function load() {
  window.__bukti = { ready: false };
  if (!api.token) { $('#doc').innerHTML = '<p class="err">Masuk sebagai perencana dulu di <a href="/perencana">dasbor perencana</a>.</p>'; return; }
  try {
    const area = await api.get('/api/area' + (AREA ? `?area=${encodeURIComponent(AREA)}` : ''));
    const p = new URLSearchParams({ eta_min: $('#eta').value || 20 });
    if ($('#wave').value) p.set('wave_m', $('#wave').value);
    const ev = await api.get(`/api/areas/${encodeURIComponent(area.meta.id)}/evidence?${p}`);
    if (!$('#wave').value) $('#wave').placeholder = `${ev.scenario.wave_m} (bawaan)`;
    render(ev, area);
  } catch (e) {
    $('#doc').innerHTML = `<p class="err">${esc(e.message)}</p>`;
  }
}
load();
