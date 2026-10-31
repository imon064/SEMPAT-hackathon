# SEMPAT: pengembangan terintegrasi Milestone 1 sampai 4

Basis kerja: isi zip `SEMPAT-hackathon-capacity` (revisi lokal yang belum di-push), dicatat sebagai commit `7371835`. Semua perubahan ada di branch lokal `integrated-dev`. Tidak ada push, tidak ada perubahan pada `main`, tidak ada operasi Git destruktif.

Tanda bukti: [E] terbukti oleh uji/eksekusi, [K] dari pembacaan kode, [A] asumsi, [?] belum bisa dibuktikan di lingkungan ini.

## Keputusan keselamatan

| kode | keputusan | sumber |
|---|---|---|
| D-2 | Margin elevasi per kepercayaan: tinggi 0 m, sedang 3 m, rendah 5 m, tidak ada data 0 m dan gedung tidak bisa terverifikasi. Kedalaman konservatif = H − (elevasi − margin). Nominal dan konservatif ditampilkan terpisah; keputusan memakai konservatif. Margin perlu divalidasi BPBD. | disetujui pemilik proyek |
| D-3 | Gedung Perlu Verifikasi ditampilkan ke warga tanpa rute dan tanpa alokasi, hanya bila warga tidak sempat ke tanah tinggi dan tidak ada gedung terverifikasi yang terjangkau. | disetujui pemilik proyek |
| P-1 | Gedung berakses jam tertentu dengan jam buka tercatat boleh terverifikasi (dipakai hanya saat terbuka sepanjang jendela peringatan). Shelter resmi tetap wajib akses 24 jam. | keputusan saya, **perlu konfirmasi** |
| P-2 | Survei lama tanpa isian kondisi gedung tidak lagi memenuhi syarat verifikasi sampai disurvei ulang. Jumlah lantai dari survei lama tanpa konfirmasi "dihitung di lokasi" tetap dianggap perkiraan. | keputusan saya, **perlu konfirmasi** |

Kebijakan tier yang berlaku: Terverifikasi menjadi tujuan hanya bila semua syarat survei terpenuhi dan lantai aman konservatif tersedia; Perlu Verifikasi masuk daftar verifikasi BPBD; Tidak Aman tidak pernah menjadi tujuan.

## Milestone 1: keselamatan dan mesin keputusan

* Satu sumber kebenaran untuk kelayakan, tier, kapasitas, dan batas waktu (`tsunami.verification`, `tsunami.assess`, `engine.deadline_policy`, `core.js isLate`). [E]
* Alokasi mode gedung hanya memakai gedung terverifikasi + tanah tinggi; gedung Perlu Verifikasi hanya info (`dests.unverified`). [E]
* Survei: jumlah lantai baru dianggap data survei bila dikonfirmasi dihitung di lokasi; kondisi gedung wajib; elevasi terukur wajib disertai metode. [E]
* HP warga: verdict dan tawaran evakuasi vertikal memakai satu fungsi batas waktu; 146 kasus HP = alokasi. [E]
* Regresi D-1 sampai D-7: `tests/test_milestone1.py` (18 uji). [E]

## Milestone 2: fondasi data

* Payload per peran: warga hanya gedung relevan. Area 19.926 bangunan: 15,3 MB / 1,66 MB gzip (sebelum) menjadi 538 KB / 53 KB untuk warga dan 13,0 MB / 1,42 MB untuk petugas. [E] (`tools/bench_payload.py`)
* Grid penduduk (format CSV HRSL dan sejenis): sel dibagi ke rumah, ke rumah terdekat ≤ 45 m, atau ke asal virtual di jalan ≤ 300 m; semua berlabel sumber. Tanpa grid: model 4 orang/rumah berlabel PERKIRAAN MODEL. [E] dengan grid sintetis; [?] belum diuji dengan berkas HRSL asli (sandbox tidak bisa mengakses HDX/AWS).
* Geometri: `building:min_level` pada building:part, duplikat footprint (titik berat < 2 m dan selisih luas < 15 %). [E]
* Satu parameter tinggi lantai (4,0 m). Provenance: `meta.snapshot` (waktu bangun, basis OSM, sumber elevasi, sintetis) dan versi model (`params_sha`, skema, metode penduduk). [E]
* Google Open Buildings 2.5D tetap opsional; tidak diklaim berfungsi tanpa uji Earth Engine. [?]

## Milestone 3: peta dan visualisasi

* Kontrol lapisan independen dan legenda dinamis yang memakai definisi warna/label yang sama dengan backend; tier dibedakan dengan simbol dan pola garis, tidak hanya warna. Legenda bisa dilipat dan terlipat bawaan di HP. [E]
* 3D `fill-extrusion` mulai sekitar zoom 16; tinggi perkiraan digambar pucat dan berlabel; gedung tanpa data lantai tidak diekstrusi. [E]
* Zona bahaya resmi (poligon) vs perkiraan aturan (grid 80 m berlabel "BUKAN peta resmi"). [E]
* Kepadatan penduduk hanya dari grid nyata yang diunggah; model 4 orang/rumah tidak pernah digambar sebagai kepadatan. [E]
* Grid waktu evakuasi 30 m, pita 3 menit, dari tabel Dijkstra yang sama dengan rute HP; sel > 60 m dari jalan kosong; label menyatakan resolusi bukan akurasi. [E]
* Regresi: `tests/test_milestone3.py` (10 uji), `tests/e2e/map_e2e.js`. [E]

## Milestone 4: performa, bukti, integrasi

* Pembaruan inkremental: revisi + epoch per area, `GET /api/area/patch`, event SSE membawa ID gedung, klien menambal gedung itu saja dan memperbarui satu fitur peta (`GeoJSONSource.updateData`). Satu survei: sekitar 600 B alih-alih unduh ulang area (warga 54 KB, petugas 1,5 MB di area 20 ribu bangunan). [E]
* Serialisasi JSON area malas dan disimpan per revisi. [E]
* Sumber GeoJSON gedung `maxzoom: 16`: heap petugas di area 20 ribu bangunan turun sekitar separuh. [E] (A/B di mesin pengembangan)
* Skenario performa berulang untuk 400, 5.000, dan 20.000 bangunan, profil desktop dan emulasi Android. Hasil di [PERFORMA.md](PERFORMA.md). Warga di emulasi Android siap dalam 3,6 sampai 4,7 s untuk semua ukuran. [E] emulasi; [?] belum diuji di HP nyata, protokol di [UJI_PERANGKAT.md](UJI_PERANGKAT.md).
* Paket Bukti Tsunami Ready (`/perencana/bukti`): 12 indikator UNESCO-IOC dengan status bukti (bukan status kepatuhan), peta draf SVG tanpa ubin internet, orang berisiko, sebaran waktu tempuh per pita 3 menit, gedung terverifikasi, daftar verifikasi, kegiatan tercatat, kebijakan dan parameter, keterbatasan, provenance. PDF lewat dialog cetak peramban, tanpa pustaka baru. Area sintetis tidak pernah berstatus "bukti tersedia". [E] (PDF 6 halaman dibuat di uji E2E)

## Hasil uji terakhir

| suite | hasil |
|---|---|
| `python -m unittest discover tests` | 226 uji OK [E] |
| `python tests/make_js_fixtures.py && node tests/js/core.test.mjs` | OK: 162 kasus rute, 88 kasus override, 300 vektor pickOption, 146 kasus batas waktu [E] |
| `node tests/e2e/e2e.js` | 54 cek OK [E] |
| `node tests/e2e/map_e2e.js` | 50 cek OK [E] |
| `python tools/bench_perf.py` | lihat PERFORMA.md [E] |

## Yang belum selesai atau belum terbukti

* Uji dengan berkas HRSL asli, Overpass/DEM langsung, dan Earth Engine. [?] Lingkungan pengembangan tidak punya akses ke layanan tersebut.
* Uji di HP Android nyata dan laju bingkai 3D dengan GPU nyata. [?]
* Petugas/perencana di area sekitar 20 ribu bangunan pada jaringan lambat butuh sekitar 13 s (hampir seluruhnya transfer 1,5 MB). Opsi lanjutan (ubin vektor, rumah tanpa poligon untuk petugas) belum dikerjakan.
* Konfirmasi P-1 dan P-2 di atas.
* Validasi margin elevasi D-2 bersama BPBD.
