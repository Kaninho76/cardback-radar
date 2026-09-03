/* Cardback Radar — service worker
   Shell: cache first (instant open, works offline).
   Data:  network first, fall back to the last copy we kept. */

const VERSION = "v7";
const SHELL = "cardback-shell-" + VERSION;
const DATA = "cardback-data-" + VERSION;

const SHELL_FILES = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png",
  "./icon-maskable-512.png",
  "./apple-touch-icon.png",
  "./favicon.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL)
      .then((c) => c.addAll(SHELL_FILES))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== SHELL && k !== DATA).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // fonts etc. — let the network handle it

  // the daily content: always try the network, keep a copy for offline
  if (url.pathname.endsWith("/data.json")) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(DATA).then((c) => c.put("./data.json", copy));
          }
          return res;
        })
        .catch(() => caches.open(DATA).then((c) => c.match("./data.json")))
    );
    return;
  }

  // the page itself: always try the network, so a new version arrives at once
  const wantsPage =
    req.mode === "navigate" ||
    url.pathname.endsWith("/") ||
    url.pathname.endsWith("/index.html");

  if (wantsPage) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(SHELL).then((c) => {
              c.put("./index.html", copy.clone());
              c.put("./", copy);
            });
          }
          return res;
        })
        .catch(() =>
          caches.match("./index.html").then((hit) => hit || caches.match("./"))
        )
    );
    return;
  }

  // icons and the manifest: cache first, refresh quietly in the background
  event.respondWith(
    caches.match(req).then((hit) => {
      const net = fetch(req)
        .then((res) => {
          if (res && res.ok && res.type === "basic") {
            const copy = res.clone();
            caches.open(SHELL).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => hit);
      return hit || net;
    })
  );
});
