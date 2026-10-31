# Performa SEMPAT: skenario uji dan hasil

Dokumen ini mencatat cara mengukur performa SEMPAT secara berulang dan hasil terakhir di mesin pengembangan. Semua area uji adalah data SINTETIS. Angka absolut bergantung mesin. Yang bermakna adalah perbandingan antarukuran area dan antarversi kode di mesin yang sama.

## Cara menjalankan

```
python tools/bench_perf.py --sizes 400,5000,20000 --profiles desktop,android --json docs/perf_results.json
python tools/bench_payload.py --sizes 400,5000,20000      # ukuran payload saja, tanpa peramban
```

`bench_perf.py` membuat satu area sintetis per ukuran di direktori data sementara, menyalakan server, mengukur endpoint lewat HTTP lokal, lalu menjalankan `tests/perf/perf.js` (Chromium dari Playwright) untuk ketiga peran. Perlu Node + Playwright yang sudah terpasang (sama dengan uji E2E).

Area uji: grid jalan 50 m, rumah poligon 8 titik, 3 % gedung bertingkat, ketinggian naik 2 m per 100 m dari garis pantai (jadi ada zona bahaya dan zona aman). Area 400 bangunan seluruhnya di zona bahaya tanpa zona aman di dalam area, sehingga grid waktunya kosong. Jaringan jalan uji hanya punya simpul di persimpangan, jauh lebih sedikit dari OSM nyata, jadi tabel rute dan grid waktu di sini lebih kecil dari kondisi lapangan.

## Profil

| profil | arti |
|---|---|
| desktop | viewport 1366x860, tanpa throttling |
| android | EMULASI HP kelas menengah: CPU 4x lebih lambat, RTT 150 ms, 1,6 Mbps turun, 750 kbps naik, viewport 390x844 (setara profil "mobile" Lighthouse). Ini bukan pengukuran di HP sungguhan. Protokol uji di HP nyata ada di [UJI_PERANGKAT.md](UJI_PERANGKAT.md). |

Metrik peramban:

* area terunduh: ms sejak navigasi sampai data `/api/area` diterima aplikasi.
* peta siap: ms sampai area dan tabel rute ada DAN peta selesai menggambar pertama kali (`idle`). Ubin peta dasar dari internet tidak ditunggu karena lingkungan uji tidak punya akses internet.
* GeoJSON gedung: waktu membangun GeoJSON seluruh gedung sekali (rata-rata 5 kali).
* heap JS: memori JavaScript halaman setelah siap.
* bingkai/detik geser 3D: zoom 17, lapisan 3D menyala, peta digeser 2,5 detik. Chromium headless memakai GPU perangkat lunak (SwiftShader), jadi angka ini terutama bergantung jumlah piksel layar dan TIDAK mewakili GPU HP. Hanya dipakai sebagai indikasi relatif.

## Hasil terakhir (1 November 2026, mesin pengembangan, data sintetis)

Server lokal tanpa throttling:

| bangunan | server siap (proses mulai sampai tabel rute ada) | /api/area warga (dingin/hangat, gzip) | /api/area petugas (dingin/hangat, gzip) | tabel HP | grid waktu (dingin/cache) | survei: tambalan vs muat ulang penuh warga |
|---|---|---|---|---|---|---|
| 399 | 4,0 s | 0,004/0,002 s, 3 KB | 0,018/0,006 s, 30 KB | 0,002 s, 1 KB | 0,003/0,002 s | 599 B vs 3 KB |
| 4.983 | 4,0 s | 0,012/0,004 s, 15 KB | 0,229/0,060 s, 365 KB | 0,003 s, 3 KB | 0,013/0,002 s | 603 B vs 15 KB |
| 19.926 | 4,5 s | 0,037/0,011 s, 54 KB | 0,732/0,262 s, 1.492 KB | 0,004 s, 8 KB | 0,047/0,003 s | 600 B vs 54 KB |

Peramban, profil android (emulasi):

| bangunan | peran | area terunduh | peta siap | GeoJSON gedung | heap JS |
|---|---|---|---|---|---|
| 399 | warga | 2.844 ms | 3.616 ms | 0 ms | 12 MB |
| 399 | petugas | 2.788 ms | 3.641 ms | 4 ms | 10 MB |
| 399 | perencana | 2.895 ms | 3.475 ms | 1 ms | 15 MB |
| 4.983 | warga | 3.043 ms | 3.892 ms | 0 ms | 16 MB |
| 4.983 | petugas | 4.729 ms | 5.748 ms | 6 ms | 15 MB |
| 4.983 | perencana | 4.730 ms | 5.850 ms | 6 ms | 19 MB |
| 19.926 | warga | 3.158 ms | 4.666 ms | 2 ms | 15 MB |
| 19.926 | petugas | 11.133 ms | 13.194 ms | 20 ms | 47 MB |
| 19.926 | perencana | 11.347 ms | 13.687 ms | 16 ms | 55 MB |

Peramban, profil desktop:

| bangunan | peran | area terunduh | peta siap | GeoJSON gedung | heap JS |
|---|---|---|---|---|---|
| 399 | warga | 697 ms | 1.059 ms | 0 ms | 16 MB |
| 399 | petugas | 294 ms | 1.025 ms | 0 ms | 10 MB |
| 399 | perencana | 213 ms | 743 ms | 0 ms | 15 MB |
| 4.983 | warga | 646 ms | 1.375 ms | 0 ms | 18 MB |
| 4.983 | petugas | 444 ms | 1.463 ms | 2 ms | 22 MB |
| 4.983 | perencana | 326 ms | 1.451 ms | 1 ms | 21 MB |
| 19.926 | warga | 775 ms | 1.696 ms | 1 ms | 16 MB |
| 19.926 | petugas | 1.167 ms | 3.461 ms | 6 ms | 84 MB |
| 19.926 | perencana | 687 ms | 3.077 ms | 3 ms | 63 MB |

Data mentah (termasuk penanda waktu per tahap dan bingkai/detik): `docs/perf_results.json`.

## Apa yang dioptimasi di Milestone 4 dan buktinya

* Payload per peran (M2): warga hanya menerima gedung yang relevan. Di area 19.926 bangunan, warga mengunduh 54 KB gzip, petugas 1,5 MB.
* Pembaruan inkremental: survei satu gedung tidak lagi membuat semua klien mengunduh ulang area. Server menyimpan revisi + epoch dan ID gedung yang berubah. Klien mengambil `/api/area/patch` (sekitar 600 B) lalu memperbarui satu fitur peta lewat `GeoJSONSource.updateData`, tanpa membangun ulang GeoJSON. Terbukti di `tests/e2e/map_e2e.js` (warga, petugas, perencana: jumlah muat ulang penuh tetap, jumlah tambalan bertambah) dan `tests/test_milestone4.py`.
* Serialisasi malas: JSON area dibangun saat pertama diminta lalu disimpan, jadi survei beruntun tidak membangun ulang JSON belasan MB setiap kali.
* Sumber GeoJSON gedung dibatasi `maxzoom: 16` (zoom lebih dekat memakai pembesaran ubin z16; presisi ubin z16 sekitar 7,5 cm). Uji A/B di area 19.926 bangunan, profil desktop: heap petugas turun dari 147 sampai 174 MB menjadi 60 sampai 84 MB, peta siap 3,3 s menjadi 2,7 sampai 3,4 s (variasi antarjalan cukup besar).

Profil petugas di area 19.926 bangunan menunjukkan bahwa pembangunan GeoJSON (6 ms) bukan hambatan. Waktu habis untuk transfer data (profil android: 1,5 MB di 1,6 Mbps sekitar 7,5 s) dan penggambaran poligon. Di GPU perangkat lunak satu bingkai penuh 20 ribu poligon memakan sekitar 440 ms, angka yang tidak mewakili GPU HP.

## Batas yang masih ada

* Petugas dan perencana di area sangat besar (sekitar 20 ribu bangunan) di jaringan lambat butuh sekitar 13 detik sampai peta siap, hampir seluruhnya untuk transfer. Area desa biasa (400 sampai 5.000 bangunan) di bawah 6 detik dalam emulasi android. Opsi lanjutan bila dibutuhkan: ubin vektor per zoom atau mengirim rumah tanpa poligon untuk petugas. Belum dikerjakan karena menambah kompleksitas dan belum terbukti perlu untuk skala desa.
* Belum ada pengukuran di HP Android nyata. Lihat [UJI_PERANGKAT.md](UJI_PERANGKAT.md).
* Laju bingkai 3D belum terukur dengan GPU nyata.
