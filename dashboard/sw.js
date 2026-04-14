const CACHE_NAME = 'sabujak-v1';
const STATIC_ASSETS = [
  '/',
  '/app.css?v=7',
  '/app.js?v=7',
  '/manifest.json',
];
const API_CACHE = 'sabujak-api-v1';

// Install: cache static assets
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Activate: clean old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME && k !== API_CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch: network-first for API, cache-first for static
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Skip SSE (streaming) and POST requests
  if (url.pathname === '/api/sse' || e.request.method !== 'GET') {
    return;
  }

  // API requests: network-first, cache fallback
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          // Cache successful GET API responses
          if (res.ok) {
            const clone = res.clone();
            caches.open(API_CACHE).then(cache => cache.put(e.request, clone));
          }
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Static assets: cache-first, network fallback
  e.respondWith(
    caches.match(e.request)
      .then(cached => cached || fetch(e.request).then(res => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
        }
        return res;
      }))
      .catch(() => caches.match('/'))
  );
});
