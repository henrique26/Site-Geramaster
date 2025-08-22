// Seu main.js ou register-sw.js

if ('serviceWorker' in navigator) {
  window.addEventListener('load', function() {
    // Referencia apenas o arquivo que criamos
    navigator.serviceWorker.register('/sw.js').then(function(registration) {
      console.log('ServiceWorker registration successful with scope: ', registration.scope);
    }, function(err) {
      console.log('ServiceWorker registration failed: ', err);
    });
  });
}