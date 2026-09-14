// Persona Studio service worker — minimal: enables PWA installability and
// keeps the shell loadable when the dev server hiccups. API/data requests are
// always fetched from the network (never cached) so data is never stale.
const SHELL_CACHE = "persona-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(["/manifest.webmanifest", "/icon-192.png", "/icon-512.png"]))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Never cache API responses or studio media — always live data.
  if (url.pathname.startsWith("/api/") || event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request).then((hit) => hit || caches.match("/")))
  );
});