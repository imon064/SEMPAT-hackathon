# SEMPAT — Sistem Evakuasi Mandiri, Pintar, dan Tepat

SEMPAT membantu warga pesisir menyelamatkan diri dari tsunami dalam tiga langkah, **di lokasi mana pun di Indonesia**:

1. **Peringatan.** Saat ada peringatan tsunami, HP bergetar dan menampilkan notifikasi sistem, walau aplikasi tertutup (Web Push). Sumber peringatan: data terbuka BMKG atau petugas.
2. **Ke tempat aman.** HP membaca GPS. SEMPAT mengunduh peta jalan nyata di sekitar Anda (OpenStreetMap) dan ketinggian tanah (DEM), lalu menunjukkan panah dan rute jalan kaki tercepat ke tempat tinggi. Titik biru bergerak mengikuti HP.
3. **Ke posko terdekat.** Setelah aman, SEMPAT menunjukkan posko yang **buka dan belum penuh** (diperbarui petugas). Bila belum ada posko, SEMPAT menunjukkan fasilitas umum terdekat dari peta (puskesmas, kantor polisi, balai), diberi label "belum diverifikasi".

Langkah 0 selalu berlaku: **gempa kuat atau lama berarti langsung lari ke tempat tinggi**, tanpa menunggu aplikasi. Tombol *"Saya merasakan gempa kuat"* memulai evakuasi mandiri.

| Halaman | Pengguna | Fungsi |
|---|---|---|
| `/` | Warga (tanpa login) | Status zona lokasi Anda, simulasi di lokasi sendiri, notifikasi, panah arah, hitung mundur, posko terdekat, tetap jalan tanpa internet |
| `/petugas` | SAR / BPBD lapangan | Pilih wilayah kerja, kirim/cabut peringatan, tambah titik bantuan, ubah status sekali ketuk, tandai jalan putus, kode QR untuk HP |
| `/perencana` | Perencana BPBD | Analisis desa mana pun langsung dari peta: siapa yang tidak sempat mengungsi, rekomendasi gedung shelter vertikal, panduan cetak + QR |

SEMPAT **tidak membuat halaman untuk BMKG**. SEMPAT membaca data gempa terbuka BMKG (`autogempa.json`) tiap 60 detik dan selalu mencantumkan "Sumber: BMKG".

---

## 1. Menjalankan

Prasyarat: **Python 3.10 atau lebih baru** (https://www.python.org/downloads/). Saat memasang, centang *Add python.exe to PATH*. Server butuh internet untuk mengunduh peta dan data BMKG.

### A. Di laptop saja
Klik dua kali `start_windows.bat`. Saat pertama dijalankan, skrip memasang paket, lalu browser terbuka di `http://localhost:8000`.

### B. Agar juri bisa mencoba di HP masing-masing (disarankan untuk demo)
Klik dua kali **`start_windows_online.bat`**.
- Skrip membuat alamat **https publik** lewat Cloudflare Quick Tunnel (gratis, tanpa akun). `cloudflared` diunduh otomatis sekali.
- Alamatnya tercetak di jendela server, misalnya `https://kata-acak.trycloudflare.com`.
- Kode QR-nya ada di halaman **/petugas** (kartu *Uji & demo*).

Browser hanya mengizinkan **GPS dan notifikasi di HP** lewat alamat https. Alamat `http://192.168.x.x` tidak cukup. Alamat tunnel berganti setiap kali dijalankan ulang.

### C. Produk live permanen (untuk Devpost)
Repo ini siap di-deploy sebagai Docker:
1. Unggah repo ke GitHub.
2. Di https://render.com pilih **New → Blueprint** lalu pilih repo. `render.yaml` sudah disiapkan (paket gratis).
3. Buat kunci notifikasi sekali dengan `python -m backend.webpush`. Isi `SEMPAT_VAPID_PRIVATE` di *Environment* Render dengan nilainya, supaya langganan notifikasi tidak hilang saat server dimulai ulang.
4. Render memberi alamat `https://sempat-xxxx.onrender.com`. Alamat ini yang dikirim ke Devpost.

Catatan untuk paket gratis Render:
- Server tidur setelah 15 menit tanpa pengunjung, dan pembukaan pertama ± 50 detik. Pakai pemantau gratis (mis. UptimeRobot) yang membuka `/api/meta` tiap 10 menit.
- Data (titik, peringatan) kembali kosong setiap kali deploy ulang.

### Manual / pilihan lain
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py --host 0.0.0.0 --tunnel
```
- `--no-bmkg` mematikan pembacaan BMKG.
- `--area <id>` memilih area bawaan.
- `--rebuild-synthetic` membangun ulang desa uji.

**Akun demo:** **petugas / petugas123** dan **perencana / perencana123**. Ganti sebelum alamat dibagikan:
```bat
python -m backend.auth set petugas KataSandiBaru
python -m backend.auth set bpbd_malang KataSandiLain perencana
```

## 2. Cara juri mencoba (± 3 menit)

1. Pindai kode QR di halaman petugas, atau buka alamat https di HP. Izinkan **lokasi**.
2. SEMPAT menyiapkan peta di sekitar posisi juri. Ini sekali saja, ± 10–30 detik. Kartu atas lalu menunjukkan **status zona lokasi**, misalnya "Lokasi Anda di zona bahaya tsunami — ketinggian ± 4 m, 600 m dari pantai" atau "di luar zona bahaya".
3. Ketuk **Coba simulasi di lokasi saya** dan izinkan notifikasi. **Kunci layar HP.** 10 detik kemudian HP bergetar dengan notifikasi *PERINGATAN DINI TSUNAMI — SIMULASI*.
4. Buka notifikasi lalu ketuk **Tunjukkan jalan**. Hasilnya: panah, jarak, waktu tempuh, hitung mundur, dan penilaian *sempat / mepet / tidak sempat*. Berjalanlah: titik biru, panah, dan sisa jarak mengikuti HP.
5. Setelah sampai di tempat aman, muncul daftar posko dan fasilitas terdekat.
6. Jika juri tidak di pesisir (status "di luar zona bahaya"), pilih **Pilih di peta** lalu salah satu lokasi contoh: Padang, Banda Aceh, Pacitan, atau Palu. Bisa juga mengetuk titik mana pun di peta.
7. Peran petugas (laptop): buka `/petugas`, **Ganti** wilayah ke peta yang baru dibuat HP juri, lalu tambah posko di dekatnya. Posko itu langsung muncul di HP. **Mulai simulasi peringatan** dengan pilihan *Semua HP* membuat layar semua HP yang membuka SEMPAT berubah merah. **Cabut peringatan** (catatan wajib) mengakhirinya.
8. Peran perencana: buka `/perencana`, geser peta ke desa mana pun, lalu **Analisis area di layar**. Hasilnya: jumlah warga yang tidak sempat, rekomendasi shelter, dan panduan + QR per rumah.

Notifikasi **gempa nyata** dari BMKG juga dikirim ke HP yang berlangganan dalam radius 300 km (info gempa) atau 600 km (berpotensi tsunami). Gempa terbaru BMKG selalu tampil di halaman warga.

> **iPhone:** notifikasi web hanya bisa diterima bila SEMPAT dipasang ke layar utama (Bagikan → *Tambahkan ke Layar Utama*, iOS 16.4+) lalu dibuka dari ikonnya. Android/Chrome langsung bisa.

## 3. Data nyata

- **Peta otomatis (warga & petugas):** kotak ± 4 × 4 km di sekitar posisi, berisi jalan, pantai, sungai, dan fasilitas dari OpenStreetMap (Overpass API), plus ketinggian dari AWS Terrain Tiles (cadangan: Open-Meteo/Copernicus DEM). Hasilnya disimpan di `data/areas/live-…` dan dipakai bersama. Bila di kotak itu belum ada tempat aman yang terjangkau (dataran rendah luas), kotak diperbesar (7 km) dan digeser ke arah daratan.
- **Analisis desa (perencana):** tombol *Analisis area di layar* juga mengunduh **bangunan** untuk area ≤ 16 km² yang tampil di peta.
- **Lewat baris perintah:**
  ```bat
  python tools/prepare_area.py --id tambakrejo --name "Desa Tambakrejo" --bbox S,W,N,E
  ```
  Opsi tambahan:
  - `--hazard-geojson bahaya.geojson`: peta bahaya resmi InaRISK/BPBD. **Sangat disarankan.** Tanpa peta resmi, zona bahaya memakai aturan *ASUMSI*: ketinggian < 20 m dan < 3 km dari pantai.
  - `--official-csv titik.csv`: titik kumpul resmi, kolom `name,kind,lat,lon`.
  - `--elev-geotiff demnas.tif`: DEMNAS yang lebih teliti.
- Bangunan dan jalan setapak di OSM sering belum lengkap. Lengkapi lewat pemetaan partisipatif sebelum hasil perencana dipakai untuk keputusan.

## 4. Cara SEMPAT menghitung (dan seberapa akurat)

Penentuan aman/tidak aman bersifat **deterministik** (aturan geografis + perhitungan rute), **bukan AI**. Data yang sama selalu memberi hasil yang sama, dan setiap angka bisa ditelusuri.

**Graf jalan.** Jalan dan jalur pejalan kaki dari OSM menjadi graf berarah. Biaya naik ≠ turun.

**Kecepatan berjalan:**
- 1,0 m/detik (umum) dan 0,5 m/detik (lansia/difabel), sesuai pedoman MLIT Jepang.
- Lereng memakai fungsi Tobler yang dinormalkan: `W(s) = 6·e^(−3,5·|s+0,05|) / W(0)`.
- Tangga 0,21 m/detik vertikal.

**Tempat aman:** di luar zona bahaya dan di atas ambang + 2 m, atau ≥ 3 km dari pantai. Gedung shelter vertikal terdaftar juga menjadi tujuan, termasuk waktu naik tangga.

**Rute:**
- Dijkstra multi-sumber terbalik memberi waktu terpendek dan "langkah berikutnya" untuk setiap simpul jalan.
- Tabel ini disimpan di HP, sehingga rute tetap dihitung **tanpa internet**. Waktu di HP sama dengan server: selisih ≤ 1,5 detik pada 74 kasus uji.
- Posisi GPS ditempelkan ke ruas jalan terdekat (proyeksi tegak lurus).

**Posko:**
- Pencarian k-terdekat ke titik *kosong / agak ramai / ramai*. Titik *penuh/tutup* tidak ditawarkan.
- Selama peringatan aktif, rute posko tidak melewati zona bahaya.

**Perencana:**
- Kapasitas gedung = luas × (lantai − 1) × 0,5 ÷ 0,93 m²/orang (FEMA P-646).
- Rekomendasi dipilih secara *greedy maximum coverage* dengan batas kapasitas.

**Notifikasi:**
- Web Push standar (RFC 8291 enkripsi aes128gcm + RFC 8292 VAPID), diimplementasikan dengan pustaka `cryptography`.
- Langganan menyimpan lokasi terakhir HP, sehingga peringatan hanya dikirim ke HP dalam radiusnya.

**Pengujian:**
- `python -m unittest discover tests` (26 uji):
  - Dijkstra dibandingkan dengan Bellman-Ford brute force.
  - Dekoder PNG untuk kelima jenis filter, dan elevasi Terrarium.
  - Pembangun peta otomatis, termasuk perluasan ke daratan.
  - Enkripsi push, dicek silang dengan implementasi independen.
  - Alur API dan cakupan peringatan per wilayah.
- `python tests/make_js_fixtures.py && node tests/js/core.test.mjs`: rute di HP sama dengan server.
- Uji browser ujung-ke-ujung `tests/e2e/e2e.js`: GPS bergerak, simulasi pribadi, petugas, perencana, dan offline. Jalankan server dengan `SEMPAT_FAKE_OSM=1` (data uji tanpa internet).

**Batasan yang perlu disampaikan jujur:**
- BMKG tidak menerbitkan perkiraan waktu tiba per desa di data terbuka, jadi SEMPAT memakai nilai bawaan 20 menit.
- Akurasi bergantung pada kelengkapan OSM dan resolusi DEM (± 30–90 m; galat tinggi beberapa meter).
- Model tidak memperhitungkan kemacetan pejalan kaki atau kerusakan jalan yang belum dilaporkan.
- Peta otomatis memakai aturan zona bahaya sementara sampai peta bahaya resmi dimuat.
- SEMPAT adalah alat bantu, **bukan pengganti** peringatan resmi BMKG, sirene, dan arahan petugas.

## 5. Prinsip HCI yang diterapkan

| Prinsip | Wujud di SEMPAT |
|---|---|
| Visibilitas status sistem (Nielsen #1) | Status zona lokasi Anda; indikator *Terhubung/Offline* + jam data; hitung mundur menempel di atas; "diperbarui X menit lalu oleh petugas"; pesan saat peta sedang diunduh |
| Cocok dengan dunia nyata (#2) | Peta jalan nyata dengan nama jalan; bahasa sehari-hari ("Belok kiri dalam 120 m"); meter dan menit |
| Kendali & kebebasan pengguna (#3) | GPS ↔ pilih di peta; batalkan/akhiri simulasi; "Saya sudah di tempat aman" / "Ternyata saya belum aman"; peta berhenti mengikuti bila digeser, tombol *Pusatkan ke saya* |
| Konsistensi (#4) | Merah = bahaya, hijau = aman, kuning = mepet di semua peran; status selalu teks + warna |
| Pencegahan kesalahan (#5) | Simulasi selalu berlabel SIMULASI; pencabutan wajib dengan catatan; konfirmasi hapus; data OSM ditandai "belum diverifikasi" |
| Mengenali, bukan mengingat (#6) | Satu tombol utama per keadaan; lokasi contoh siap pilih; pilihan berupa kartu/centang |
| Minimalis (#8) | Layar peringatan hanya berisi bahaya, tindakan, waktu, sumber, dan satu tombol |
| Pemulihan dari galat (#9) | Pesan menyebut penyebab dan jalan keluar (izin lokasi, https, iPhone) |
| Bantuan (#10) | "Cara memakai & cara SEMPAT menghitung"; transparansi data & asumsi di dasbor perencana |
| Pesan peringatan (Mileti & Sorensen) | Bahaya, lokasi, tindakan, waktu, sumber; notifikasi sistem dengan getaran panjang |
| Aksesibilitas (WCAG 2.1 AA) | Kontras ≥ 4,5:1, target sentuh ≥ 48 px, warna tidak berdiri sendiri, `aria-live`, keyboard, *reduce motion*, mode gelap |
| Kondisi darurat | Bekerja offline (service worker + tabel rute + ubin peta tersimpan); langkah 0 "gempa kuat = langsung lari"; kecepatan kelompok rentan |

## 6. Struktur folder

```
run.py                       menjalankan server (+ --tunnel untuk https publik)
start_windows.bat            laptop saja
start_windows_online.bat     + alamat https untuk HP/juri
Dockerfile, render.yaml      deploy cloud
backend/
  app.py                     API FastAPI + SSE + banyak area + Web Push + BMKG
  live_area.py               peta otomatis di sekitar posisi mana pun
  osm_fetch.py               Overpass, elevasi (Terrarium/Open-Meteo), Nominatim
  pngdec.py                  dekoder PNG kecil untuk ubin elevasi
  webpush.py                 enkripsi & pengiriman notifikasi (RFC 8291/8292)
  engine.py                  graf, Dijkstra, rute, tabel offline, perencana shelter
  area_builder.py            OSM + elevasi -> zona bahaya, tempat aman, bangunan
  speed.py, geo.py           kecepatan berjalan, geometri
  bmkg.py, guidance.py       data gempa BMKG, teks peringatan & panduan
  store.py, auth.py          SQLite, akun petugas
  synthetic.py               desa sintetis untuk uji tanpa internet
tools/prepare_area.py        membangun data desa dari baris perintah
frontend/                    HTML/CSS/JS tanpa build step, MapLibre, service worker
tests/                       uji Python, JS, dan E2E
data/                        dibuat otomatis — jangan diunggah ke repo publik
```

## 7. Penggunaan AI & pihak ketiga (AI Use and Third-Party Declaration)

**AI dalam produk:**
- Penentuan zona aman, rute, dan rekomendasi shelter **tidak memakai AI**.
- Opsional: bila `ANTHROPIC_API_KEY` dan `SEMPAT_LLM_MODEL` diisi, model bahasa (Claude, Anthropic) dapat menyusun ulang kalimat panduan cetak dari fakta yang diberikan. Hasilnya diberi label "disusun ulang AI".

**AI dalam pengembangan:** kode, dokumentasi, dan pengujian disusun dengan bantuan asisten AI Claude (Anthropic), lalu ditinjau dan diuji tim. *(Tim: lengkapi bagian mana yang ditulis/diubah sendiri.)*

**Pustaka, data, dan layanan pihak ketiga:**

| Komponen | Lisensi / ketentuan |
|---|---|
| FastAPI, Starlette, Pydantic, Uvicorn | MIT / BSD-3 |
| cryptography | Apache-2.0 / BSD |
| MapLibre GL JS 4.7.1 | BSD-3-Clause |
| qrcode-generator 1.4.4 | MIT |
| OpenStreetMap (jalan, pantai, fasilitas) via Overpass API | © kontributor OpenStreetMap, ODbL 1.0 — atribusi tampil di peta |
| Peta dasar CARTO Voyager (cadangan: tile.openstreetmap.org) | © OpenStreetMap © CARTO; gratis untuk penggunaan non-komersial dengan batas wajar |
| AWS Terrain Tiles (Mapzen/Tilezen; SRTM, GMTED, ETOPO1, dll.) | Data terbuka; atribusi sumber elevasi |
| Open-Meteo Elevation API (Copernicus DEM GLO-90) | CC BY 4.0; © DLR/Airbus, Copernicus |
| Nominatim (nama tempat) | Kebijakan penggunaan OSMF (≤ 1 permintaan/detik, User-Agent jelas) |
| Data gempa BMKG (`autogempa.json`) | Data terbuka BMKG — wajib "Sumber: BMKG" |
| Cloudflare Quick Tunnel (`cloudflared`, opsional) | Apache-2.0; layanan gratis Cloudflare |
| Playwright (khusus pengujian) | Apache-2.0 |

Referensi metode: MLIT Jepang (kecepatan evakuasi), Tobler 1993, FEMA P-646, Mileti & Sorensen 1990, RFC 8291/8292 (Web Push), Nielsen 10 heuristik, Shneiderman 8 aturan emas, WCAG 2.1.
