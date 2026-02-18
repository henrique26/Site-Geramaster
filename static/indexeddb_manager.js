// static/indexeddb_manager.js

const DB_NAME = 'veiculosDB';
const DB_VERSION = 1;
const OBJECT_STORE_NAME = 'registros';

// Função para abrir o banco de dados
function openDatabase() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);

        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(OBJECT_STORE_NAME)) {
                db.createObjectStore(OBJECT_STORE_NAME, { keyPath: 'id' });
            }
        };

        request.onsuccess = (event) => {
            resolve(event.target.result);
        };

        request.onerror = (event) => {
            reject('Erro ao abrir o banco de dados: ' + event.target.error);
        };
    });
}

// Função para salvar os registros no IndexedDB
async function saveRegistrosToIndexedDB(registros) {
    const db = await openDatabase();
    const transaction = db.transaction([OBJECT_STORE_NAME], 'readwrite');
    const store = transaction.objectStore(OBJECT_STORE_NAME);

    // Limpa o armazenamento antes de adicionar os novos dados
    await new Promise(resolve => {
        const request = store.clear();
        request.onsuccess = resolve;
    });

    for (const registro of registros) {
        store.add(registro);
    }

    return new Promise((resolve, reject) => {
        transaction.oncomplete = () => {
            console.log('Registros salvos no IndexedDB.');
            resolve();
        };
        transaction.onerror = () => {
            reject('Erro ao salvar registros no IndexedDB.');
        };
    });
}

// Função para carregar os registros do IndexedDB
async function loadRegistrosFromIndexedDB() {
    const db = await openDatabase();
    const transaction = db.transaction([OBJECT_STORE_NAME], 'readonly');
    const store = transaction.objectStore(OBJECT_STORE_NAME);
    const request = store.getAll();

    return new Promise((resolve, reject) => {
        request.onsuccess = (event) => {
            console.log('Registros carregados do IndexedDB.');
            resolve(event.target.result);
        };
        request.onerror = () => {
            reject('Erro ao carregar registros do IndexedDB.');
        };
    });
}