// RINDA offline app shell.
//
// * The console's own files are cached so it opens with no signal.
// * A few read requests a field officer needs offline (bootstrap, account,
//   assigned tasks, registered habitats, forecast stamp) are answered from the
//   network when possible and from the last saved copy otherwise.
// * Writes are never cached or replayed here: observations recorded offline go
//   through the idempotent queue in js/offline.js.
// * Signing out deletes the saved API answers (see app.js).
const SHELL = 'rinda-shell-v1';
const API = 'rinda-api-v1';
const VIEWS = ['admin', 'assurance', 'bulletins', 'evidence', 'field', 'flight', 'forecast', 'governance', 'help', 'impact',
  'missions', 'network', 'overview', 'programme', 'replay', 'scenario'];
const FILES = ['/', '/css/app.css', '/js/app.js', '/js/ui.js', '/js/site.js', '/js/offline.js', '/js/i18n.js',
  '/vendor/echarts.min.js', '/vendor/leaflet/leaflet.js', '/vendor/leaflet/leaflet.css',
  '/vendor/leaflet/images/layers.png', '/vendor/leaflet/images/layers-2x.png', '/vendor/leaflet/images/marker-icon.png',
  '/vendor/leaflet/images/marker-icon-2x.png', '/vendor/leaflet/images/marker-shadow.png', '/manifest.webmanifest',
  ...VIEWS.map((v) => `/js/views/${v}.js`)];
// read-only API answers worth keeping for offline field use (path prefixes, after /api/v1)
const KEEP = ['/api/v1/bootstrap', '/api/v1/auth/me', '/api/v1/tasks', '/api/v1/habitats', '/api/v1/forecast/meta',
  '/api/v1/field/summary', '/api/v1/alerts', '/api/v1/observations'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => ![SHELL, API].includes(k)).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function networkFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const r = await fetch(req);
    if (r.ok) cache.put(req, r.clone());
    return r;
  } catch (err) {
    const hit = await cache.match(req, { ignoreVary: true });
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;  // writes go straight to the network
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // map tiles and fonts: browser default
  if (url.pathname.startsWith('/api/')) {
    if (KEEP.some((p) => url.pathname.startsWith(p))) e.respondWith(networkFirst(req, API));
    return;  // everything else in the API: network only
  }
  // console files: fresh when online (they change with each release), saved copy when not
  e.respondWith(networkFirst(req, SHELL).catch(() => caches.match('/')));
});
