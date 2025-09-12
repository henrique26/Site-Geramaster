// Cria ou abre o banco de dados IndexedDB
const dbPromise = idb.open('geramaster-db', 1, upgradeDB => {
  upgradeDB.createObjectStore('veiculos-registros', { keyPath: 'id', autoIncrement: true });
});

// Salva os dados do formulário no IndexedDB
async function saveOfflineData(data) {
  const db = await dbPromise;
  const tx = db.transaction('veiculos-registros', 'readwrite');
  tx.objectStore('veiculos-registros').add(data);
  return tx.done;
}

// Envia os dados offline para o servidor
async function sendOfflineData() {
  if (navigator.onLine) {
    const db = await dbPromise;
    const tx = db.transaction('veiculos-registros', 'readonly');
    const records = await tx.objectStore('veiculos-registros').getAll();
    
    records.forEach(async (record) => {
      try {
        const response = await fetch('/salvar-registro-offline', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(record),
        });

        if (response.ok) {
          console.log('Registro offline enviado com sucesso para o servidor:', record.id);
          // Se o envio for bem-sucedido, exclui o registro local
          const delTx = db.transaction('veiculos-registros', 'readwrite');
          delTx.objectStore('veiculos-registros').delete(record.id);
          await delTx.done;
        } else {
          console.error('Falha ao enviar registro offline:', record.id, response.statusText);
        }
      } catch (error) {
        console.error('Erro de rede ao tentar enviar registro offline:', error);
      }
    });
  }
}

// Lógica de manipulação do formulário
document.addEventListener('DOMContentLoaded', () => {
  const form = document.querySelector('form[name="salvar_registro"]');
  if (form) {
    form.addEventListener('submit', async (event) => {
      // Impede o envio padrão do formulário
      event.preventDefault();

      const formData = new FormData(form);
      const data = {};
      formData.forEach((value, key) => data[key] = value);

      if (navigator.onLine) {
        // Se estiver online, envia para o servidor (padrão)
        // Isso assume que você já tem um endpoint para isso.
        // O código a seguir é um exemplo de como seria.
        // Você pode manter sua lógica de envio atual aqui.
        try {
          const response = await fetch(form.action, {
            method: form.method,
            body: formData,
          });
          if (response.ok) {
            alert('Registro salvo online com sucesso!');
            form.reset();
            // Dispara a sincronização caso hajam dados pendentes
            sendOfflineData();
          } else {
            throw new Error('Falha ao salvar online.');
          }
        } catch (error) {
          console.error('Erro ao salvar online:', error);
          alert('Erro ao salvar online. Salvando offline...');
          await saveOfflineData(data);
          alert('Registro salvo offline. Será sincronizado quando a conexão voltar.');
          form.reset();
        }
      } else {
        // Se estiver offline, salva no IndexedDB
        await saveOfflineData(data);
        alert('Registro salvo offline. Ele será enviado quando a conexão voltar.');
        form.reset();
      }
    });
  }

  // Tenta sincronizar os dados offline ao carregar a página
  // e também quando a conexão for restabelecida
  window.addEventListener('online', sendOfflineData);
  sendOfflineData();
});