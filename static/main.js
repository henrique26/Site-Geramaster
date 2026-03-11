// AQUI ESTÁ A LÓGICA DO IDB
const dbPromise = idb.openDB('GeramasterDB', 1, {
    upgrade(db) {
        console.log('IndexedDB: Criando ou atualizando a base de dados...');
        if (!db.objectStoreNames.contains('controle_veiculos')) {
            console.log('IndexedDB: Criando a tabela controle_veiculos...');
            db.createObjectStore('controle_veiculos', { keyPath: 'id' });
        }
    },
});

async function saveVeiculo(veiculo) {
    const db = await dbPromise;
    const tx = db.transaction('controle_veiculos', 'readwrite');
    const store = tx.objectStore('controle_veiculos');
    await store.put(veiculo);
    return tx.done;
}

async function getAllVeiculos() {
    const db = await dbPromise;
    return db.getAll('controle_veiculos');
}

async function syncAndSaveData(registros) {
    if (!Array.isArray(registros)) {
        console.error("Dados do servidor não são um array.", registros);
        return;
    }
    console.log('IndexedDB: Iniciando sincronização e salvamento de dados do servidor.');
    
    const db = await dbPromise;
    const tx = db.transaction('controle_veiculos', 'readwrite');
    const store = tx.objectStore('controle_veiculos');
    
    try {
        // Limpar a tabela antes de salvar os dados do servidor para evitar duplicatas
        console.log('IndexedDB: Limpando a tabela...');
        await store.clear();

        for (const registro of registros) {
            // Garantir que cada registro tenha uma chave primária 'id'
            if (registro.id === undefined) {
                // Adiciona um ID temporário se não houver (para registros offline)
                registro.id = Math.random().toString(36).substring(2, 15);
            }
            console.log(`IndexedDB: Salvando registro com ID ${registro.id}...`);
            await store.put(registro);
        }
        await tx.done;
        console.log('IndexedDB: Sincronização concluída com sucesso.');
    } catch (e) {
        console.error('IndexedDB: Falha na transação de sincronização.', e);
    }
}

// AQUI COMEÇA A LÓGICA DA PÁGINA
document.addEventListener('DOMContentLoaded', function() {
    const tipoRegistroSelect = document.getElementById('tipo_registro');
    const litrosInput = document.getElementById('litros');
    const valorInput = document.getElementById('valor');

    function toggleRequiredFields() {
        if (tipoRegistroSelect && litrosInput && valorInput) {
            if (tipoRegistroSelect.value === 'abastecimento') {
                litrosInput.setAttribute('required', 'required');
                valorInput.setAttribute('required', 'required');
            } else {
                litrosInput.removeAttribute('required');
                valorInput.removeAttribute('required');
            }
        }
    }
    if (tipoRegistroSelect) {
        toggleRequiredFields();
        tipoRegistroSelect.addEventListener('change', toggleRequiredFields);
    }
    const kmInput = document.getElementById('km');
    if (kmInput) {
        kmInput.addEventListener('input', function(e) {
            let value = e.target.value.replace(/\D/g, '');
            if (value) {
                let numValue = parseInt(value, 10);
                if (!isNaN(numValue)) {
                    e.target.value = numValue.toLocaleString('pt-BR').replace(/,/g, '.');
                } else {
                    e.target.value = '';
                }
            } else {
                e.target.value = '';
            }
        });
    }
    
    const menuToggle = document.querySelector('.menu-toggle');
    if (menuToggle) {
        menuToggle.addEventListener('click', () => {
            document.body.classList.toggle('sidebar-open');
        });
    }

    function renderizarRegistros(registros) {
        const tabelaCorpo = document.querySelector('#tabela-registros tbody');
        if (!tabelaCorpo) {
            console.error('Elemento #tabela-registros tbody não encontrado.');
            return;
        }
        tabelaCorpo.innerHTML = '';
        if (registros.length === 0) {
            tabelaCorpo.innerHTML = '<tr><td colspan="15">Nenhum registro encontrado.</td></tr>';
            return;
        }
        registros.forEach(reg => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${reg.placa}</td>
                <td>${reg.tecnico_nome || '-'}</td>
                <td>${reg.tipo_registro}</td>
                <td>${reg.data}</td>
                <td>${(reg.km || 0).toLocaleString('pt-BR')}</td>
                <td>${(reg.litros || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                <td>R$ ${(reg.valor || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                <td>${(reg.valor_por_litro_calc || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                <td>${(reg.consumo_km_litro_calc || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                <td>${(reg.custo_por_km_individual_calc || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                <td>${reg.proxima_troca_km || '-'}</td>
                <td>${reg.proxima_troca_data || '-'}</td>
                <td>${reg.observacoes || '-'}</td>
                <td>
                    <form method="POST" class="actions-form" onsubmit="return confirm('Tem certeza que deseja excluir este registro?');">
                        <input type="hidden" name="acao" value="excluir_registro" />
                        <input type="hidden" name="id_registro" value="${reg.id}" />
                        <button type="submit" class="excluir"></button>
                    </form>
                </td>
            `;
            tabelaCorpo.appendChild(row);
        });
    }

    const formVeiculo = document.getElementById('registroVeiculoForm');
    if (formVeiculo) {
        formVeiculo.addEventListener('submit', function(event) {
            event.preventDefault();
            const veiculo = {
                placa: document.getElementById('placa').value,
                tipo_registro: document.getElementById('tipo_registro').value,
                data: document.getElementById('data').value,
                km: document.getElementById('km').value.replace(/\./g, ''),
                litros: parseFloat(document.getElementById('litros').value) || 0,
                valor: parseFloat(document.getElementById('valor').value) || 0,
                proxima_troca_km: document.getElementById('proxima_troca_km') ? document.getElementById('proxima_troca_km').value : null,
                proxima_troca_data: document.getElementById('proxima_troca_data') ? document.getElementById('proxima_troca_data').value : null,
                observacoes: document.getElementById('observacoes').value,
                usuario_id: "{{ session.get('user_id') }}",
                sincronizado: 0,
                id: null // O IndexedDB vai gerar um ID para este novo registro
            };
            saveVeiculo(veiculo)
                .then(() => {
                    alert('Registro salvo localmente! Ele será sincronizado quando houver conexão.');
                    formVeiculo.reset();
                    getAllVeiculos().then(offlineData => renderizarRegistros(offlineData));
                })
                .catch(error => {
                    console.error('Falha ao salvar no IndexedDB:', error);
                    alert('Erro ao salvar o registro localmente. Tente novamente.');
                });
        });
    }

    // Inicia a aplicação com o banco de dados
    const dadosScript = document.getElementById('dados-do-servidor');
    if (dadosScript && dadosScript.textContent.trim().length > 0) {
        try {
            const dados = JSON.parse(dadosScript.textContent);
            if (Array.isArray(dados)) {
                console.log('Dados recebidos do servidor são um array. Salvando...');
                renderizarRegistros(dados);
                syncAndSaveData(dados);
            } else if (dados && dados.registros && Array.isArray(dados.registros)) {
                console.log('Dados recebidos do servidor são um objeto com a chave "registros". Salvando...');
                renderizarRegistros(dados.registros);
                syncAndSaveData(dados.registros);
            } else {
                console.warn("Dados do servidor não estão no formato esperado. Tentando carregar dados offline...");
                getAllVeiculos().then(offlineData => {
                    renderizarRegistros(offlineData);
                    console.log("Dados carregados do IndexedDB como fallback.");
                });
            }
        } catch (e) {
            console.error("Erro ao ler dados do servidor:", e);
            getAllVeiculos().then(offlineData => {
                renderizarRegistros(offlineData);
                console.log("Dados carregados do IndexedDB como fallback.");
            });
        }
    } else {
        console.log("Nenhum dado do servidor encontrado. Carregando dados offline...");
        getAllVeiculos().then(offlineData => {
            renderizarRegistros(offlineData);
            if (offlineData.length > 0) {
                console.log("Dados carregados do IndexedDB.");
            } else {
                const tabelaCorpo = document.querySelector('#tabela-registros tbody');
                if (tabelaCorpo) {
                    tabelaCorpo.innerHTML = '<tr><td colspan="15">Nenhum registro encontrado.</td></tr>';
                }
                console.log("IndexedDB vazio. Nenhum registro para exibir.");
            }
        }).catch(err => {
            console.error('Erro ao carregar dados offline:', err);
            const tabelaCorpo = document.querySelector('#tabela-registros tbody');
            if (tabelaCorpo) {
                tabelaCorpo.innerHTML = '<tr><td colspan="15">Não foi possível carregar os registros. Verifique a conexão.</td></tr>';
            }
        });
    }
});
document.addEventListener('DOMContentLoaded', function() {
    // ...
    const dadosScript = document.getElementById('dados-do-servidor');

    // Verifique se a tag de script existe e tem conteúdo
    if (dadosScript && dadosScript.textContent.trim().length > 0) {
        try {
            // Analise o JSON
            const dadosDoServidor = JSON.parse(dadosScript.textContent);

            // Verifique se a chave 'registros' existe e é um array
            if (dadosDoServidor.registros && Array.isArray(dadosDoServidor.registros)) {
                console.log("Dados do servidor encontrados e processados com sucesso.");
                
                // Chame a função para renderizar os registros
                renderizarRegistros(dadosDoServidor.registros);
                
                // Salve no IndexedDB para uso offline
                syncAndSaveData(dadosDoServidor.registros);
            } else {
                // Caso a chave 'registros' não seja encontrada ou não seja um array
                console.warn("Dados do servidor não estão no formato esperado. Tentando carregar dados offline...");
                getAllVeiculos().then(offlineData => {
                    renderizarRegistros(offlineData);
                    console.log("Dados carregados do IndexedDB como fallback.");
                });
            }
        } catch (e) {
            // Se houver um erro de parsing do JSON
            console.error("Erro ao ler dados do servidor:", e);
            getAllVeiculos().then(offlineData => {
                renderizarRegistros(offlineData);
                console.log("Dados carregados do IndexedDB como fallback.");
            });
        }
    } else {
        // Se a tag de script não for encontrada ou estiver vazia
        console.log("Nenhum dado do servidor encontrado. Carregando dados offline...");
        getAllVeiculos().then(offlineData => {
            renderizarRegistros(offlineData);
            if (offlineData.length > 0) {
                console.log("Dados carregados do IndexedDB.");
            } else {
                console.log("IndexedDB vazio. Nenhum registro para exibir.");
            }
        }).catch(err => {
            console.error('Erro ao carregar dados offline:', err);
        });
    }
});