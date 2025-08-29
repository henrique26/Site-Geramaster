// service-worker.js
console.log('Service Worker registrado!');

self.addEventListener('push', function(event) {
    const data = event.data.json();
    console.log('Notificação push recebida:', data);

    const options = {
        body: data.body,
        // Use seus próprios ícones aqui
        icon: '/static/icons/icon-192.png', // Exemplo usando um dos seus ícones
        badge: '/static/icons/icon-192.png', // Opcional, pode ser o mesmo ou um ícone menor
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