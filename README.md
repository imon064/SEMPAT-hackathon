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

**Opsional — tinggi gedung dari Google Open Buildings 2.5D (Earth Engine):**
```bat
pip install -r requirements-ee.txt
earthengine authenticate
set SEMPAT_EE_PROJECT=id-proyek-google-cloud-anda
python run.py
```
- Proyek Google Cloud harus terdaftar di Earth Engine (gratis untuk penggunaan nonkomersial/riset).
- Setelah itu tombol *Ambil tinggi dari Google Open Buildings* di dasbor perencana aktif. `set SEMPAT_EE_LIVE=1` membuat peta otomatis warga ikut mengambil tinggi gedung (lebih lambat ± 10–60 detik).
- Tanpa akun di server: jalankan `tools/ee_export_heights.js` di https://code.earthengine.google.com, unduh GeoJSON-nya, lalu unggah di dasbor perencana.

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
9. **Tingkat ancaman & gedung tinggi:** di `/petugas`, simulasi dengan tingkat **Siaga** dan waktu tiba **2 menit**. HP yang berada di dataran rendah tidak sempat ke tanah tinggi, sehingga SEMPAT menawarkan **gedung bertingkat terdekat + "naik minimal ke lantai N"** (atau tanah lebih tinggi terdekat). Simulasi **Waspada** hanya meminta warga menjauhi pantai, tanpa rute evakuasi.
10. **Survei gedung:** di `/petugas`, kartu *Gedung evakuasi vertikal* → pilih gedung → isi jumlah lantai, struktur, dan akses. Gedung itu langsung berlabel *diverifikasi petugas* di HP warga.

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

**Gedung tinggi & tanah tinggi (khusus tsunami).** Sumber jumlah lantai, dari yang paling dipercaya. Sumber yang lebih dipercaya tidak pernah ditimpa sumber di bawahnya, dan setiap gedung membawa label sumbernya sampai ke HP warga.

| Sumber | Cara masuk | Label di aplikasi |
|---|---|---|
| Survei petugas (lantai, basement, struktur, akses, atap, SLF) | `/petugas` → *Survei gedung* | diverifikasi petugas |
| OSM `building:levels` | otomatis | jumlah lantai dari OpenStreetMap |
| OSM `height` (÷ 3 m, dibulatkan ke bawah) | otomatis | tinggi dari OpenStreetMap |
| GeoJSON unggahan perencana (properti `height` atau `levels`) | `/perencana` → *Tinggi gedung* | data dari perencana |
| Google Open Buildings 2.5D Temporal (± 4 m, 2016–2023) | Earth Engine (opsional) | perkiraan satelit |
| Tidak ada data | — | **tidak pernah disarankan** |

- Peta otomatis warga kini juga mengunduh gedung bertingkat dari OSM, yaitu yang bertag `building:levels ≥ 2` atau `height`.
- Basement (`building:levels:underground`) dicatat. Warga diingatkan agar tidak berlindung di basement.
- Material OSM (`building:material`) dan survei menyaring bangunan kayu/bambu dan bata tanpa rangka beton.
- **Peta bahaya resmi** (GeoJSON poligon InaRISK/BPBD) bisa diunggah dari dasbor perencana. Tempat aman lalu = di luar poligon dan ≥ 50 m dari batasnya.

## 4. Cara SEMPAT menghitung (dan seberapa akurat)

Penentuan aman/tidak aman bersifat **deterministik** (aturan geografis + perhitungan rute), **bukan AI**. Data yang sama selalu memberi hasil yang sama, dan setiap angka bisa ditelusuri.

**Graf jalan.** Jalan dan jalur pejalan kaki dari OSM menjadi graf berarah. Biaya naik ≠ turun.

**Kecepatan berjalan:**
- 1,0 m/detik (umum) dan 0,5 m/detik (lansia/difabel), sesuai pedoman MLIT Jepang.
- Lereng memakai fungsi Tobler yang dinormalkan: `W(s) = 6·e^(−3,5·|s+0,05|) / W(0)`.
- Tangga 0,21 m/detik vertikal.

**Tempat aman:**
- Di luar zona bahaya dan di atas ambang + 5 m, atau ≥ 3 km dari pantai.
- Margin 5 m menutup galat tinggi DEM global di pesisir datar, supaya "tanah tinggi" tidak ternyata lebih rendah dari perkiraan.
- Gedung shelter vertikal terdaftar juga menjadi tujuan, termasuk waktu naik tangga sampai lantai aman.

**Tsunami — tingkat ancaman (kategori BMKG):**

| Tingkat | Tinggi | Yang dilakukan SEMPAT |
|---|---|---|
| Waspada | < 0,5 m | Layar kuning "jauhi pantai dan sungai", tanpa rute evakuasi |
| Siaga | 0,5–3 m | Evakuasi; lantai aman dihitung untuk 3 m |
| Awas | > 3 m | Evakuasi; lantai aman dihitung untuk skenario rencana (ASUMSI 10 m, bisa diubah perencana) |

Bila peringatan memuat perkiraan tinggi, angka itu yang dipakai. Peringatan BMKG dari data terbuka (*berpotensi tsunami*) diperlakukan seperti Awas.

**Tsunami — gedung tinggi (evakuasi vertikal):**
- Genangan di gedung = tinggi tsunami − ketinggian tanah. Ini model "bak mandi" tanpa peredaman ke darat, jadi cenderung melebih-lebihkan.
- Lantai aman = genangan dibulatkan ke atas + 1, mengikuti pedoman gedung evakuasi tsunami Kantor Kabinet Jepang (2005): genangan 2 m → lantai 3 ke atas, 3 m → lantai 4 ke atas.
- Gedung disarankan hanya bila:
  - jumlah lantainya diketahui;
  - bukan rumah pribadi;
  - bukan kayu/bata tanpa rangka beton;
  - bisa dimasuki;
  - jumlah lantainya ≥ lantai aman.
- Kapasitas = luas × jumlah lantai di atas lantai aman × 0,5 ÷ 0,93 m²/orang (FEMA P-646).
- Shelter terdaftar yang lantainya kurang tinggi untuk tingkat ancaman saat itu otomatis tidak dipakai.

**Tsunami — pilihan darurat bila tidak sempat:**
- HP juga menyimpan tabel kedua (`gedung-*`): waktu tercepat ke zona aman, gedung layak, **atau tanah lebih tinggi** (≥ tinggi tsunami + 5 m).
- Bila waktu ke tanah tinggi melebihi sisa waktu (atau waktu tiba tidak diketahui), SEMPAT menawarkan tujuan itu beserta lantai minimum. Ini juga bekerja offline.

**Ruas berisiko:** di zona bahaya, terowongan/underpass diberi biaya ×10 (tergenang lebih dulu) dan jembatan < 1,5 km dari pantai ×3 (tsunami naik lewat sungai). Rute memilih jalan lain bila ada; bila terpaksa lewat, perkiraan waktunya dibuat lebih pesimistis.

**Rute:**
- Dijkstra multi-sumber terbalik memberi waktu terpendek dan "langkah berikutnya" untuk setiap simpul jalan.
- Tabel ini disimpan di HP, sehingga rute tetap dihitung **tanpa internet**. Waktu di HP sama dengan server: selisih ≤ 1,5 detik pada 152 kasus uji (tanah tinggi + pilihan darurat gedung).
- Posisi GPS ditempelkan ke ruas jalan terdekat (proyeksi tegak lurus).

**Posko:**
- Pencarian k-terdekat ke titik *kosong / agak ramai / ramai*. Titik *penuh/tutup* tidak ditawarkan.
- Selama peringatan aktif, rute posko tidak melewati zona bahaya.

**Perencana:**
- Penggeser *Tinggi tsunami rencana* menentukan gedung mana yang cukup tinggi. Kapasitas dihitung hanya dari lantai di atas lantai aman.
- Rekomendasi dipilih secara *greedy maximum coverage* dengan batas kapasitas.
- Kartu *Data gedung & peta bahaya* menampilkan berapa gedung yang jumlah lantainya belum diketahui. Kartu ini juga tempat mengunggah peta bahaya resmi atau data tinggi gedung.

**Notifikasi:**
- Web Push standar (RFC 8291 enkripsi aes128gcm + RFC 8292 VAPID), diimplementasikan dengan pustaka `cryptography`.
- Langganan menyimpan lokasi terakhir HP, sehingga peringatan hanya dikirim ke HP dalam radiusnya.

**Pengujian:**
- `python -m unittest discover tests` (41 uji):
  - Dijkstra dibandingkan dengan Bellman-Ford brute force.
  - Dekoder PNG untuk kelima jenis filter, dan elevasi Terrarium.
  - Pembangun peta otomatis, termasuk perluasan ke daratan.
  - Enkripsi push, dicek silang dengan implementasi independen.
  - Alur API dan cakupan peringatan per wilayah.
  - Model tsunami:
    - lantai aman (2 m → lantai 3, 3 m → lantai 4);
    - penyaringan gedung (rumah, kayu, tanpa data);
    - sumber jumlah lantai dan prioritasnya;
    - terowongan dihindari;
    - tanah tinggi darurat;
    - unggahan GeoJSON;
    - Earth Engine dengan modul tiruan;
    - survei petugas yang bertahan setelah area dimuat ulang.
- `python tests/make_js_fixtures.py && node tests/js/core.test.mjs`: rute di HP sama dengan server.
- Uji browser ujung-ke-ujung `tests/e2e/e2e.js` (38 cek): GPS bergerak, simulasi pribadi, petugas, Siaga → gedung + lantai, Waspada, survei gedung, perencana, dan offline. Jalankan server dengan `SEMPAT_FAKE_OSM=1` (data uji tanpa internet).

**Batasan yang perlu disampaikan jujur:**
- BMKG tidak menerbitkan perkiraan waktu tiba per desa di data terbuka, jadi SEMPAT memakai nilai bawaan 20 menit.
- Akurasi bergantung pada kelengkapan OSM dan resolusi DEM (± 30–90 m; galat tinggi beberapa meter).
- Model tidak memperhitungkan kemacetan pejalan kaki atau kerusakan jalan yang belum dilaporkan.
- Peta otomatis memakai aturan zona bahaya sementara sampai peta bahaya resmi dimuat.
- Lantai aman memakai model "bak mandi": tidak memperhitungkan run-up, pantulan, arus, maupun puing.
- Kekuatan struktur gedung tidak bisa dibaca dari peta atau satelit. Gedung yang belum disurvei selalu diberi label *belum diverifikasi*.
- Tinggi Google 2.5D adalah perkiraan satelit (± 4 m); gedung kecil dan padat bisa meleset.
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
  tsunami.py                 tingkat BMKG, genangan, lantai aman, kelayakan gedung
  heights.py                 tinggi gedung: GeoJSON, Google Open Buildings 2.5D (Earth Engine), peta bahaya
  area_builder.py            OSM + elevasi -> zona bahaya, tempat aman, bangunan
  speed.py, geo.py           kecepatan berjalan, geometri
  bmkg.py, guidance.py       data gempa BMKG, teks peringatan & panduan
  store.py, auth.py          SQLite, akun petugas
  synthetic.py               desa sintetis untuk uji tanpa internet
tools/prepare_area.py        membangun data desa dari baris perintah
tools/ee_export_heights.js   skrip Earth Engine Code Editor: ekspor tinggi gedung ke GeoJSON
requirements-ee.txt          paket opsional Earth Engine
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
| Google Open Buildings 2.5D Temporal & V3 (tinggi/poligon gedung, opsional) | CC BY 4.0 (V3 juga ODbL) — atribusi "Google Open Buildings" ditambahkan ke sumber area |
| Google Earth Engine + `earthengine-api` (opsional) | API Apache-2.0; layanan Earth Engine gratis untuk nonkomersial/riset sesuai ketentuan Google |
| Playwright (khusus pengujian) | Apache-2.0 |

Referensi metode: MLIT Jepang (kecepatan evakuasi), Kantor Kabinet Jepang 2005 (pedoman gedung evakuasi tsunami: lantai aman), kategori peringatan dini tsunami BMKG (Waspada/Siaga/Awas), Tobler 1993, FEMA P-646, Mileti & Sorensen 1990, RFC 8291/8292 (Web Push), Nielsen 10 heuristik, Shneiderman 8 aturan emas, WCAG 2.1.
