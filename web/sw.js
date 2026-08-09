/* 앱 셸만 캐시한다. 예약 데이터는 매번 서버에서 받아야 하므로 캐시하지 않는다. */

const CACHE = "travel-inbox-shell-v3";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // API 응답은 캐시하지 않는다. 오래된 예약 정보를 보여주는 것이
  // 연결 실패를 알리는 것보다 위험하다.
  if (url.pathname.startsWith("/api/")) return;

  // 화면(HTML)은 네트워크를 먼저 본다. 캐시를 먼저 보면 새로 배포해도
  // 사용자가 예전 화면을 계속 쓰게 된다. 오프라인일 때만 캐시로 되돌아간다.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put("/index.html", copy));
          return res;
        })
        .catch(() => caches.match("/index.html"))
    );
    return;
  }

  // 아이콘·매니페스트 같은 정적 자산은 캐시를 먼저 쓰고 뒤에서 갱신한다.
  event.respondWith(
    caches.match(request).then(hit => {
      const network = fetch(request)
        .then(res => {
          if (res.ok && res.type === "basic") {
            const copy = res.clone();
            caches.open(CACHE).then(c => c.put(request, copy));
          }
          return res;
        })
        .catch(() => hit);
      return hit || network;
    })
  );
});
