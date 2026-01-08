self.addEventListener('install', (e) => {
    console.log('[Service Worker] Install');
  });
  self.addEventListener('fetch', (e) => {
    // Basic pass-through (No heavy caching to avoid update issues)
    e.respondWith(fetch(e.request));
  });