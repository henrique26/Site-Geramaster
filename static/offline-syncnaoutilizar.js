// A sua lógica de inicialização do IndexedDB (openDatabase)
const dbPromise = idb.openDB('controle-veiculos-db', 1, {
    upgrade(db) {
        if (!db.objectStoreNames.contains('registros')) {
            const registrosStore = db.createObjectStore('registros', {
                keyPath: 'id',
                autoIncrement: true
            });
            registrosStore.createIndex('placa', 'placa', { unique: false });
            registrosStore.createIndex('sincronizado', 'sincronizado', { unique: false });
        }
    },
});

async function openDatabase() {
    return dbPromise;
}

// Salva um registro no IndexedDB com o flag de 'sincronizado' = false
async function saveVeiculo(veiculo) {
    const db = await openDatabase();
    const tx = db.transaction('registros', 'readwrite');
    const store = tx.objectStore('registros');
    // Adiciona o flag de sincronizacao. Ele só é true quando vem do servidor.
    veiculo.sincronizado = false; 
    await store.put(veiculo);
    return tx.done;
}

// Retorna todos os registros do IndexedDB
async function getAllVeiculos() {
    const db = await openDatabase();
    return db.getAll('registros');
}

// Sincroniza os dados do servidor com o IndexedDB (Online)
async function syncAndSaveData(serverRecords) {
    const db = await openDatabase();
    const tx = db.transaction('registros', 'readwrite');
    const store = tx.objectStore('registros');

    // Limpa a base local para garantir que a do servidor é a mais atualizada
    await store.clear();

    // Adiciona os novos dados do servidor (já sincronizados)
    for (const record of serverRecords) {
        // Marca os registros que vem do servidor como já sincronizados
        record.sincronizado = true; 
        await store.put(record);
    }
    await tx.done;
}

// Função de Sincronização offline -> online
async function syncOfflineRecords() {
    const db = await openDatabase();
    const pendingRecords = await db.getAllFromIndex('registros', 'sincronizado', false);
    
    if (pendingRecords.length === 0) {
        console.log("Nenhum registro offline para sincronizar.");
        return;
    }

    console.log(`Sincronizando ${pendingRecords.length} registro(s) offline...`);
    
    for (const record of pendingRecords) {
        try {
            const response = await fetch('/api/veiculos', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ acao: 'salvar_registro', ...record })
            });

            const result = await response.json();
            
            if (result.success) {
                // Se a API salvou, atualiza o status do registro local para 'sincronizado'
                const tx = db.transaction('registros', 'readwrite');
                const store = tx.objectStore('registros');
                record.sincronizado = true;
                await store.put(record);
                await tx.done;
                console.log(`Registro ${record.id} sincronizado com sucesso.`);
            } else {
                console.error(`Erro ao sincronizar registro ${record.id}: ${result.message}`);
            }
        } catch (error) {
            console.error(`Erro de conexão ao sincronizar: ${error}`);
            // Sai do loop se a conexão cair novamente
            break;
        }
    }
}

// Exporta as funções para serem usadas em outros scripts
export { openDatabase, saveVeiculo, getAllVeiculos, syncAndSaveData, syncOfflineRecords };