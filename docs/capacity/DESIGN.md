# Desain alokasi berkapasitas

Status: **diimplementasikan** (`backend/allocation.py`, `backend/engine.py`, `backend/app.py`, `frontend/js/core.js`).
Temuan sebelum perubahan: [AUDIT.md](AUDIT.md). Angka kinerja & perbandingan: [BENCHMARK.md](BENCHMARK.md).

Tujuan: setiap warga model dialokasikan ke tujuan yang layak dan terjangkau, **tidak pernah** melebihi kapasitas
gedung, tanpa kehilangan satu orang pun dalam pembukuan, dan rute HP (offline), rute online, dasbor petugas, dan
perencana memakai **hasil yang sama**. Bila tempat tidak cukup, kekurangannya dilaporkan, bukan disembunyikan.

## 1. Himpunan dan data

| Simbol | Arti | Sumber di kode | Jenis data |
|---|---|---|---|
| o ∈ O | asal = (bangunan berpenghuni, kelompok g ∈ {umum, rentan}) | `Engine.demand()` | model |
| q_o ∈ ℤ≥0 | permintaan (orang). Penduduk model (`residents`) dibulatkan **largest remainder** (total area tetap), lalu 15 % rentan dengan cara yang sama | `allocation.largest_remainder` | **perkiraan** (4 orang/rumah dsb.) |
| d ∈ D | tujuan berkapasitas: shelter resmi terverifikasi (mode `aman`) atau semua gedung layak (mode `gedung`) | `Engine.mode_dests` | — |
| κ_d ∈ ℤ≥0 | kapasitas tersedia = kapasitas FEMA P-646 dari luas tapak × lantai aman − **okupansi yang dilaporkan petugas** | `tsunami.shelter_capacity`, `PUT /api/shelters/{id}/occupancy` | perkiraan (rumus) / laporan petugas |
| U | tujuan **tanpa batas tercatat**: zona aman (+ tanah tinggi ≥ H+5 m di mode `gedung`) | `Engine._unlimited` | kapasitas **tidak diketahui** |
| c_od | waktu tempuh (s) bangunan → gedung, sama dengan tabel HP: akses tegak lurus + potongan ruas + Dijkstra + `extra_s` (naik tangga) | `reverse_k_best`, `_place_target` | model kecepatan |
| c_oU | waktu ke titik zona aman / tanah tinggi terdekat | Dijkstra terbalik | model kecepatan |
| T | batas waktu jalan = (ETA − jeda mulai − margin)·60 s. ETA dari peringatan (atau `default_eta_min`); jeda 5 & margin 2 menit = parameter area `alloc_delay_min`, `alloc_margin_min` (sama dengan penggeser perencana) | `Engine.budget_s` | **asumsi** |

**Kebijakan kapasitas zona aman.** Zona aman SEMPAT adalah *wilayah* daratan di luar zona bahaya (bukan satu titik):
warga dapat terus bergerak menjauh, sehingga daya tampungnya bukan bilangan gedung. Kapasitasnya **tidak diketahui**;
dimodelkan tanpa batas dan **selalu** diberi label `capacity_known=false` di dasbor/API. Pembatas sebenarnya (kepadatan
jalan, antrean) tidak dimodelkan — keterbatasan. Aturan pemakaian:

* zona aman/tanah tinggi hanya boleh menerima asal yang **benar-benar terjangkau** (waktu hingga);
* bersaing dengan gedung **hanya dengan waktu tempuh asli** (tanpa penalti buatan) — orang tidak dikirim ke zona aman
  selama gedung yang lebih cepat masih punya kursi, kecuali tahap batas waktu (§2) membutuhkannya;
* tidak terjangkau dan gedung penuh → orang itu **tidak teralokasi** dengan alasan (`kapasitas_habis` /
  `tidak_ada_tujuan_terjangkau`), dilaporkan ke petugas, dan HP di bangunan itu menampilkan peringatannya.

## 2. Model (program linear — masalah transportasi berkapasitas)

Variabel: x_od ≥ 0 (orang o→d, hanya untuk d layak & terjangkau), y_o ≥ 0 (ke U, hanya bila c_oU < ∞),
z_o ≥ 0 (tidak teralokasi).

```
untuk setiap o:   Σ_d x_od + y_o + z_o = q_o            (pembukuan tepat)
untuk setiap d:   Σ_o x_od            ≤ κ_d             (kapasitas KERAS)
```

Objektif **leksikografis** (setiap tahap mengunci nilai optimal tahap sebelumnya sebagai kendala):

1. **min Σ z_o** — sebanyak mungkin orang mendapat tujuan. Dilewati bila setiap asal punya U terjangkau (z = 0 pasti
   optimal; z dikunci 0 lewat batas variabel).
2. **min Σ (orang dengan waktu > T)** — sesedikit mungkin orang tiba setelah batas waktu. Bawaan aktif
   (`alloc_prioritize_deadline`, dapat dimatikan per area); dilewati bila T tidak ada.
3. **min Σ c·x + Σ c_oU·y** — total waktu tempuh.

Mengapa leksikografis, bukan bobot: urutan prioritas keselamatan eksplisit dan tidak ada konstanta penalti sembarang.
Mengapa tahap 2: pada kapasitas terbatas, minimum total waktu bisa memberi kursi gedung kepada orang yang sebenarnya
masih sempat ke zona aman, sehingga orang yang *hanya* sempat ke gedung itu tiba terlambat (contoh di
`tests/test_allocation.py::test_banyak_asal_berebut_batas_waktu`). **Harga tahap 2** (diukur, lihat BENCHMARK.md):
lebih banyak asal tidak bisa diwakili tabel HP (lebih banyak `overrides`, unduhan HP lebih besar), dan orang yang
memang tidak sempat ke mana pun bisa dialokasikan ke tujuan yang lebih jauh (waktu maksimum naik) karena kursi gedung
dekat diberikan kepada yang masih bisa tepat waktu. Keputusan kebijakan ini sebaiknya dikonfirmasi pemangku
kepentingan (BPBD); parameter area `alloc_prioritize_deadline=false` mengembalikan ke min total waktu murni.

Tidak dipakai: MILP (tidak perlu — matriks unimodular total, lihat §4); kelompok tak terpisah (satu bangunan boleh
dipecah; HP memilih bagiannya, §6); min waktu maksimum (bottleneck; tidak diimplementasikan — keterbatasan).

**Kandidat & pemeriksaan harga.** Untuk tiap asal: k = 8 gedung termurah (Dijkstra k-label terbalik, dibatasi waktu
ke U). Setelah LP, **pemeriksaan harga** (column generation): Dijkstra terbalik dengan offset dual π_d (+ dual kendala
pengunci); bila ada asal → gedung di luar kandidat dengan biaya tereduksi negatif, kolom ditambahkan dan LP diulang
(maks. 5 putaran). Belum konvergen → `optimal=false` + pesan.

## 3. Solver dan cadangan

* Utama: `scipy.optimize.linprog(method="highs-ipm")` (HiGHS interior point + crossover → solusi basis), batas waktu
  per tahap `SEMPAT_ALLOC_TIME_LIMIT_S` (bawaan 60 s). `highs-ipm` dipilih karena diukur 6–9× lebih cepat daripada
  simplex dual pada masalah besar yang sangat degeneratif ini (BENCHMARK.md).
* Cadangan bila scipy tidak terpasang / solver gagal / batas waktu: **greedy berbasis urgensi** (asal dengan pilihan
  tepat waktu paling sedikit, lalu penyesalan terbesar, lebih dulu; tiap orang ke gedung termurah yang masih ada
  kursi, lalu U, lalu tidak teralokasi). Kapasitas tetap keras; hasil berlabel `method="greedy"`, `optimal=false`,
  dan pesan ditampilkan di dasbor.

## 4. Rekonsiliasi pecahan → bilangan bulat

Matriks transportasi unimodular total; dengan q, κ bulat, solusi basis tahap tanpa kendala tambahan sudah bulat. Baris
pengunci tahap 1–2 dapat menghasilkan pecahan (diukur: 14 variabel dari ~83.000 pada grid besar). Prosedur:

1. x̂ = ⌊x⌋; sisa r_o = q_o − Σ⌊·⌋ dan sisa kapasitas R_d = κ_d − Σ⌊x⌋ (keduanya bulat ≥ 0).
2. Masalah transportasi sisa (unimodular total, tanpa pengunci) dengan bobot leksikografis **diturunkan dari data**:
   W_late = R·C_max + 1, W_unres = R·(W_late + C_max) + 1 (R = Σ r_o, C_max = waktu terbesar). Untuk solusi bulat,
   satu orang lebih sedikit tidak teralokasi selalu lebih berharga daripada seluruh selisih terlambat + waktu, dan
   seterusnya → tepat leksikografis pada sisa. Solusi basis bulat.
   *(Uji MILP independen menemukan bobot awal W_unres = R·W_late + 1 keliru untuk R kecil; sudah diperbaiki.)*
3. LP sisa gagal → greedy hanya untuk orang sisa (`reconcile.method = "greedy_sisa"`, `optimal=false`).
4. Tidak ada pembulatan per variabel; tidak ada langkah yang bisa menambah orang atau melampaui kapasitas.

## 5. Validasi independen (selalu, sebelum hasil dipakai)

`allocation.validate` menghitung ulang dari daftar alokasi akhir (bukan dari keluaran solver): beban ≤ κ_d; semua nilai
bulat ≥ 0; Σ alokasi + tidak teralokasi = q_o tiap asal; tujuan ada di kandidat layak dengan waktu hingga; U hanya bila
terjangkau. Lalu engine memvalidasi **seperti HP**: setiap bangunan berpenghuni disimulasikan dengan aturan HP (tabel +
overrides) dan bebannya harus sama persis dengan alokasi. Gagal salah satu → status `gagal`, hasil **tidak** dipakai,
tabel memakai rute tujuan tercepat tanpa batas kapasitas dengan status `gagal` yang terlihat di HP & dasbor.

## 6. Dari alokasi ke rute HP / online (konsistensi)

Tabel HP = pohon jalur terpendek dengan offset per tujuan. Dual kapasitas π_d ≥ 0 tahap terakhir menjadi offset
(`pen_s`): untuk sebagian besar asal, tujuan termurah di tabel = tujuan alokasinya (complementary slackness). Waktu yang
tampil di HP tetap waktu asli (offset dikurangkan).

Asal yang **tidak** terwakili tabel (dipecah ke >1 tujuan, seri, atau tahap batas waktu mengubah pilihan) dikirim
sebagai `overrides[mode-kelompok]`: `{o, e, t, unres, opts:[{d, n, s, nodes}]}` (ruas & posisi bangunan, porsi orang per
tujuan, waktu, jalur simpul). Aturan HP (`core.js overrideAt` = `Engine._override_at`): override pada **ruas yang sama
dalam 25 m** sepanjang ruas, **yang terdekat**. Agar setiap bangunan cocok dengan override-nya sendiri:

* bangunan tetangga dalam radius itu juga diberi override (berisi tujuan alokasinya sendiri);
* bangunan di titik yang persis sama (mis. berhadapan) digabung menjadi satu kumpulan porsi — HP memang tidak bisa
  membedakannya; beban total tetap tepat.

HP memilih satu opsi dengan **hash stabil ID perangkat** (FNV-1a 32-bit atas `"<id>|<bangunan>"`, porsi = jumlah orang;
`allocation.pick_option` = `core.js pickOption`, diuji pada 300 vektor) dan **mengunci** jalur itu (disimpan di
`localStorage`) selama hasil alokasi yang sama (`computed_at`) berlaku dan warga tidak menyimpang > 150 m. `route()`
server memakai aturan yang sama (`device_key` opsional; tanpa itu: porsi terbesar).

Mode `gedung` online dulu tanpa zona aman sedangkan tabel dengan zona aman (temuan T5): kini **kedua mode selalu
menyertakan zona aman**, sama dengan alokasi.

## 7. Alur peringatan & eksekusi latar belakang

`create_alert`: simpan → **SSE `alert`** → **Web Push** (thread pool) → tabel dasar (Dijkstra; alokasi hanya dari cache
untuk skenario yang persis sama) → antrean alokasi (satu thread pekerja per server, digabung per area). Kunci cache =
(mode, penutupan jalan, H, T, daftar (id, kapasitas tersedia, extra_s)). Selama dihitung, tabel berstatus `pending`:
rute = tujuan tercepat **tanpa batas kapasitas** dan HP/dasbor menyatakannya; hasil skenario lain **tidak pernah**
dipakai. Selesai → tabel diganti, versi naik, SSE `state` (`reason: "kapasitas"`). Gagal → status `gagal`; peringatan
tidak terpengaruh. HiGHS melepaskan GIL (diukur), sehingga server tetap melayani permintaan selama optimasi.

## 8. Status yang bisa muncul (`tables.capacity[mode].status`)

| status | arti | rute |
|---|---|---|
| `ok` | dialokasikan & lolos validasi; semua mendapat tujuan (bisa termasuk zona aman, bisa ada yang terlambat — lihat `totals.late`) | alokasi |
| `kurang` | lolos validasi, tetapi ada orang tanpa tujuan terjangkau (`totals.unresolved`, per alasan) | alokasi |
| `pending` | sedang dihitung untuk skenario ini | tercepat, tanpa batas kapasitas |
| `gagal` | solver/validasi gagal | tercepat, tanpa batas kapasitas |
| `tanpa_penduduk` | area tanpa data penduduk (mis. peta otomatis) — kapasitas TIDAK dihitung | tercepat |
| `tanpa_gedung` | tidak ada gedung berkapasitas untuk mode ini | zona aman / tanah tinggi |

## 9. Struktur hasil (`allocation.solve`)

```
{ status: "ok"|"kurang"|"gagal", errors: [...],
  flows: {indeks_asal: {indeks_gedung | -1 (zona aman/tanah tinggi) | -2 (tidak teralokasi): orang}},
  duals: [π_d], load: [orang per gedung], remaining: [κ_d − beban],
  totals: {demand, to_shelter, to_unlimited, unresolved, unresolved_by_reason, late, time_sum, time_max, time_mean,
           capacity_total, capacity_used},
  meta: {method: "lp_highs"|"greedy", optimal, objective, stages, price_rounds, price_converged, reconcile,
         columns, messages, timing: {candidates_s, solve_s, price_s, reconcile_s, validate_s, encode_s, total_s, all_s}} }
```

API: `GET /api/tables` → `capacity` (ringkasan per mode: status, waktu hitung, metode, totals, per gedung
kapasitas/okupansi/dialokasikan/sisa/penuh, label zona aman `capacity_known=false`) dan `overrides`;
`GET /api/allocation?mode=` (petugas/perencana) → baris per bangunan & kelompok: orang, tujuan, jenis, waktu, terlambat;
`PUT /api/shelters/{id}/occupancy` (petugas/perencana) → orang yang sudah di dalam.

## 10. Kompleksitas

Kandidat: k·(E log V) per kelompok. LP: |O| baris kesetaraan + |D| baris kapasitas, ~|O|·(k+1) kolom (grid besar:
~10.000 asal, ~83.000 kolom). Tahap 1–3 + pemeriksaan harga + rekonsiliasi + enkode (satu Dijkstra terpotong per
(kelompok, gedung) yang dipakai override). Angka terukur: BENCHMARK.md.

## 11. Keterbatasan yang diketahui

* Penduduk = model (4 orang/rumah, 15 % rentan, di rumah), bukan data kependudukan atau posisi nyata; peta otomatis tidak
  punya penduduk → status `tanpa_penduduk` (kapasitas tidak dihitung, dinyatakan terbuka).
* Kapasitas zona aman tidak diketahui; kepadatan & antrean di jalan/tangga tidak dimodelkan; kapasitas gedung = rumus
  FEMA dari luas tapak; okupansi nyata hanya bila petugas melaporkannya.
* Rute HP dari posisi sembarang (bukan bangunan berpenghuni model) mengikuti tabel: konsisten dengan harga alokasi,
  tetapi orang itu bukan bagian dari permintaan yang dialokasikan.
* Pemilihan opsi pada asal yang dipecah memakai hash perangkat → kepatuhan porsi bersifat statistik; radius 25 m dan
  batas lepas kunci 150 m adalah heuristik.
* Tahap batas waktu menambah jumlah overrides (unduhan HP lebih besar) dan dapat menaikkan waktu maksimum (§2).
* Selama `pending` (beberapa detik pada grid besar), rute belum memperhitungkan kapasitas — dinyatakan di HP & dasbor.
* Semua validasi dilakukan pada data sintetis; tidak ada validasi lapangan.
