// ARQUIVO: static/service-worker.js

const CACHE_NAME = 'geramaster-cache-v6'; // V6 para forçar atualização imediata
const DATA_CACHE_NAME = 'geramaster-data-v6';

// 1. ARQUIVOS ESSENCIAIS (SHELL)
const STATIC_FILES = [
    '/',              
    '/login',
    '/menu',          // Garante que o menu exista
    '/static/js/offline-manager.js',
    '/static/Geramaster logo Preto fundo transparente.png',
    // Links externos (CDNs) DEVEM estar aqui para serem baixados na instalação
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

// 2. INSTALAÇÃO
self.addEventListener('install', (evt) => {
    console.log('[ServiceWorker] Instalando V6 e baixando recursos...');
    evt.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            // Tenta baixar tudo. Se um falhar, não quebra a instalação inteira,
            // mas avisa no console.
            return Promise.all(
                STATIC_FILES.map(url => {
                    return cache.add(url).catch(err => console.error('Falha ao cachear arquivo:', url, err));
                })
            );
        })
    );
    self.skipWaiting();
});

// 3. ATIVAÇÃO (LIMPEZA)
self.addEventListener('activate', (evt) => {
    console.log('[ServiceWorker] Ativando V6 e limpando cache antigo...');
    evt.waitUntil(
        caches.keys().then((keyList) => {
            return Promise.all(keyList.map((key) => {
                if (key !== CACHE_NAME && key !== DATA_CACHE_NAME) {
                    return caches.delete(key);
                }
            }));
        })
    );
    self.clients.claim();
});

// 4. FETCH INTELIGENTE (CORREÇÃO DOS ÍCONES E NAVEGAÇÃO)
self.addEventListener('fetch', (evt) => {
    
    // Ignora requisições que não sejam GET
    if (evt.request.method !== 'GET') return;

    evt.respondWith(
        fetch(evt.request)
            .then((response) => {
                // AQUI ESTAVA O PROBLEMA ANTES:
                // Precisamos aceitar response.type === 'cors' (para CDNs funcionarem)
                // E aceitar response.type === 'basic' (para nossos arquivos)
                
                // Se a resposta for válida (200 OK)
                if (response && response.status === 200) {
                    const responseToCache = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(evt.request, responseToCache);
                    });
                }
                return response;
            })
            .catch(async () => {
                // --- MODO OFFLINE OU FALHA DE REDE ---
                console.log('[ServiceWorker] Offline detectado. Buscando cache para:', evt.request.url);

                // 1. Tenta a página exata
                const cachedResponse = await caches.match(evt.request, { ignoreSearch: true });
                if (cachedResponse) {
                    return cachedResponse;
                }

                // 2. Fallback de Navegação (Se o usuário tentar abrir uma página que não tem)
                if (evt.request.mode === 'navigate') {
                    // Tenta entregar o Menu ou Painel que COM CERTEZA salvamos no install
                    return caches.match('/menu') 
                        || caches.match('/os/painel') 
                        || caches.match('/login')
                        || caches.match('/');
                }
            })
    );
});