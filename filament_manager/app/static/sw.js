// Service Worker: macht die App installierbar und lesbar ohne Verbindung.
//  - Oberfläche + Bibliotheken liegen im Cache (App startet auch ohne Netz)
//  - Die Slot-Daten werden bei jeder erfolgreichen Abfrage zwischengespeichert; ohne Verbindung
//    zeigt die App den letzten Stand (nur lesen). Änderungen brauchen immer eine Serververbindung.
// Bei einer neuen App-Version die Zahl bei VERSION erhöhen.
const VERSION = 5;
const SHELL_CACHE = `fm-shell-${VERSION}`;
const DATA_CACHE = "fm-data";
const BASE = self.registration.scope;
const SHELL_FILES = [
  "./", "manifest.webmanifest",
  "icons/icon-192.png", "icons/icon-512.png", "icons/favicon.png", "icons/apple-touch-icon.png",
  "vendor/html5-qrcode.min.js", "vendor/qrcode.min.js", "vendor/jspdf.umd.min.js",
].map((f) => new URL(f, BASE).href);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("fm-shell-") && k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const withTimeout = (promise, ms) =>
  Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))]);

async function networkFirstShell(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const res = await withTimeout(fetch(request), 4000);
    if (res.ok) cache.put(request, res.clone());
    return res;
  } catch (e) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    throw e;
  }
}

async function networkFirstData(request) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const res = await withTimeout(fetch(request), 6000);
    if (res.ok) {
      const headers = new Headers(res.headers);
      headers.set("X-Cached-At", String(Date.now()));
      cache.put(request.url, new Response(await res.clone().blob(), { status: 200, headers }));
    }
    return res;
  } catch (e) {
    const cached = await cache.match(request.url);
    if (!cached) throw e;
    const headers = new Headers(cached.headers);
    headers.set("X-From-Cache", "1");
    return new Response(await cached.blob(), { status: 200, headers });
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const res = await fetch(request);
  if (res.ok) cache.put(request, res.clone());
  return res;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.endsWith("/api/slots")) {
    event.respondWith(networkFirstData(request));
  } else if (url.pathname.includes("/api/")) {
    return; // alles andere geht direkt zum Server
  } else if (request.mode === "navigate" || url.pathname.endsWith("/manifest.webmanifest")) {
    event.respondWith(networkFirstShell(request.mode === "navigate" ? new Request(new URL("./", BASE).href) : request));
  } else {
    event.respondWith(cacheFirst(request));
  }
});
