// SEMPAT — ekspor tinggi gedung dari Google Open Buildings 2.5D Temporal (Google Earth Engine Code Editor).
//
// Untuk perencana yang tidak memasang Earth Engine di server SEMPAT:
//   1. Buka https://code.earthengine.google.com (masuk dengan akun Google yang terdaftar Earth Engine).
//   2. Tempel seluruh skrip ini. Ganti BBOX dengan batas area dari SEMPAT Perencana
//      (kartu "Data gedung & peta bahaya" menampilkan angkanya).
//   3. Klik Run. Di tab Tasks klik RUN pada "sempat_tinggi_gedung". Berkas GeoJSON masuk ke Google Drive.
//   4. Unduh berkas itu, lalu unggah di SEMPAT Perencana → "Tinggi gedung (GeoJSON)".
//
// Data: Google Open Buildings 2.5D Temporal (2016–2023), tinggi gedung relatif terhadap tanah,
// resolusi efektif ± 4 m; poligon Open Buildings V3 (kepercayaan ≥ 0,75). Lisensi CC BY 4.0 —
// cantumkan "Google Open Buildings". Nilai tinggi adalah PERKIRAAN satelit: SEMPAT menandainya
// "belum diverifikasi" sampai petugas mensurvei gedungnya.

var BBOX = [100.33, -0.97, 100.39, -0.92]; // [barat, selatan, timur, utara] — GANTI
var YEAR = 2023;                            // 2016–2023
var MIN_HEIGHT_M = 6;                       // hanya gedung ± 2 lantai ke atas yang relevan untuk evakuasi vertikal

var region = ee.Geometry.Rectangle(BBOX);
var img = ee.ImageCollection('GOOGLE/Research/open-buildings-temporal/v1')
  .filterBounds(region)
  .filterDate(YEAR + '-01-01', (YEAR + 1) + '-01-01')
  .mosaic();
var height = img.select('building_height').updateMask(img.select('building_presence').gt(0.5));

var polys = ee.FeatureCollection('GOOGLE/Research/open-buildings/v3/polygons')
  .filterBounds(region)
  .filter(ee.Filter.gte('confidence', 0.75))
  .filter(ee.Filter.gte('area_in_meters', 40));

var withHeight = height.reduceRegions({ collection: polys, reducer: ee.Reducer.mean(), scale: 4 })
  .filter(ee.Filter.gte('mean', MIN_HEIGHT_M))
  .map(function (f) { return f.select(['mean'], ['height']); });

Map.centerObject(region, 15);
Map.addLayer(height, { min: 0, max: 30, palette: ['ffffff', 'fdae61', 'd7191c'] }, 'Tinggi gedung 2.5D ' + YEAR);
Map.addLayer(withHeight, { color: '6d28d9' }, 'Gedung >= ' + MIN_HEIGHT_M + ' m');
print('Jumlah gedung >= ' + MIN_HEIGHT_M + ' m:', withHeight.size());

Export.table.toDrive({
  collection: withHeight,
  description: 'sempat_tinggi_gedung',
  fileFormat: 'GeoJSON'
});
