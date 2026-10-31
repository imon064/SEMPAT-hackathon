# Audit alokasi kapasitas (fase 1) — sebelum perubahan

Basis: snapshot zip `SEMPAT-hackathon-fixed-v3` (branch `fix/final-safety-review`, belum dipush; folder `.git`
tidak ikut di zip sehingga commit `a52def1` tidak bisa diverifikasi). Semua angka di bawah diukur ulang pada snapshot
ini dengan **grid sintetis**; itu perilaku algoritma, bukan validasi dunia nyata.

## Arsitektur yang ada

| Bagian | Fungsi | Peran kapasitas |
|---|---|---|
| Penduduk | `area_builder` → `b["residents"]` (4 orang/rumah, asumsi) ; `Engine._people` membagi umum/rentan 85/15 | sumber permintaan |
| Tujuan | `shelter_dests` (terdaftar+terverifikasi), `gedung_dests` (semua layak), zona aman (`safe`), tanah tinggi | kapasitas = `tsunami.shelter_capacity` (FEMA P-646) |
| Penalti | `capacity_penalties` → `reverse_k_best` (k=5) + `_auction` + verifikasi `_limited_loads` (max_outer=6, max_steps=20000) | penalti detik per gedung, hanya naik |
| Tabel HP | `live_tables` → `_penalties` (cache) → `reverse_multi_source` dengan penalti | `dests[*].pen_s`, `capacity_check` |
| Rute online | `route()` → `_penalties` (cache, dihitung sinkron bila belum ada) | sama, kecuali mode `gedung` (lihat bug) |
| Perencana | `plan()` → greedy sendiri (`allocate`, pecahan, hanya warga "merah", urut pendaftaran) | **tidak** memakai penalti |
| Peringatan | `create_alert` → `recompute_all` (tabel+penalti semua area) → SSE → Web Push | penalti di jalur kritis |
| Frontend | warga: `routeFromTable` (aman/gedung/posko), `crowdNote` dari `crowded`; petugas/perencana: tidak membaca `capacity_check` | — |

Objektif lama: tidak ada fungsi tujuan eksplisit. Penalti dinaikkan sampai beban model ≤ kapasitas; zona aman dan
tanah tinggi = tujuan tanpa batas (tanpa kebijakan tertulis).

## Temuan (dibuktikan ulang)

| ID | Temuan | Bukti (snapshot ini) |
|---|---|---|
| T1 | Peringatan menunggu optimasi | `create_alert`: `recompute_all` sebelum SSE/push. Grid 19.600 simpul, 5.000 rumah, 150 gedung: SSE & push baru keluar **+11,95 s** |
| T2 | Kapasitas bisa dilanggar, lebih buruk dari tanpa penalti | Rantai 12×20, 200 warga, tanpa cadangan: 170 orang di 1 gedung kap. 20 (tanpa penalti maks. kelebihan 60); 2×200 vs 600 warga: A=600, B=0 |
| T3 | Penalti meledak & lambat | penalti 1,65×10⁸ s; 38–80 s pada grid 180 simpul |
| T4 | Kapasitas terbuang, warga dikirim jauh | 1 bangunan 500 org, 2 gedung 300 dekat, zona aman 2 km: 425 org ke zona aman, gedung terisi 75/600 |
| T5 | `route(mode="gedung")` beda model dgn tabel | penalti dihitung `include_safe=True`, rute `include_safe=False`: 165/240 bangunan beda tujuan; beban 184 vs kap. 96 |
| T6 | `overflow` tidak ditindaklanjuti | tidak dibaca kode lain maupun frontend |
| T7 | Peta otomatis tanpa penduduk → kapasitas tidak dihitung, laporan tampak bersih | `people` kosong → `iterations=0, overflow=[]` |
| T8 | Perencana & rute warga memakai model berbeda | `plan()` greedy pecahan vs penalti per bangunan |

Hipotesis yang belum terbukti: balapan `_pen_cache` (0 galat dari 1.200 panggilan, 8 thread); hitung ganda saat
banyak permintaan bersamaan (tidak diukur).

## Tindak lanjut (setelah implementasi)

| # | Penyelesaian | Bukti |
|---|---|---|
| T1 | SSE + push dikirim sebelum perhitungan apa pun; alokasi di thread latar belakang, status `pending` eksplisit | BENCHMARK.md §3 (+0,003 s vs +16,4 s); `test_capacity_integration` |
| T2 | Kapasitas = kendala keras LP + validasi independen + validasi "seperti HP" | `test_allocation` (rantai 20×10, 2×200/600), BENCHMARK.md §2 (0 pelanggaran) |
| T3 | Tidak ada penalti buatan; offset = harga dual LP; 6–16 s di 19.600 simpul | BENCHMARK.md §1 |
| T4 | Gedung terdekat diisi sebelum zona aman (min waktu), 0 kursi terbuang | `test_gedung_dekat_masih_ada_sisa_zona_aman_jauh`, BENCHMARK.md §2 |
| T5 | `route()` kedua mode menyertakan zona aman; rute online = tabel = alokasi | `test_rute_online_sama_dengan_alokasi`, `core.test.mjs` |
| T6 | Dasbor petugas menampilkan tanpa tujuan, terlambat, penuh, sisa, status & waktu hitung | E2E, `petugas.js renderCapacity` |
| T7 | Area tanpa penduduk → status `tanpa_penduduk` terlihat (tidak tampak "bersih") | `test_tanpa_penduduk_status_eksplisit`, E2E |
| T8 | Perencana memakai `allocate()` yang sama | `test_perencana_memakai_model_alokasi` |
