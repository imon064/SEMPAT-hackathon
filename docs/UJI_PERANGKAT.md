# Protokol uji di HP Android kelas menengah

Emulasi di `tools/bench_perf.py` (CPU 4x lebih lambat, 4G lambat) membantu membandingkan versi, tetapi tidak menggantikan uji di HP sungguhan. GPU, memori, suhu, dan penghemat baterai HP nyata tidak ikut diemulasikan. Protokol ini dipakai supaya hasil uji perangkat bisa dibandingkan dari waktu ke waktu.

## Perangkat dan kondisi

* Minimal satu HP Android kelas menengah (RAM 3 sampai 4 GB, chipset setara Snapdragon 6xx/Helio G8x, Android 11 ke atas), Chrome versi terbaru. Catat merek, model, versi Android, versi Chrome.
* Baterai di atas 50 %, mode hemat daya mati, aplikasi lain ditutup.
* Jaringan: (a) Wi-Fi yang sama dengan server, (b) data seluler 4G di lokasi uji. Catat operator dan kekuatan sinyal.
* Server: laptop di jaringan yang sama, `python run.py --host 0.0.0.0`. GPS dan notifikasi di HP butuh alamat https: pakai `python run.py --tunnel` (lihat README).

## Area uji

* Desa sintetis bawaan (sekitar 400 bangunan).
* Area besar sintetis: jalankan `python tools/bench_perf.py --sizes 5000,20000 --profiles desktop` sekali untuk memastikan alat berjalan, lalu salin direktori area dari skrip (atau buat area nyata lewat "Analisis area di layar" di halaman perencana) dan catat jumlah bangunannya.

## Langkah dan yang dicatat

1. Hapus data situs di Chrome (Setelan, Situs, SEMPAT, Hapus data). Buka halaman warga. Catat waktu dari ketuk tautan sampai peta dan panel "Lokasi Anda" tampil (stopwatch, ulangi 3 kali, ambil median).
2. Ulangi untuk halaman petugas dan perencana setelah login.
3. Remote debugging (chrome://inspect dari laptop, kabel USB): tab Performance, rekam muat ulang halaman. Catat waktu sampai `load` peta, panjang tugas utama terpanjang, dan heap JS.
4. Di halaman petugas, zoom ke 17, nyalakan "Bangunan 3D", geser peta 10 detik. Rekam dengan Performance dan catat laju bingkai rata-rata dan bingkai yang jatuh. Perhatikan apakah HP menjadi panas.
5. Mode offline: setelah peta warga terbuka, nyalakan mode pesawat, muat ulang halaman. Peta, rute, dan tanda "Offline" harus tetap tampil.
6. Pembaruan inkremental: dengan halaman warga terbuka di HP, survei satu gedung dari laptop (halaman petugas). Status gedung di HP harus berubah dalam beberapa detik tanpa peta berkedip atau kamera berpindah.
7. Simulasi peringatan dari halaman petugas: catat waktu sampai layar peringatan muncul di HP dan apakah getaran/suara aktif.

## Format hasil

| tanggal | perangkat | jaringan | peran | area (bangunan) | siap (median 3x) | tugas terpanjang | heap | fps 3D | offline | tambalan | catatan |
|---|---|---|---|---|---|---|---|---|---|---|---|

Simpan hasil di `docs/perf_device_results.md`. Jangan mencampur angka emulasi dengan angka HP nyata di tabel yang sama.

## Kriteria awal (usulan, perlu disepakati tim)

* Warga, desa sampai 5.000 bangunan, 4G: peta siap di bawah 5 detik.
* Petugas, desa sampai 5.000 bangunan, 4G: peta siap di bawah 8 detik.
* 3D di zoom 17: rata-rata di atas 30 fps, tanpa macet lebih dari 1 detik.
* Mode offline dan tambalan survei berjalan seperti langkah 5 dan 6.
