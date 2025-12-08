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

// Dentro do seu arquivo sw.js existente

self.addEventListener('sync', (event) => {
    if (event.tag === 'sync-os-pendentes') {
        event.waitUntil(sincronizarNoBackground());
    }
});

// Função auxiliar dentro do SW para chamar a lógica de sync
async function sincronizarNoBackground() {
    // Como o SW não acessa o DOM, precisamos importar o script ou replicar a lógica.
    // A maneira mais fácil aqui é enviar uma mensagem para todas as abas abertas
    // ou usar a mesma lógica do IndexedDB aqui dentro.
    
    // (Simplificação: O navegador vai tentar rodar o sync quando voltar a internet. 
    // Vamos garantir que a página principal faça isso ao carregar também).
    console.log('[SW] Background Sync disparado! A internet voltou.');
}