// static/js/offline-manager.js

const DB_NAME = 'GeramasterOfflineDB';
const STORE_NAME = 'fila_envio_os';
const DB_VERSION = 1;

// 1. Abre (ou cria) o Banco de Dados
function openDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, DB_VERSION);

        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true });
            }
        };

        request.onsuccess = (event) => resolve(event.target.result);
        request.onerror = (event) => reject(event.target.error);
    });
}

// 2. Salva a OS na fila (incluindo Fotos!)
async function salvarOSOffline(osId, formData) {
    const db = await openDB();
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);

    // O FormData não é salvável diretamente, precisamos converter para um objeto simples
    const dados = {
        osId: osId,
        timestamp: Date.now(),
        campos: {},
        arquivos: {} // Vamos salvar os Blobs das imagens aqui
    };

    // Extrai dados do FormData
    for (const [key, value] of formData.entries()) {
        if (value instanceof File) {
            // Se for arquivo (foto), salva apenas se tiver conteúdo
            if (value.name && value.size > 0) {
                // Se for múltiplo, cria array
                if (!dados.arquivos[key]) dados.arquivos[key] = [];
                dados.arquivos[key].push(value);
            }
        } else {
            dados.campos[key] = value;
        }
    }

    return new Promise((resolve, reject) => {
        const request = store.add(dados);
        request.onsuccess = () => resolve(true);
        request.onerror = () => reject(request.error);
    });
}

// 3. Tenta enviar itens pendentes (Sincronização)
async function sincronizarPendentes() {
    if (!navigator.onLine) return; // Se tá offline, nem tenta

    const db = await openDB();
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const request = store.getAll();

    request.onsuccess = async () => {
        const itens = request.result;
        if (itens.length === 0) return;

        console.log(`📡 Sincronizando ${itens.length} OS pendentes...`);

        for (const item of itens) {
            try {
                // Reconstrói o FormData
                const formData = new FormData();
                
                // Adiciona campos texto
                for (const [k, v] of Object.entries(item.campos)) {
                    formData.append(k, v);
                }
                
                // Adiciona arquivos
                for (const [k, arquivos] of Object.entries(item.arquivos)) {
                    arquivos.forEach(blob => formData.append(k, blob));
                }

                // Envia para o Python
                const response = await fetch(`/os/finalizar/${item.osId}`, {
                    method: 'POST',
                    body: formData
                });

                if (response.redirected || response.ok) {
                    // Se deu certo, remove da fila
                    const deleteTx = db.transaction(STORE_NAME, 'readwrite');
                    deleteTx.objectStore(STORE_NAME).delete(item.id);
                    console.log(`✅ OS #${item.osId} sincronizada com sucesso!`);
                }
            } catch (erro) {
                console.error(`Erro ao sincronizar OS #${item.osId}:`, erro);
            }
        }
    };
}

async function verificarOSPendente(osId) {
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const request = store.getAll();

        return new Promise((resolve) => {
            request.onsuccess = () => {
                const itens = request.result;
                // Procura se existe algum item com o osId igual ao atual
                const pendente = itens.find(item => item.osId == osId);
                resolve(!!pendente); // Retorna true ou false
            };
        });
    } catch (e) {
        console.error("Erro ao verificar pendentes:", e);
        return false;
    }
}

// 5. Conta quantos itens tem na fila (para mostrar badge no menu)
async function contarPendentes() {
    try {
        const db = await openDB();
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const request = store.count();
        
        return new Promise((resolve) => {
            request.onsuccess = () => resolve(request.result);
        });
    } catch { return 0; }
}