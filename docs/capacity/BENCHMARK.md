# Benchmark & perbandingan alokasi kapasitas

**Semua angka di sini dari area grid SINTETIS** (`tools/bench_capacity.py`), bukan dari kota nyata, dan bukan validasi
lapangan. Grid n×n simpul berjarak 50 m; zona aman = 2 baris teratas (atau tidak ada); rumah 4 orang di 60 % bagian
bawah; "sekolah" 4 lantai dianggap sudah disurvei (agar bisa menjadi shelter resmi) dengan luas tapak acak; kapasitas
dihitung engine (FEMA P-646). Batas waktu jalan T = (ETA 20 − 5 − 2) menit = 13 menit; tinggi gelombang 3 m; mode
`aman` (shelter resmi + zona aman). Diukur 1 Nov 2026 pada kontainer 2 vCPU (Intel Xeon 2,8 GHz), Python 3.13,
scipy 1.18.1 (HiGHS). Angka waktu berbeda di mesin lain.

Semua metrik hasil (beban, terlambat, waktu) dihitung **"seperti HP"**: setiap bangunan berpenghuni mengikuti tabel
yang benar-benar dikirim ke HP (+ `overrides` untuk metode baru) — bukan angka internal solver. Permintaan bulat yang
sama dipakai untuk ketiga metode.

Mereproduksi:

```bash
python tools/bench_capacity.py perf --json perf.json
git worktree add ../sempat-lama <commit lama>          # kode sebelum perubahan (pembanding "old")
python tools/bench_capacity.py compare --old-repo ../sempat-lama --policy --json compare.json
python tools/bench_capacity.py alert --old-repo ../sempat-lama --json alert.json
```

## 1. Kinerja metode baru (rincian waktu, detik)

`kandidat` = Dijkstra k-label + Dijkstra ke zona aman; `solver` = semua tahap LP HiGHS (semua putaran);
`harga` = pemeriksaan harga (column generation); `rekon` = rekonsiliasi bulat; `validasi` = validasi independen;
`enkode` = dual→tabel, overrides, jalur, dan simulasi "seperti HP"; `total` = semuanya.

| skenario | simpul | gedung | orang | kandidat | solver | harga | rekon | validasi | enkode | **total** | status | ke gedung / zona aman / tanpa tujuan | overrides (KB gzip) | tabel HP (KB gzip) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| kecil | 1.600 | 15 | 1.600 | 0,04 | 0,12 | 0,00 | 0,005 | 0,001 | 0,02 | **0,20** | ok | 1.350 / 250 / 0 | 451 (13) | 23 |
| kecil, tanpa zona aman | 1.600 | 15 | 1.600 | 0,06 | 0,18 | 0,02 | 0,004 | 0,000 | 0,02 | **0,29** | kurang | 1.350 / 0 / 250 | 167 (2) | 13 |
| sedang | 6.400 | 60 | 7.200 | 0,39 | 1,05 | 0,01 | 0,008 | 0,002 | 0,11 | **1,62** | ok | 5.889 / 1.311 / 0 | 2.120 (79) | 143 |
| sedang, tanpa zona aman | 6.400 | 60 | 7.200 | 0,41 | 1,71 | 0,15 | 0,007 | 0,002 | 0,08 | **2,37** | kurang | 5.889 / 0 / 1.311 | 955 (22) | 98 |
| besar | 19.600 | 150 | 20.000 | 1,49 | 3,98 | 0,04 | 0,012 | 0,005 | 0,51 | **6,19** | ok | 14.253 / 5.747 / 0 | 5.260 (252) | 441 |
| besar, tanpa zona aman | 19.600 | 150 | 20.000 | 1,85 | 12,74 | 1,33 | 0,012 | 0,004 | 0,33 | **16,39** | kurang | 14.253 / 0 / 5.747 | 3.318 (74) | 296 |
| besar, penduduk 2× | 19.600 | 150 | 40.000 | 1,38 | 9,25 | 0,14 | 0,019 | 0,009 | 0,54 | **11,52** | ok | 14.821 / 25.179 / 0 | 6.498 (355) | 544 |
| besar, penduduk 2×, tanpa zona aman | 19.600 | 150 | 40.000 | 1,75 | 10,46 | 0,63 | 0,017 | 0,007 | 0,49 | **13,47** | kurang | 14.821 / 0 / 25.179 | 9.710 (168) | 383 |
| terburuk (kapasitas langka, penduduk 2×) | 19.600 | 150 | 40.000 | 1,35 | 7,99 | 0,14 | 0,018 | 0,007 | 0,38 | **10,05** | ok | 3.217 / 36.783 / 0 | 2.788 (124) | 312 |
| terburuk, tanpa zona aman | 19.600 | 150 | 40.000 | 1,71 | 12,88 | 0,83 | 0,018 | 0,005 | 0,45 | **16,00** | kurang | 3.217 / 0 / 36.783 | 12.688 (203) | 420 |

Pada semua 10 skenario: **0 gedung melebihi kapasitas** (dihitung dari tabel HP), beban dasbor = beban HP, metode
`lp_highs`, `optimal=true` (pemeriksaan harga konvergen, rekonsiliasi LP). Total waktu ini berjalan di latar belakang.

Pemilihan solver (masalah besar yang sama, k = 8): `highs-ipm` total 17,4 s vs `highs-ds` 57,6 s vs `highs` 58,7 s
(diukur sebelum optimasi enkode; dasar pemilihan `LP_METHOD = "highs-ipm"`). HiGHS melepaskan GIL (detak thread lain
maks. 49 ms selama solve), sehingga server tetap melayani permintaan.

Keoptimalan diperiksa terpisah: 1.500 instans acak kecil (≤ 60 asal, ≤ 12 gedung, dengan/tanpa batas waktu dan zona
aman) dibandingkan dengan MILP independen (`scipy.optimize.milp`, variabel bulat, bobot leksikografis): **0 berbeda**
(tidak teralokasi, terlambat, total waktu). Pemeriksaan ini menemukan bug bobot rekonsiliasi yang lalu diperbaiki
(DESIGN.md §4).

## 2. Perbandingan: tanpa kapasitas vs lama ("lelang") vs baru (LP)

`langgar` = gedung dengan beban HP > kapasitas (orang berlebih; rasio terburuk). `tak terpakai` = Σ kapasitas gedung
yang tidak terisi. `dasbor = HP` = beban yang dilaporkan sama dengan beban yang dihasilkan tabel HP.
"Waktu" = waktu tempuh yang akan dialami (tanpa penalti).

| skenario | metode | langgar (orang, rasio) | ke gedung | ke zona aman | tanpa tujuan (dilaporkan) | terlambat (> T) | total waktu (jam·orang) | rata-rata / maks (menit) | kapasitas tak terpakai | waktu hitung tabel | dasbor = HP |
|---|---|---|---|---|---|---|---|---|---|---|---|
| besar (19.600 simpul, 150 gedung, 20.000 org, kapasitas 14.253) | tanpa kapasitas | 103 (+7.519, ×4,09) | 20.000 | 0 | 0 | 853 | 2.047,5 | 6,1 / 32,5 | 1.772 | 0,4 s | — |
| | lama | 5 (+75, ×1,49) | 8.988 | 11.012 | 0 | 13.622 | 15.723,2 | 47,2 / 121,0 | 5.340 | 14,1 s | **tidak** |
| | **baru** | **0** | 14.253 | 5.747 | 0 | **5.747** | **9.942,6** | 29,8 / 242,1 | **0** | 13,7 s* | ya |
| | baru, tanpa tahap batas waktu | 0 | 14.253 | 5.747 | 0 | 9.319 | 8.912,6 | 26,7 / 121,0 | 0 | 10,1 s† | ya |
| besar, **tanpa zona aman** | tanpa kapasitas | 103 (+7.519, ×4,09) | 20.000 | 0 | 0 | 853 | 2.047,5 | 6,1 / 32,5 | 1.772 | 0,4 s | — |
| | lama | 17 (+17.737, **×160**) | 20.000 | 0 | 0 | 16.268 | 12.888,9 | 38,7 / 202,6 | 11.990 | **522 s** | **tidak** |
| | **baru** | **0** | 14.253 | 0 | **5.747** | 0 | 1.189,3 | 5,0 / 12,6 | 0 | 36,6 s* | ya |
| sedang (6.400 simpul, 60 gedung, 7.200 org, kap. 5.889) | tanpa kapasitas | 34 (+2.431, ×7,7) | 7.200 | 0 | 0 | 405 | 735,1 | 6,1 / 42,5 | 1.120 | 0,1 s | — |
| | lama | 2 (+5, ×1,05) | 4.273 | 2.927 | 0 | 3.774 | 2.836,5 | 23,6 / 71,0 | 1.621 | 3,5 s | **tidak** |
| | **baru** | **0** | 5.889 | 1.311 | 0 | **1.311** | **1.810,5** | 15,1 / 142,1 | **0** | 3,5 s* | ya |
| | baru, tanpa tahap batas waktu | 0 | 5.889 | 1.311 | 0 | 2.178 | 1.610,3 | 13,4 / 63,7 | 0 | 1,6 s† | ya |
| sedang, kapasitas langka (12.000 org, kap. 1.230) | tanpa kapasitas | 60 (+10.770, ×23,3) | 12.000 | 0 | 0 | 314 | 1.106,1 | 5,5 / 24,2 | 0 | 0,1 s | — |
| | lama | 7 (+18, ×1,28) | 794 | 11.206 | 0 | 11.581 | 10.473,3 | 52,4 / 140,4 | 454 | 2,2 s | **tidak** |
| | **baru** | **0** | 1.230 | 10.770 | 0 | **10.770** | **9.908,5** | 49,5 / 142,1 | **0** | 4,7 s* | ya |

\* waktu `live_tables` sinkron untuk kedua mode (aman + gedung); di server ini berjalan di latar belakang.
† hanya alokasi mode aman (`all_s`).

Cara membaca dengan jujur:

* **Tanpa kapasitas** tampak paling cepat (853 terlambat), tetapi hanya karena 7.519 orang "masuk" ke gedung yang
  sudah penuh (hingga 4× kapasitas) — angka waktunya tidak bisa terjadi. Ini bukan pembanding yang layak dicapai.
* **Lama**: tetap melanggar kapasitas (hingga ×160 tanpa zona aman), membuang kapasitas (5.340 kursi kosong sementara
  11.012 orang dikirim ke zona aman jauh), lambat (522 s), dan angka dasbor ≠ rute HP.
* **Baru**: 0 pelanggaran, 0 kursi terbuang, kekurangan dilaporkan sebagai "tanpa tujuan" (bukan disembunyikan), dan
  dibanding lama: terlambat −58 % (13.622 → 5.747), total waktu −37 %, rata-rata 47 → 30 menit.
* **Biaya tahap batas waktu** (kebijakan, DESIGN.md §2): dibanding min-total-waktu murni, terlambat turun
  9.319 → 5.747, tetapi total waktu naik 11,6 %, waktu **maksimum** naik 121 → 242 menit (orang yang memang tidak
  sempat ke gedung mana pun diarahkan ke tujuan lebih jauh agar kursi dekat dipakai yang masih bisa tepat waktu), dan
  overrides 1.398 → 5.260 (unduhan HP 403 → 441 KB gzip). Pada grid sintetis ini zona aman sangat jauh, sehingga semua
  orang yang ke zona aman memang terlambat; pada peta nyata angkanya akan berbeda. Parameter area
  `alloc_prioritize_deadline` memilih kebijakan; keputusan sebaiknya dibuat bersama BPBD.

## 3. Latensi peringatan

Dari `create_alert` dipanggil sampai SSE `alert` dipublikasikan dan Web Push mulai dijadwalkan (150 shelter terdaftar,
skenario peringatan Siaga baru → alokasi harus dihitung ulang):

| kode | simpul | SSE peringatan | push mulai | `create_alert` kembali | alokasi selesai (latar belakang) |
|---|---|---|---|---|---|
| lama | 6.400 | +7,28 s | +7,28 s | 7,28 s | (di dalam request) |
| lama | 19.600 | **+16,40 s** | +16,40 s | 16,40 s | (di dalam request) |
| baru | 6.400 | **+0,003 s** | +0,003 s | 4,82 s‡ | +11,9 s, status ok |
| baru | 19.600 | **+0,003 s** | +0,003 s | 4,60 s‡ | +21,8 s, status ok |

‡ sisa waktu request = tabel dasar (Dijkstra tanpa optimasi) untuk HP; peringatan sudah terkirim sebelumnya.
Selama alokasi berjalan, tabel berstatus `pending` (diuji di `tests/test_capacity_integration.py` dengan optimasi
diperlambat buatan 2 s: SSE & push < 1 s, request < 2 s, status `pending`, lalu SSE `state` `reason=kapasitas`).

## 4. Hasil uji (saat dokumen ini dibuat)

* `python -m unittest discover tests` — 178 uji, semua lulus (Python 3.13; juga Python 3.10.20 dengan scipy 1.15.3 dan
  dengan versi minimum scipy 1.10.1 / numpy 1.23.5 untuk uji alokasi & integrasi).
* `node tests/js/core.test.mjs` — 162 kasus rute, 48 kasus override (HP = server: tujuan, opsi, waktu, jumlah titik),
  300 vektor hash `pickOption` identik dengan Python.
* `node tests/e2e/e2e.js` — 46 cek lulus (peta otomatis tanpa penduduk → status `tanpa_penduduk` tampil di dasbor).
