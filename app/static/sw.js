/* Service worker for Snoqualmie Valley Pool League Scheduler.
 * Bump CACHE_NAME when deploying changes that invalidate cached assets.
 * Strategy:
 *   - HTML navigation: network-first, cache fallback
 *   - Same-origin static assets: cache-first
 *   - CDN resources (Bootstrap, fonts): stale-while-revalidate
 */

const CACHE_NAME = 'svpl-v1';
const PRECACHE = ['/seasons', '/static/css/app.css', '/static/css/print.css'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;

  const url = new URL(e.request.url);
  const ownOrigin = url.origin === self.location.origin;

  if (e.request.mode === 'navigate') {
    // HTML pages — network-first so content stays fresh; fall back to cache when offline
    e.respondWith(
      fetch(e.request)
        .then(r => { caches.open(CACHE_NAME).then(c => c.put(e.request, r.clone())); return r; })
        .catch(() => caches.match(e.request).then(r => r || caches.match('/seasons')))
    );
  } else if (ownOrigin) {
    // Our own static files — cache-first
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request).then(nr => {
        caches.open(CACHE_NAME).then(c => c.put(e.request, nr.clone()));
        return nr;
      }))
    );
  } else {
    // CDN (Bootstrap, fonts) — stale-while-revalidate
    e.respondWith(
      caches.match(e.request).then(cached => {
        const fresh = fetch(e.request).then(r => {
          caches.open(CACHE_NAME).then(c => c.put(e.request, r.clone()));
          return r;
        });
        return cached || fresh;
      })
    );
  }
});
