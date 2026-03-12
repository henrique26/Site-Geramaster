// service-worker.js
const CACHE_NAME = 'geramaster-v3'; // Mudamos a versão para forçar a atualização

// Arquivos base que SEMPRE devem estar no celular
const coreAssets = [
    '/',
    '/static/style.css',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    // Coloque aqui o caminho do seu JS principal, do Bootstrap, etc.
];

// 1. INSTALAÇÃO (Baixa o esqueleto)
self.addEventListener('install', (event) => {
    console.log('[SW] Instalando novo Service Worker...');
    self.skipWaiting(); // Força a instalação imediata
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(coreAssets);
        })
    );
});

// 2. ATIVAÇÃO (Limpa o lixo velho)
self.addEventListener('activate', (event) => {
    console.log('[SW] Ativado e limpando caches antigos...');
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cache) => {
                    if (cache !== CACHE_NAME) {
                        console.log('[SW] Apagando cache antigo:', cache);
                        return caches.delete(cache);
                    }
                })
            );
        })
    );
    return self.clients.claim(); // Assume o controle da tela na hora
});

// 3. O INTERCEPTADOR INTELIGENTE (Network First)
self.addEventListener('fetch', (event) => {
    // Só intercepta requisições do tipo GET (ignora POSTs e formulários por enquanto)
    if (event.request.method !== 'GET') return;

    // Ignora rotas de API por enquanto (não queremos cachear JSON de retorno ainda)
    if (event.request.url.includes('/api/')) return;

    event.respondWith(
        fetch(event.request)
            .then((networkResponse) => {
                // Se tem internet, clona a resposta e salva no cache (Cache Dinâmico!)
                // Assim, se ele abriu a OS #1042 hoje, ela vai funcionar offline amanhã.
                const responseToCache = networkResponse.clone();
                caches.open(CACHE_NAME).then((cache) => {
                    cache.put(event.request, responseToCache);
                });
                return networkResponse;
            })
            .catch(() => {
                // SE A INTERNET CAIR (Ou o servidor der erro)
                console.log('[SW] Modo Offline Ativado. Buscando no Cache:', event.request.url);
                return caches.match(event.request).then((cachedResponse) => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    // Opcional: Se ele tentar abrir uma página que não tem no cache, 
                    // você pode retornar uma página customizada de "Você está offline".
                    // return caches.match('/offline.html');
                });
            })
    );
});

// ==========================================
// MANTIVE O SEU CÓDIGO DE NOTIFICAÇÕES INTACTO
// ==========================================
self.addEventListener('push', function(event) {
    const data = event.data.json();
    const options = {
        body: data.body,
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        data: { url: data.url }
    };
    event.waitUntil(self.registration.showNotification(data.title, options));
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(clients.openWindow(event.notification.data.url || '/'));
});