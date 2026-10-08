/* Trenes a tiempo — service worker: push + click + shell básico. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(clients.claim()));

/* fetch passthrough: requerido para instalabilidad; no cacheamos datos RT. */
self.addEventListener('fetch', () => {});

self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {}; }
  e.waitUntil(Promise.all([
    self.registration.showNotification(d.title || 'Trenes a tiempo', {
      body: d.body || '',
      data: { url: d.url || '/' },
      tag: d.tag || 'trenes',
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      renotify: true,
    }),
    // repostear a las páginas abiertas (observable; también sirve para QA e2e)
    clients.matchAll({ type: 'window' }).then((cs) =>
      cs.forEach((c) => c.postMessage({ type: 'push', payload: d }))),
  ]));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = e.notification?.data?.url || '/';
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then((cs) => {
      const c = cs.find((w) => w.url.includes(self.location.origin));
      if (c) { c.focus(); c.navigate(url); return; }
      return clients.openWindow(url);
    }));
});
