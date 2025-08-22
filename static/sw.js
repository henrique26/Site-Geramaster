// service-worker.js

// Nome do cache
const CACHE_NAME = 'geramaster-cache';

// Lista de arquivos essenciais para acesso offline
const urlsToCache = [
  '/',
  '/menu',
  '/dashboard',
  '/rdv',
  '/pendencias',
  '/controle_veiculos',
  '/pedidos-bling',
  // E também todos os arquivos estáticos (CSS, JS, imagens, etc.) que essas páginas usam
  '/static/style.css',
  '/static/icons/icon-192.png',
  // etc.
];

// Instalação: salva os arquivos no cache
self.addEventListener('install', (event) => {
  console.log('Service Worker: Evento de instalação!');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Cache aberto');
        return cache.addAll(urlsToCache);
      })
  );
});

// Intercepta requisições de rede
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request)
      .then((response) => {
        // Retorna a cópia do cache se ela existir
        if (response) {
          return response;
        }
        // Se não, faz a requisição normal à rede
        return fetch(event.request);
      })
  );
});

// Código de notificações push que você já tinha
self.addEventListener('push', function(event) {
    const data = event.data.json();
    console.log('Notificação push recebida:', data);
    const options = {
        body: data.body,
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        data: { url: data.url }
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