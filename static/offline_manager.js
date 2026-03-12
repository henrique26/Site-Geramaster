// --- /static/offline_manager.js ---
console.log("🚀 Arquivo offline_manager.js carregado com sucesso!");

const DB_NAME = 'GeramasterOffline';
const STORE_NAME = 'fila_requisicoes';

// 1. Abre o Banco de Dados
function openOfflineDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, 1);
        
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true });
            }
        };
        
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

// 2. Salva no Cofre
async function salvarNoCofre(url_destino, dadosFormulario) {
    const db = await openOfflineDB();
    
    return new Promise((resolve, reject) => {
        const transaction = db.transaction([STORE_NAME], 'readwrite');
        const store = transaction.objectStore(STORE_NAME);
        
        const pacote = {
            url: url_destino,
            dados: dadosFormulario,
            data_hora: new Date().toLocaleString()
        };
        
        store.add(pacote);
        
        transaction.oncomplete = () => {
            console.log('[COFRE] Dados salvos com sucesso para envio posterior!');
            resolve();
        };
        transaction.onerror = () => reject(transaction.error);
    });
}

// 3. Sincroniza o Cofre
async function sincronizarCofre() {
    console.log("🔄 Verificando o cofre...");
    
    try {
        const db = await openOfflineDB();
        const transaction = db.transaction([STORE_NAME], 'readonly');
        const store = transaction.objectStore(STORE_NAME);
        const request = store.getAll();

        request.onsuccess = async () => {
            const pacotes = request.result;
            if (pacotes.length === 0) return;

            alert(`📦 Achei ${pacotes.length} registro(s) salvo(s)! Enviando para o servidor...`);

            for (const pacote of pacotes) {
                try {
                    const formData = new FormData();
                    
                    // Lógica blindada para garantir que não vai vazio
                    if (Array.isArray(pacote.dados)) {
                        pacote.dados.forEach(([key, value]) => {
                            // 👇 O FILTRO ANTI-FANTASMA 👇
                            // Se for um arquivo, mas o tamanho for 0 (o cara não colocou foto), pula!
                            if ((value instanceof File || value instanceof Blob) && value.size === 0) {
                                return; 
                            }
                            
                            if (value !== undefined && value !== null) {
                                formData.append(key, value);
                            }
                        });
                    } else {
                        // Formato antigo (RDV e Veículos)
                        for (const key in pacote.dados) {
                            formData.append(key, pacote.dados[key]);
                        }
                    }

                    // Dispara pro Python!
                    const resposta = await fetch(pacote.url, {
                        method: 'POST',
                        body: formData 
                    });

                    if (resposta.ok) {
                        await deletarDoCofre(pacote.id);
                        console.log(`✅ Pacote ${pacote.id} enviado com sucesso!`);
                    } else {
                        alert(`❌ Erro no servidor. Código: ${resposta.status}`);
                    }
                } catch (erro) {
                    console.log(`⚠️ Falha ao enviar pacote ${pacote.id}: ${erro}`);
                    break; // Para o loop se a internet cair no meio
                }
            }
            // Atualiza a tela no final de tudo
            window.location.reload(); 
        };
    } catch (erroDB) {
        console.error("🚨 Erro ao tentar abrir o cofre: " + erroDB);
    }
}

// 4. Deleta do Cofre
function deletarDoCofre(id) {
    return new Promise(async (resolve, reject) => {
        const db = await openOfflineDB();
        const transaction = db.transaction([STORE_NAME], 'readwrite');
        const store = transaction.objectStore(STORE_NAME);
        const request = store.delete(id);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
    });
}

// 5. Escutador Automático (Gatilho)
window.addEventListener('online', () => {
    console.log("📡 A internet voltou! Disparando sincronização...");
    sincronizarCofre();
});