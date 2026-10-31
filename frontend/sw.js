// Service worker SEMPAT: aplikasi tetap terbuka & rute tetap jalan saat internet putus,
// dan menampilkan notifikasi peringatan (Web Push) walau aplikasi tertutup.
const VERSION = 'sempat-v2';
const TILES = 'sempat-tiles-v1';
const MAX_TILES = 1500;
const SHELL = [
  '/', '/petugas', '/perencana', '/manifest.webmanifest',
  '/static/css/app.css', '/static/js/common.js', '/static/js/core.js', '/static/js/warga.js',
  '/static/js/petugas.js', '/static/js/perencana.js',
  '/static/vendor/maplibre-gl.js', '/static/vendor/maplibre-gl.css', '/static/vendor/qrcode.js',
  '/static/icons/icon-192.png', '/static/icons/icon-512.png',
];
// Data yang selalu diambil terbaru dari server, dengan salinan terakhir sebagai cadangan offline.
const DATA = ['/api/area', '/api/tables', '/api/state', '/api/meta', '/api/quake'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION && k !== TILES).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function networkFirst(req) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (!hit) throw err;
    const h = new Headers(hit.headers);
    h.set('X-Sempat-Offline', '1');
    return new Response(await hit.blob(), { status: hit.status, statusText: hit.statusText, headers: h });
  }
}

// Ubin peta dasar: pakai salinan bila ada (hemat kuota & bisa offline), simpan yang baru.
async function tileCache(req) {
  const cache = await caches.open(TILES);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) {
    cache.put(req, res.clone());
    cache.keys().then((ks) => { if (ks.length > MAX_TILES) ks.slice(0, ks.length - MAX_TILES).forEach((k) => cache.delete(k)); });
  }
  return res;
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (/basemaps\.cartocdn\.com$|tile\.openstreetmap\.org$/.test(url.hostname)) { e.respondWith(tileCache(req)); return; }
  if (url.origin !== location.origin) return;
  if (url.pathname === '/api/stream') return;
  if (DATA.includes(url.pathname) || SHELL.includes(url.pathname) || url.pathname.startsWith('/static/')) {
    e.respondWith(networkFirst(req));
  }
});

// ---- Web Push ----
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: 'SEMPAT', body: e.data ? e.data.text() : '' }; }
  const urgent = d.type === 'alert' || d.type === 'sim';
  e.waitUntil((async () => {
    const list = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    list.forEach((c) => c.postMessage({ type: 'push', data: d }));
    await self.registration.showNotification(d.title || 'SEMPAT', {
      body: d.body || '', tag: d.tag || 'sempat', renotify: true, requireInteraction: urgent,
      vibrate: urgent ? [800, 300, 800, 300, 800, 300, 800] : [200],
      icon: '/static/icons/icon-192.png', badge: '/static/icons/icon-192.png',
      data: { url: d.url || '/', payload: d },
    });
  })());
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const data = e.notification.data || {};
  const url = data.url || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) {
      if (new URL(c.url).pathname === '/' && 'focus' in c) {
        c.postMessage({ type: 'push', data: data.payload || {} });
        return c.focus();
      }
    }
    return self.clients.openWindow(url);
  }));
});
