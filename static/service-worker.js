// service-worker.js
const CACHE_NAME = 'geramaster-cache-v1';
// Substitua os URLs abaixo pelos caminhos reais dos arquivos do seu projeto
const urlsToCache = [
  '/',
  '/index.html',
  '/static/style.css',
  '/static/main.js',
  '/static/icons/icon-192.png'
];

console.log('Service Worker registrado!');

// Evento de instalação: pré-caching de recursos estáticos
self.addEventListener('install', function(event) {
  console.log('Service Worker: Evento de instalação!');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function(cache) {
        console.log('Service Worker: Cache aberto, pré-armazenando arquivos...');
        return cache.addAll(urlsToCache);
      })
  );
});

// Evento de ativação: limpa caches antigos
self.addEventListener('activate', function(event) {
  console.log('Service Worker: Ativado!');
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then(function(cacheNames) {
      return Promise.all(
        cacheNames.map(function(cacheName) {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            console.log('Service Worker: Deletando cache antigo:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// Evento fetch: estratégia de cache "Cache First" para recursos
self.addEventListener('fetch', function(event) {
  if (event.request.url.startsWith(self.location.origin)) {
    event.respondWith(
      caches.match(event.request)
        .then(function(response) {
          if (response) {
            console.log('Service Worker: Servindo do cache:', event.request.url);
            return response;
          }
          return fetch(event.request).then(
            function(response) {
              if (!response || response.status !== 200 || response.type !== 'basic') {
                return response;
              }
              var responseToCache = response.clone();
              caches.open(CACHE_NAME)
                .then(function(cache) {
                  cache.put(event.request, responseToCache);
                });
              return response;
            }
          );
        })
    );
  }
});

// Seu código original para notificações push
self.addEventListener('push', function(event) {
    const data = event.data.json();
    console.log('Notificação push recebida:', data);
    const options = {
        body: data.body,
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        data: {
            url: data.url
        }
    };
    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.openWindow(event.notification.data.url || '/')
    );
});