import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash, send_file
import pytz
from datetime import datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import DictCursor
import requests
import secrets
from io import BytesIO
from fpdf import FPDF
from collections import Counter, defaultdict
import json
import decimal
# --- Configuração do Flask ---
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', '2c306f12cb2487428dec7af91fe8c021ef4e1cc439bc0638c9e4debe7abe00b3')

# --- Credenciais e Endpoints do Bling ---
BLING_CLIENT_ID = os.environ.get('BLING_CLIENT_ID', 'd2b6ea30918ada35ce9475a1040705eec542a504')
BLING_CLIENT_SECRET = os.environ.get('BLING_CLIENT_SECRET', 'baa38ff9a1730897e4daf78ed555bb8c1a08364899d9f3c1f7e2fac2915c')
# ATENÇÃO: LEMBRE-SE DE ATUALIZAR ESTA URL DO NGROK REGULARMENTE!
BLING_REDIRECT_URI = os.environ.get('BLING_REDIRECT_URI', 'https://site-geramaster.onrender.com/callback') 

BLING_AUTHORIZE_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
BLING_TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"
BLING_API_BASE_URL = "https://api.bling.com.br/Api/v3/"

# --- Configuração de Fuso Horário ---
fuso_brasilia = pytz.timezone('America/Sao_Paulo')

# --- FUNÇÕES DE CONEXÃO E INICIALIZAÇÃO DO BANCO DE DADOS ---

def get_connection():
    """Retorna uma nova conexão de banco de dados PostgreSQL."""
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        return psycopg2.connect(database_url)
    else:
        return psycopg2.connect(
            dbname="geramaster_db",
            user="geramaster_db_user",
            password="TJ30dPQcC78FISPHI0QLRotCOeskemab",
            host="dpg-d0lrj9umcj7s73891d70-a.oregon-postgres.render.com",
            port="5432"
        )

def init_db():
    """
    Cria as tabelas do banco de dados, se não existirem, e insere usuários padrão.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS usuarios (
                        id SERIAL PRIMARY KEY,
                        usuario TEXT UNIQUE NOT NULL,
                        senha TEXT NOT NULL,
                        tipo TEXT NOT NULL,
                        nome TEXT NOT NULL
                    )
                ''')

                try:
                    cursor.execute("ALTER TABLE registros ADD COLUMN IF NOT EXISTS latitude REAL;")
                    cursor.execute("ALTER TABLE registros ADD COLUMN IF NOT EXISTS longitude REAL;")
                except Exception as e:
                    print(f"Aviso: Não foi possível adicionar colunas latitude/longitude (já existem ou outro erro): {e}")

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS registros (
                        id SERIAL PRIMARY KEY,
                        usuario TEXT NOT NULL,
                        tipo_registro TEXT NOT NULL,
                        data DATE NOT NULL,
                        hora TEXT NOT NULL,
                        latitude REAL,
                        longitude REAL
                    )
                ''')

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS rdvs (
                        id SERIAL PRIMARY KEY,
                        usuario TEXT NOT NULL,
                        valor TEXT NOT NULL,
                        descricao TEXT NOT NULL,
                        data DATE NOT NULL
                    )
                ''')

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS depositos (
                        id SERIAL PRIMARY KEY,
                        usuario TEXT NOT NULL,
                        valor REAL NOT NULL,
                        data DATE NOT NULL
                    )
                ''')

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS pendencias_os (
                        id SERIAL PRIMARY KEY,
                        numero_os TEXT NOT NULL,
                        data DATE NOT NULL,
                        cliente TEXT NOT NULL,
                        pendencia TEXT NOT NULL,
                        status TEXT DEFAULT 'Pendente',
                        criado_por INT REFERENCES usuarios(id),
                        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        numero_os_concluida TEXT,
                        observacoes TEXT,
                        data_conclusao TIMESTAMP,
                        pecas_necessarias TEXT
                    )
                ''')

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS controle_veiculos (
                        id SERIAL PRIMARY KEY,
                        placa VARCHAR(20) NOT NULL,
                        tipo_registro VARCHAR(20) NOT NULL CHECK (tipo_registro IN ('troca_oleo', 'abastecimento')),
                        data DATE NOT NULL,
                        km INTEGER NOT NULL,
                        litros REAL,
                        valor REAL,
                        proxima_troca_km INTEGER,
                        proxima_troca_data DATE,
                        observacoes TEXT
                    );
                ''')

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS bling_tokens (
                        id SERIAL PRIMARY KEY,
                        access_token TEXT NOT NULL,
                        refresh_token TEXT NOT NULL,
                        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                usuarios_iniciais = [
                    ('admin', '1234', 'admin', 'Administrador'),
                    ('henrique', 'henrique1234', 'tecnico', 'Henrique Antunes Fonseca'),
                    ('Euler', 'euler1234', 'tecnico', 'Euler Mendes Pena Silva'),
                    ('Alexon', 'alexon1234', 'tecnico','Alexon Braz de Deus'),
                    ('Carlos', 'carlos1234', 'tecnico', 'Carlos Alexandre de Oliveira'),
                    ('ivan', 'ivan1234', 'admin', 'Ivan Chagas Miranda'),
                    ('Wallace', 'wallace1234', 'admin', 'Afonso Wallace da Silva'),
                    ('Gestor', 'gera1234', 'admin', 'Geramaster Gestor')
                ]

                for usuario, senha, tipo, nome in usuarios_iniciais:
                    cursor.execute("SELECT * FROM usuarios WHERE usuario = %s", (usuario,))
                    if not cursor.fetchone():
                        cursor.execute("INSERT INTO usuarios (usuario, senha, tipo, nome) VALUES (%s, %s, %s, %s)",
                            (usuario, senha, tipo, nome))

                conn.commit()
            print("Tabelas do banco de dados verificadas/criadas com sucesso e usuários padrão inseridos.")
    except Exception as e:
        print(f"Erro ao configurar o banco de dados: {e}")
        raise

# --- FUNÇÕES DE GERENCIAMENTO DE TOKENS DO BLING ---

class BlingTokenData:
    """Objeto simples para encapsular os dados do token."""
    def __init__(self, access_token, refresh_token, expires_at):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at

    def is_access_token_expired(self):
        return datetime.now(timezone.utc) >= (self.expires_at - timedelta(minutes=5))

def get_bling_tokens_from_db():
    """Recupera o token do Bling armazenado no banco de dados e garante que a data de expiração é 'aware' e em UTC."""
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cursor:
                cursor.execute("SELECT access_token, refresh_token, expires_at FROM bling_tokens ORDER BY created_at DESC LIMIT 1")
                token_row = cursor.fetchone()
                if token_row:
                    expires_at_db = token_row['expires_at']

                    if expires_at_db.tzinfo is None:
                        expires_at_db = pytz.utc.localize(expires_at_db)
                    else:
                        expires_at_db = expires_at_db.astimezone(timezone.utc)

                    return BlingTokenData(
                        token_row['access_token'],
                        token_row['refresh_token'],
                        expires_at_db
                    )
            return None
    except Exception as e:
        print(f"Erro ao obter tokens do Bling do DB: {e}")
        return None

def refresh_bling_access_token():
    """Tenta renovar o access_token do Bling usando o refresh_token."""
    bling_token_obj = get_bling_tokens_from_db()

    if not bling_token_obj or not bling_token_obj.refresh_token:
        print("Nenhum refresh_token encontrado no banco de dados para renovação.")
        return None

    refresh_data = {
        'grant_type': 'refresh_token',
        'refresh_token': bling_token_obj.refresh_token,
    }

    print("Tentando renovar o access_token com o refresh_token...")
    try:
        response = requests.post(
            BLING_TOKEN_URL,
            data=refresh_data,
            auth=(BLING_CLIENT_ID, BLING_CLIENT_SECRET)
        )
        response.raise_for_status()

        new_tokens = response.json()
        print(f"Novos tokens recebidos do Bling: {new_tokens}")

        access_token = new_tokens.get('access_token')
        refresh_token = new_tokens.get('refresh_token', bling_token_obj.refresh_token) # Use o antigo se não vier um novo
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=new_tokens.get('expires_in', 0))

        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE bling_tokens
                    SET access_token = %s, refresh_token = %s, expires_at = %s, created_at = %s
                    WHERE id = (SELECT id FROM bling_tokens LIMIT 1)
                    """,
                    (access_token, refresh_token, expires_at, datetime.now(timezone.utc))
                )
                conn.commit()

        return access_token

    except requests.exceptions.RequestException as e:
        print(f"Erro ao renovar access_token: {e}")
        if e.response is not None:
            try:
                error_details = e.response.json()
            except ValueError:
                error_details = e.response.text
            print(f"Detalhes do erro de renovação: {e.response.status_code} - {error_details}")
        with get_connection() as conn:
            conn.rollback()
        return None
    except Exception as e:
        print(f"Erro inesperado durante a renovação do token: {e}")
        with get_connection() as conn:
            conn.rollback()
        return None

def bling_api_call(endpoint, method='GET', data=None):
    """
    Faz uma chamada à API do Bling, gerenciando a renovação do token automaticamente.
    """
    current_token_obj = get_bling_tokens_from_db()

    if not current_token_obj or not current_token_obj.access_token:
        print("Nenhum token Bling disponível. Por favor, conecte-se ao Bling primeiro.")
        flash("Sua conexão com o Bling não está ativa. Por favor, reconecte.")
        return None

    if current_token_obj.is_access_token_expired():
        print("Access token expirado. Tentando renovar...")
        new_access_token = refresh_bling_access_token()
        if not new_access_token:
            print("Falha ao renovar o access token. Requer reautenticação manual.")
            flash("Sua conexão com o Bling expirou e não pôde ser renovada automaticamente. Por favor, reconecte.")
            return None
        current_token_obj.access_token = new_access_token

    headers = {
        'Authorization': f'Bearer {current_token_obj.access_token}',
        'Content-Type': 'application/json'
    }

    url = f"{BLING_API_BASE_URL}{endpoint}"
    print(f"Fazendo chamada à API: {method} {url}")

    try:
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, params=data)
        elif method.upper() == 'POST':
            response = requests.post(url, headers=headers, json=data)
        elif method.upper() == 'PUT':
            response = requests.put(url, headers=headers, json=data)
        elif method.upper() == 'DELETE':
            response = requests.delete(url, headers=headers)
        else:
            raise ValueError("Método HTTP não suportado.")

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        print(f"Erro na chamada à API do Bling para {endpoint}: {e}")
        if e.response is not None:
            try:
                error_details = e.response.json()
            except ValueError:
                error_details = e.response.text
            print(f"Detalhes do erro da API: {e.response.status_code} - {error_details}")
        return None
    except Exception as e:
        print(f"Erro inesperado na chamada da API Bling: {e}")
        return None

# --- ROTAS DO FLASK ---

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        senha = request.form['senha']
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # Certifique-se de que a query retorna o 'id' do usuário como a primeira coluna (índice 0)
                cursor.execute("SELECT id, usuario, senha, tipo, nome FROM usuarios WHERE usuario = %s AND senha = %s", (usuario, senha))
                user = cursor.fetchone()
                if user:
                    session['user_id'] = user[0] # NOVO: Salva o ID do usuário na sessão (índice 0)
                    session['usuario'] = user[1] # Nome de usuário/login
                    session['tipo'] = user[3]    # Tipo de usuário (admin, tecnico)
                    session['nome'] = user[4]    # Nome completo do usuário

                    flash('Login bem-sucedido!', 'success')
                    
                    # Verifica saldos se for admin
                    if user[3] == 'admin': # user[3] é o 'tipo'
                        cursor.execute("""
                        SELECT u.usuario, u.nome,
                            COALESCE(dep.total, 0) - COALESCE(rdv.total, 0) AS saldo
                        FROM usuarios u
                        LEFT JOIN (
                            SELECT usuario, SUM(valor) AS total
                            FROM depositos
                            GROUP BY usuario
                        ) dep ON dep.usuario = u.usuario
                        LEFT JOIN (
                            SELECT usuario, SUM(CAST(valor AS NUMERIC)) AS total
                            FROM rdvs
                            GROUP BY usuario
                        ) rdv ON rdv.usuario = u.usuario
                        WHERE u.tipo = 'tecnico'
                        AND (COALESCE(dep.total, 0) - COALESCE(rdv.total, 0)) < 100
                        """)
                        alertas = cursor.fetchall()
                        session['alertas'] = [(usuario, nome, float(saldo)) for usuario, nome, saldo in alertas]
                    
                    return redirect(url_for('menu'))
                else:
                    flash('Usuário ou senha inválidos.', 'danger')
                    return render_template('login.html')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Você foi desconectado com sucesso!', 'info')
    return redirect(url_for('login'))

@app.route('/menu')
def menu():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    # Coleta o tipo de usuário da sessão
    tipo_usuario = session.get('tipo') 

    alertas = session.pop('alertas', []) if tipo_usuario == 'admin' else []
    avisos_troca_oleo = []

    with get_connection() as conn:
        with conn.cursor() as cursor:
            # ... (Sua consulta SQL para avisos_troca_oleo) ...
            cursor.execute("""
                WITH UltimosKMsGerais AS (
                    SELECT
                        placa,
                        MAX(km) AS ultimo_km_geral,
                        MAX(data) AS ultima_data_geral
                    FROM controle_veiculos
                    GROUP BY placa
                ),
                UltimasTrocasOleo AS (
                    SELECT
                        placa,
                        proxima_troca_km,
                        ROW_NUMBER() OVER(PARTITION BY placa ORDER BY data DESC, id DESC) as rn
                    FROM controle_veiculos
                    WHERE tipo_registro = 'troca_oleo' AND proxima_troca_km IS NOT NULL
                )
                SELECT
                    ukg.placa,
                    ukg.ultimo_km_geral,
                    uto.proxima_troca_km
                FROM UltimosKMsGerais ukg
                JOIN UltimasTrocasOleo uto ON ukg.placa = uto.placa
                WHERE uto.rn = 1;
            """)
            resultados = cursor.fetchall()

            for placa, ultimo_km_geral, proxima_troca_km in resultados:
                if proxima_troca_km is not None:
                    km_restante = proxima_troca_km - ultimo_km_geral
                    if km_restante <= 1000: # Condição para exibir o aviso
                        avisos_troca_oleo.append({
                            'placa': placa,
                            'km_restante': km_restante
                        })

    return render_template('menu.html',
                           usuario=session['usuario'],
                           tipo=tipo_usuario, # Mantém 'tipo' para compatibilidade se outras partes do template usarem
                           user_type=tipo_usuario, # <--- ADICIONADO: Passa 'user_type' explicitamente
                           alertas=alertas,
                           avisos_troca_oleo=avisos_troca_oleo)

# Sua função calcular_saldo existente
def calcular_saldo(usuario):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT SUM(valor) FROM depositos WHERE usuario = %s", (usuario,))
            total_depositos = cursor.fetchone()[0] or 0
            cursor.execute("SELECT SUM(CAST(valor AS REAL)) FROM rdvs WHERE usuario = %s", (usuario,))
            total_gastos = cursor.fetchone()[0] or 0
    return total_depositos - total_gastos

@app.route('/rdv', methods=['GET', 'POST'])
def rdv():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    tipo_usuario = session.get('tipo')
    usuario = session.get('usuario')

    # --- Lógica de Registro de Novo RDV (Requisição POST) ---
    if request.method == 'POST' and tipo_usuario in ['tecnico', 'admin']:
        try:
            valor = float(request.form['valor'].replace(',', '.'))
            descricao = request.form['descricao']
            data_str = request.form['data'] # String da data do formulário

            # Converter a string da data para um objeto date do Python
            data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()

            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("INSERT INTO rdvs (usuario, valor, descricao, data) VALUES (%s, %s, %s, %s)",
                                   (usuario, valor, descricao, data_obj)) # Passa o objeto date
                    conn.commit()
            flash('RDV registrado com sucesso!', 'success')
        except ValueError:
            flash('Erro ao registrar RDV: Valor ou formato de data inválido.', 'danger')
        except Exception as e:
            flash(f'Erro ao registrar RDV: {e}', 'danger')
        return redirect(url_for('rdv'))

    # --- Lógica de Filtragem e Exibição de RDVs (Requisição GET) ---

    # Coleta os filtros da URL (parâmetros GET)
    # Garante que, se o parâmetro não estiver na URL, ele seja uma string vazia
    filtro_usuario = request.args.get('filtro_usuario', '').strip()
    filtro_data_unica_str = request.args.get('filtro_data', '').strip()
    filtro_data_inicio_str = request.args.get('filtro_data_inicio', '').strip()
    filtro_data_fim_str = request.args.get('filtro_data_fim', '').strip()

    # Variáveis para passar ao template
    rdvs = []
    tecnicos = []
    saldo = 0 # Saldo do usuário logado (se for técnico)
    saldos = {} # Saldos de todos os técnicos (se for admin)
    ultimo_deposito = None

    is_admin = (tipo_usuario == 'admin') # Variável para controle no template

    try:
        with get_connection() as conn:
            # Usar DictCursor para facilitar o acesso aos resultados por nome da coluna
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor: # ou simplesmente cursor() se não usa DictCursor
                base_query = """
                             SELECT rdv.id, rdv.usuario, rdv.valor, rdv.descricao, rdv.data, u.nome
                             FROM rdvs rdv
                             LEFT JOIN usuarios u ON rdv.usuario = u.usuario
                             """
                conditions = []
                params = []

                # Condição base para técnico ou admin
                if not is_admin: # Se não for admin, só vê os próprios RDVs
                    conditions.append("rdv.usuario = %s")
                    params.append(usuario)

                # Aplicar filtro de usuário para admin
                if is_admin and filtro_usuario:
                    conditions.append("rdv.usuario = %s")
                    params.append(filtro_usuario)

                # Lógica de filtro de datas (prioriza intervalo, depois data única)
                # Tentar converter strings de data para objetos date para a consulta SQL
                data_inicio_obj = None
                data_fim_obj = None
                data_unica_obj = None

                if filtro_data_inicio_str and filtro_data_fim_str:
                    try:
                        data_inicio_obj = datetime.strptime(filtro_data_inicio_str, '%Y-%m-%d').date()
                        data_fim_obj = datetime.strptime(filtro_data_fim_str, '%Y-%m-%d').date()
                        conditions.append("rdv.data BETWEEN %s AND %s")
                        params.append(data_inicio_obj)
                        params.append(data_fim_obj)
                    except ValueError:
                        flash('Formato de data de início/fim inválido para o filtro.', 'warning')
                        # Limpar as variáveis do filtro para que não tentem ser repassadas de forma inválida
                        filtro_data_inicio_str = ''
                        filtro_data_fim_str = ''
                elif filtro_data_unica_str:
                    try:
                        data_unica_obj = datetime.strptime(filtro_data_unica_str, '%Y-%m-%d').date()
                        conditions.append("rdv.data = %s")
                        params.append(data_unica_obj)
                    except ValueError:
                        flash('Formato de data única inválido para o filtro.', 'warning')
                        filtro_data_unica_str = ''


                final_query = base_query
                if conditions:
                    final_query += " WHERE " + " AND ".join(conditions)
                final_query += " ORDER BY rdv.data DESC"

                cursor.execute(final_query, tuple(params)) # tuple(params) é importante para o psycopg2
                rdvs_raw = cursor.fetchall()

                # Processar os RDVs para ter o nome completo do técnico
                rdvs = []
                for rdv_item in rdvs_raw:
                    # Se você usou DictCursor, pode acessar como rdv_item['nome_da_coluna']
                    # Se usou cursor() padrão, é rdv_item[indice_da_coluna]
                    rdvs.append((
                        rdv_item['id'],
                        rdv_item['usuario'], # Nome de usuário do técnico
                        float(rdv_item['valor']),
                        rdv_item['descricao'],
                        rdv_item['data'].strftime('%Y-%m-%d') # Formatar a data para o input HTML
                    ))

                # Lógica para obter a lista de técnicos (apenas para admin)
                if is_admin:
                    cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico' ORDER BY usuario")
                    tecnicos = [row[0] for row in cursor.fetchall()]

                    # Calcular saldos para todos os técnicos (apenas admin)
                    for tecnico_username in tecnicos:
                        cursor.execute("SELECT COALESCE(SUM(valor),0) FROM depositos WHERE usuario = %s", (tecnico_username,))
                        total_depositos = cursor.fetchone()[0]
                        cursor.execute("SELECT COALESCE(SUM(CAST(valor AS REAL)),0) FROM rdvs WHERE usuario = %s", (tecnico_username,))
                        total_rdvs = cursor.fetchone()[0]
                        saldos[tecnico_username] = float(total_depositos) - float(total_rdvs)
                else: # Se for técnico, calcula apenas o seu saldo e último depósito
                    cursor.execute("SELECT COALESCE(SUM(valor),0) FROM depositos WHERE usuario = %s", (usuario,))
                    total_depositos = cursor.fetchone()[0]
                    cursor.execute("SELECT COALESCE(SUM(CAST(valor AS REAL)),0) FROM rdvs WHERE usuario = %s", (usuario,))
                    total_rdvs = cursor.fetchone()[0]
                    saldo = float(total_depositos) - float(total_rdvs)

                    # Consulta do último depósito para técnico logado
                    cursor.execute(
                        "SELECT valor, data FROM depositos WHERE usuario = %s ORDER BY data DESC LIMIT 1", (usuario,)
                    )
                    result = cursor.fetchone()
                    if result:
                        ultimo_deposito = {'valor': float(result['valor']), 'data': result['data']} # Acessar por nome se for DictCursor
                    else:
                        ultimo_deposito = None

    except Exception as e:
        flash(f'Erro ao carregar dados: {e}', 'danger')
        print(f"DEBUG: Erro ao carregar dados na rota RDV: {e}") # Para ver no console do servidor

    # Renderiza o template passando todas as variáveis necessárias
    return render_template('rdv.html',
                           rdvs=rdvs,
                           tipo=tipo_usuario,
                           usuario=usuario,
                           tecnicos=tecnicos,
                           filtro_usuario=filtro_usuario,
                           filtro_data=filtro_data_unica_str, # Passe a string original para o value do input
                           filtro_data_inicio=filtro_data_inicio_str, # Passe a string original
                           filtro_data_fim=filtro_data_fim_str,     # Passe a string original
                           is_admin=is_admin,
                           saldo=saldo,
                           saldos=saldos,
                           ultimo_deposito=ultimo_deposito,
                           user_type=tipo_usuario # Use user_type para o debug no HTML, se preferir
                           )

@app.route('/deposito', methods=['GET', 'POST'])
def deposito():
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        usuario = request.form['usuario']
        try:
            valor = float(request.form['valor'])
        except ValueError:
            flash('Valor inválido! Digite um número.')
            return redirect(url_for('deposito'))

        data = request.form['data']

        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO depositos (usuario, valor, data) VALUES (%s, %s, %s)",
                               (usuario, valor, data))
                conn.commit()
        flash('Depósito registrado com sucesso!')
        return redirect(url_for('deposito'))

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico'")
            tecnicos = [row[0] for row in cursor.fetchall()]

            cursor.execute("SELECT id, valor, data, usuario FROM depositos ORDER BY data DESC")
            depositos_raw = cursor.fetchall()

    depositos = []
    for dep in depositos_raw:
        id_, valor_, data_, usuario_ = dep

        # Tenta converter para datetime e formatar para string BR dd/mm/yyyy
        data_str = ''
        if data_ is not None:
            if isinstance(data_, str):
                try:
                    dt_obj = datetime.strptime(data_, '%Y-%m-%d')
                    data_str = dt_obj.strftime('%d/%m/%Y')
                except Exception:
                    data_str = data_  # se der ruim, manda a string original
            else:
                # Se for date/datetime do banco, formata direto
                try:
                    data_str = data_.strftime('%d/%m/%Y')
                except Exception:
                    data_str = str(data_)

        depositos.append((id_, valor_, data_str, usuario_))

    return render_template('deposito.html', tecnicos=tecnicos, depositos=depositos)



@app.route('/deposito/delete/<int:deposito_id>', methods=['POST'])
def deletar_deposito(deposito_id):
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM depositos WHERE id = %s", (deposito_id,))
            conn.commit()
    return redirect(url_for('deposito'))

@app.route('/delete_rdv/<int:rdv_id>', methods=['POST'])
def delete_rdv(rdv_id):
    # Supondo que você tenha uma função para deletar RDV do banco
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM rdvs WHERE id = %s', (rdv_id,))
            conn.commit()
    return redirect(url_for('rdv'))  # Altere 'rdv' para o nome da sua rota que lista os RDVs

@app.route('/exportar_pdf')
def exportar_pdf():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    usuario = session['usuario']
    tipo_usuario = session.get('tipo')

    filtro_data_inicio = request.args.get('filtro_data_inicio')
    filtro_data_fim = request.args.get('filtro_data_fim')
    filtro_usuario = request.args.get('filtro_usuario') if tipo_usuario == 'admin' else usuario

    query = "SELECT * FROM registros WHERE 1=1"
    params = []

    if tipo_usuario != 'admin':
        query += " AND usuario = %s"
        params.append(usuario)
    else:
        if filtro_usuario:
            query += " AND usuario = %s"
            params.append(filtro_usuario)

    if filtro_data_inicio:
        query += " AND data >= %s"
        params.append(filtro_data_inicio)
    if filtro_data_fim:
        query += " AND data <= %s"
        params.append(filtro_data_fim)

    query += " ORDER BY data ASC, hora ASC"

    with get_connection() as conn:
        with conn.cursor() as cursor:
            # Buscar registros
            cursor.execute(query, tuple(params))
            registros = cursor.fetchall()

            # Pega usuários únicos dos registros para buscar nome completo
            usuarios_unicos = list(set([r[1] for r in registros]))

            if usuarios_unicos:
                format_strings = ','.join(['%s'] * len(usuarios_unicos))
                cursor.execute(
                    f"SELECT usuario, nome FROM usuarios WHERE usuario IN ({format_strings})",
                    tuple(usuarios_unicos)
                )
                nomes_result = cursor.fetchall()
                # Dicionário usuário -> nome completo
                usuario_nome_dict = {u: n for u, n in nomes_result}
            else:
                usuario_nome_dict = {}

    # Montar o título do PDF
    datas = [r[3] for r in registros if r[3]]
    if datas:
        try:
            datas_convertidas = [datetime.strptime(d, '%Y-%m-%d') if isinstance(d, str) else d for d in datas]
        except Exception:
            datas_convertidas = datas

        datas_str = [d.strftime('%Y-%m-%d') for d in datas_convertidas]
        contagem = Counter(datas_str)
        data_mais_comum = contagem.most_common(1)[0][0]
        dt = datetime.strptime(data_mais_comum, '%Y-%m-%d')
        mes_ano = dt.strftime('%m/%Y')
    else:
        mes_ano = "Data Indisponível"

    if tipo_usuario == 'admin':
        if filtro_usuario:
            nome_tec_exibir = usuario_nome_dict.get(filtro_usuario, filtro_usuario)
        else:
            nome_tec_exibir = "Todos os Técnicos"
        titulo = f"Folha de ponto - {mes_ano}"
    else:
        nome_tec_exibir = usuario_nome_dict.get(usuario, usuario)
        titulo = f"{nome_tec_exibir} - Folha de ponto - {mes_ano}"

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.image('static/Geramaster logo Preto fundo transparente.png', 140, 2, 66)
    pdf.cell(0, 10, titulo, 0, 1, 'C')
    pdf.ln(1)

    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, f"Técnico: {nome_tec_exibir}", 0, 1, 'L')
    pdf.ln(2)

    headers = [
        "Data\n ", 
        "Entrada\nTrabalho", 
        "Entrada\nAlmoço", 
        "Retorno\nAlmoço", 
        "Saída\nTrabalho", 
        "Entrada\nJanta", 
        "Retorno\nJanta",  
        "Hora\nExtra"
    ]
    col_widths = [28, 18, 18, 18, 18, 18, 18, 18]

    pdf.set_font("Arial", "B", 9)

    x_start = pdf.get_x()
    y_start = pdf.get_y()
    altura_celula = 6

    # headers que precisam de linha extra pra balancear visualmente
    headers_ajustados = []
    for h in headers:
        if '\n' not in h:
            h += '\n '  # adiciona uma linha em branco pra "forçar" quebra de linha e altura
        headers_ajustados.append(h)

    for i, header in enumerate(headers_ajustados):
        pdf.set_xy(x_start + sum(col_widths[:i]), y_start)
        pdf.multi_cell(col_widths[i], altura_celula, header, border=1, align='C')




    data_dict = {}
    for r in registros:
        key = (r[1], r[3])  # (usuario, data)
        if key not in data_dict:
            data_dict[key] = {
                'entrada': '', 
                'entrada_almoco': '12:00', 
                'retorno_almoco': '13:12',
                'entrada_janta': '', 
                'retorno_janta': '', 
                'saida': '', 
                'saida_hora_extra': ''
            }

        if r[2] == 'entrada':
            data_dict[key]['entrada'] = r[4]
        elif r[2] == 'saida':
            data_dict[key]['saida'] = r[4]
        elif r[2] == 'entrada_janta':
            data_dict[key]['entrada_janta'] = r[4]
        elif r[2] == 'retorno_janta':
            data_dict[key]['retorno_janta'] = r[4]
        elif r[2] == 'saida_hora_extra':
            data_dict[key]['saida_hora_extra'] = r[4]

    pdf.set_font("Arial", "", 9)

    for (tec, data), times in data_dict.items():
        data_obj = datetime.strptime(data, '%Y-%m-%d') if isinstance(data, str) else data
        dias_semana = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
        dia_semana_str = dias_semana[data_obj.weekday()]
        data_formatada = f"{dia_semana_str} - {data_obj.strftime('%d/%m/%Y')}"

        nome_completo = usuario_nome_dict.get(tec, tec)

        pdf.cell(col_widths[0], 8, data_formatada, 1, 0, 'C')
        pdf.cell(col_widths[1], 8, times['entrada'], 1, 0, 'C')
        pdf.cell(col_widths[2], 8, times['entrada_almoco'], 1, 0, 'C')
        pdf.cell(col_widths[3], 8, times['retorno_almoco'], 1, 0, 'C')
        pdf.cell(col_widths[4], 8, times['saida'], 1, 0, 'C')
        pdf.cell(col_widths[5], 8, times['entrada_janta'], 1, 0, 'C')
        pdf.cell(col_widths[6], 8, times['retorno_janta'], 1, 0, 'C')
        pdf.cell(col_widths[7], 8, times['saida_hora_extra'], 1, 0, 'C')  # <- última célula: ln=1 para pular linha

        pdf.ln()

    pdf.ln(4)
    pdf.cell(0, 10, 'Assinatura do Técnico: _________________________________________', 0, 1)

    pdf_bytes = pdf.output(dest='S').encode('latin1')
    pdf_output = BytesIO(pdf_bytes)

    return send_file(pdf_output, download_name='folha_de_ponto.pdf', as_attachment=True, mimetype='application/pdf')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    usuario = session['usuario']
    tipo_usuario = session.get('tipo')

    if request.method == 'POST':
        acao = request.form['acao']

        if acao == 'registrar_agora':
            tipo = request.form['tipo_registro']
            agora = datetime.now(fuso_brasilia)
            hora = agora.strftime('%H:%M:%S')
            data = agora.strftime('%Y-%m-%d')
            latitude = request.form.get('latitude')
            longitude = request.form.get('longitude')

        elif acao == 'registrar_manual':
            tipo = request.form['tipo_registro_manual']
            data = request.form['data']
            hora = request.form['hora']
            latitude_str = request.form.get('latitude', '').strip()
            longitude_str = request.form.get('longitude', '').strip()

            try:
                latitude = float(latitude_str) if latitude_str else None
            except ValueError:
                latitude = None

            try:
                longitude = float(longitude_str) if longitude_str else None
            except ValueError:
                longitude = None
         
        else:
            return redirect(url_for('dashboard'))

        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO registros (usuario, tipo_registro, data, hora, latitude, longitude) VALUES (%s, %s, %s, %s, %s, %s)",
                    (usuario, tipo, data, hora, latitude, longitude)
                    )

                conn.commit()
        return redirect(url_for('dashboard'))

    filtro_data = request.args.get('filtro_data', '')
    filtro_data_inicio = request.args.get('filtro_data_inicio', '')
    filtro_data_fim = request.args.get('filtro_data_fim', '')
    filtro_usuario = request.args.get('filtro_usuario', '')

    with get_connection() as conn:
        with conn.cursor() as cursor:
            if tipo_usuario == 'admin':
                query = "SELECT * FROM registros WHERE TRUE"
                params = []

                if filtro_usuario:
                    query += " AND usuario = %s"
                    params.append(filtro_usuario)

                if filtro_data_inicio and filtro_data_fim:
                    query += " AND data BETWEEN %s AND %s"
                    params.append(filtro_data_inicio)
                    params.append(filtro_data_fim)
                elif filtro_data:
                    query += " AND data = %s"
                    params.append(filtro_data)

                query += " ORDER BY usuario ASC, data ASC, hora ASC"
                cursor.execute(query, params)
                registros = cursor.fetchall()

                cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico'")
                tecnicos = [row[0] for row in cursor.fetchall()]

            else:
                query = "SELECT * FROM registros WHERE usuario = %s"
                params = [usuario]

                if filtro_data_inicio and filtro_data_fim:
                    query += " AND data BETWEEN %s AND %s"
                    params.append(filtro_data_inicio)
                    params.append(filtro_data_fim)
                elif filtro_data:
                    query += " AND data = %s"
                    params.append(filtro_data)

                query += " ORDER BY data ASC, hora ASC"
                cursor.execute(query, params)
                registros = cursor.fetchall()
                tecnicos = []

            # Agora vamos agrupar registros por (usuario, data)
            registros_agrupados = {}

            for r in registros:
                id_registro = r[0]
                usuario_registro = r[1]
                tipo_registro = r[2]
                data = r[3]
                hora = r[4]
                latitude = r[5]
                longitude = r[6]

                key = (usuario_registro, data)

                if key not in registros_agrupados:
                    registros_agrupados[key] = {
                        'entrada': None,
                        'entrada_almoco': {'id': None, 'hora': '12:00'},
                        'retorno_almoco': {'id': None, 'hora': '13:12'},
                        'saida': None,
                        'entrada_janta': None,
                        'retorno_janta': None,
                        'saida_hora_extra': None
                    }

                if tipo_registro == 'entrada':
                    registros_agrupados[key]['entrada'] = {'id': id_registro, 'hora': hora, 'latitude': latitude,'longitude': longitude}
                elif tipo_registro == 'saida':
                    registros_agrupados[key]['saida'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}
                elif tipo_registro == 'entrada_janta':
                    registros_agrupados[key]['entrada_janta'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}
                elif tipo_registro == 'retorno_janta':
                    registros_agrupados[key]['retorno_janta'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}
                elif tipo_registro == 'saida_hora_extra':
                    registros_agrupados[key]['saida_hora_extra'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}


    return render_template(
        'dashboard.html',
        registros_agrupados=registros_agrupados,
        registros=registros,
        usuario=usuario,
        tipo=tipo_usuario,
        tecnicos=tecnicos,
        filtro_usuario=filtro_usuario,
        filtro_data=filtro_data,
        filtro_data_inicio=filtro_data_inicio,
        filtro_data_fim=filtro_data_fim,
        is_admin=(tipo_usuario == 'admin')
    )

@app.route('/excluir/<int:id>', methods=['POST'])
def excluir_registro(id):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM registros WHERE id = %s", (id,))
            conn.commit()

    return redirect(url_for('dashboard'))

@app.route('/excluir_dia/<data>', methods=['POST'])
def excluir_dia(data):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    usuario = session['usuario']
    tipo_usuario = session.get('tipo')

    with get_connection() as conn:
        with conn.cursor() as cursor:
            if tipo_usuario == 'admin':
                # Se for admin, pode escolher usuário pela query string (exemplo)
                filtro_usuario = request.args.get('usuario')
                if filtro_usuario:
                    cursor.execute("DELETE FROM registros WHERE usuario = %s AND data = %s", (filtro_usuario, data))
                else:
                    # Se admin não passou usuário, evita apagar geral
                    return redirect(url_for('dashboard'))
            else:
                # Técnico só apaga dele mesmo
                cursor.execute("DELETE FROM registros WHERE usuario = %s AND data = %s", (usuario, data))
            conn.commit()
    return redirect(url_for('dashboard'))

@app.route('/pendencias', methods=['GET', 'POST'])
def pendencias():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    usuario = session['usuario']
    tipo_usuario = session.get('tipo')

    if request.method == 'POST':
        acao = request.form.get('acao')

        if acao == 'adicionar':
            numero_os = request.form['numero_os']
            data = request.form['data']
            cliente = request.form['cliente']
            pendencia = request.form['pendencia']

            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('''
                        SELECT id FROM usuarios WHERE usuario = %s
                    ''', (usuario,))
                    criador = cursor.fetchone()
                    if criador:
                        criado_por = criador[0]
                        cursor.execute('''
                            INSERT INTO pendencias_os (numero_os, data, cliente, pendencia, criado_por)
                            VALUES (%s, %s, %s, %s, %s)
                        ''', (numero_os, data, cliente, pendencia, criado_por))
                        conn.commit()


        

        elif acao == 'resolver':
            id_pendencia = request.form['id_pendencia']
            numero_os_concluida = request.form.get('numero_os_concluida')
            observacoes = request.form.get('observacoes')

            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE pendencias_os 
                        SET status = 'Resolvida',
                            concluido_por = %s,
                            numero_os_concluida = %s,
                            observacoes = %s,
                            data_conclusao = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (usuario, numero_os_concluida, observacoes, id_pendencia))
                    conn.commit()



        elif acao == 'excluir':
            id_pendencia = request.form['id_pendencia']
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('SELECT criado_por FROM pendencias_os WHERE id = %s', (id_pendencia,))
                    result = cursor.fetchone()

                    if result:
                        criador_id = result[0]
                        cursor.execute('SELECT id FROM usuarios WHERE usuario = %s', (usuario,))
                        usuario_id = cursor.fetchone()[0]

                        if tipo_usuario == 'admin':
                            cursor.execute('DELETE FROM pendencias_os WHERE id = %s', (id_pendencia,))
                            conn.commit()

        return redirect(url_for('pendencias'))

    # 🔍 Filtros GET
        # 🔍 Filtros GET
    filtro_os = request.args.get('numero_os', '')
    filtro_cliente = request.args.get('cliente', '')
    filtro_data = request.args.get('data', '')

    with get_connection() as conn:
        with conn.cursor() as cursor:
            query = '''
                SELECT p.id, p.numero_os, p.data, p.cliente, p.pendencia, p.status, u.usuario, p.concluido_por, p.numero_os_concluida, p.observacoes, p.data_conclusao
                FROM pendencias_os p        
                JOIN usuarios u ON p.criado_por = u.id
            '''

            conditions = []
            params = []

            if filtro_os:
                conditions.append("p.numero_os ILIKE %s")
                params.append(f"%{filtro_os}%")

            if filtro_cliente:
                conditions.append("p.cliente ILIKE %s")
                params.append(f"%{filtro_cliente}%")

            if filtro_data:
                conditions.append("p.data = %s")
                params.append(filtro_data)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += ' ORDER BY p.data DESC'

            cursor.execute(query, params)
            pendencias = cursor.fetchall()


    return render_template('pendencias.html',
                           pendencias=pendencias,
                           usuario=usuario,
                           tipo=tipo_usuario,
                           filtro_os=filtro_os,
                           filtro_cliente=filtro_cliente,
                           filtro_data=filtro_data)

@app.route('/controle_veiculos', methods=['GET', 'POST'])
def controle_veiculos():
    # Permite acesso a administradores e técnicos
    if 'usuario' not in session or session.get('tipo') not in ['admin', 'tecnico']:
        flash('Você não tem permissão para acessar esta página.', 'danger')
        return redirect(url_for('login'))

    # Obtém o tipo de usuário e o ID do usuário logado
    user_type = session.get('tipo')
    user_id_logado = session.get('user_id') 

    # Se o ID do usuário não estiver na sessão, redireciona para o login
    if not user_id_logado:
        flash('Erro: ID do usuário logado não encontrado na sessão. Por favor, faça login novamente.', 'danger')
        return redirect(url_for('login'))

    # --- Lógica de POST (salvar/excluir) permanece inalterada ---
    if request.method == 'POST':
        acao = request.form.get('acao')

        if acao == 'salvar_registro':
            placa = request.form['placa']
            tipo_registro = request.form['tipo_registro']
            data_reg_str = request.form['data']
            
            # NOVO: Restrição para técnicos no salvamento de registros
            if user_type == 'tecnico' and tipo_registro != 'abastecimento':
                flash('Técnicos podem registrar apenas abastecimentos.', 'danger')
                return redirect(url_for('controle_veiculos'))

            try:
                data_reg = datetime.strptime(data_reg_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Formato de data inválido. Use AAAA-MM-DD.', 'danger')
                return redirect(url_for('controle_veiculos'))

            km_input = request.form['km']
            try:
                km_limpo = km_input.replace('.', '').replace(',', '')
                km = int(km_limpo)
            except ValueError:
                flash('Valor de KM inválido. Por favor, insira apenas números.', 'danger')
                return redirect(url_for('controle_veiculos'))

            try:
                litros = float(request.form['litros']) if request.form.get('litros') else None
                valor = float(request.form['valor']) if request.form.get('valor') else None
            except ValueError:
                flash('Valores de Litros ou Valor inválidos. Use apenas números.', 'danger')
                return redirect(url_for('controle_veiculos'))

            observacoes = request.form.get('observacoes')

            proxima_troca_km = None
            proxima_troca_data = None

            if tipo_registro == 'troca_oleo':
                proxima_troca_km = km + 10000
                proxima_troca_data = data_reg + timedelta(days=365)
            
            try:
                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        # Inclui 'usuario_id' na query INSERT
                        cursor.execute("""
                            INSERT INTO controle_veiculos
                            (placa, tipo_registro, data, km, litros, valor, proxima_troca_km, proxima_troca_data, observacoes, usuario_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (placa, tipo_registro, data_reg, km, litros, valor, proxima_troca_km, proxima_troca_data, observacoes, user_id_logado))
                        conn.commit()
                flash('Registro salvo com sucesso!', 'success')
                return redirect(url_for('controle_veiculos'))
            except Exception as e:
                flash(f'Erro ao salvar registro: {e}', 'danger')
                print(f"DEBUG: Erro ao salvar registro: {e}") # DEBUG PRINT
                return redirect(url_for('controle_veiculos'))

        elif acao == 'excluir_registro':
            # Verificação adicional para exclusão:
            # Técnicos só podem excluir os próprios registros de abastecimento
            registro_id = request.form.get('id_registro')
            if registro_id:
                try:
                    registro_id = int(registro_id)
                    with get_connection() as conn:
                        with conn.cursor() as cursor:
                            # NOVO: Para técnico, verifica se o registro pertence a ele e é um abastecimento
                            if user_type == 'tecnico':
                                cursor.execute("SELECT usuario_id, tipo_registro FROM controle_veiculos WHERE id = %s", (registro_id,))
                                reg_owner_info = cursor.fetchone()
                                if reg_owner_info and reg_owner_info[0] == user_id_logado and reg_owner_info[1] == 'abastecimento':
                                    cursor.execute("DELETE FROM controle_veiculos WHERE id = %s", (registro_id,))
                                    conn.commit()
                                    flash('Registro de abastecimento excluído com sucesso!', 'success')
                                else:
                                    flash('Você não tem permissão para excluir este registro ou ele não é um abastecimento seu.', 'danger')
                            else: # Admin pode excluir qualquer um
                                cursor.execute("DELETE FROM controle_veiculos WHERE id = %s", (registro_id,))
                                conn.commit()
                                flash('Registro excluído com sucesso!', 'success')
                except ValueError:
                    flash('ID de registro inválido.', 'danger')
                except Exception as e:
                    flash(f'Erro ao excluir registro: {e}', 'danger')
                    print(f"DEBUG: Erro ao excluir registro: {e}") # DEBUG PRINT
            else:
                flash('ID do registro não fornecido para exclusão.', 'danger')
            return redirect(url_for('controle_veiculos'))

    # --- Início do bloco GET (código que renderiza a página) ---
    filtro_placa = request.args.get('placa_filtro', '').strip()
    filtro_data = request.args.get('data_filtro', '').strip()
    filtro_tipo = request.args.get('tipo_filtro', '').strip() 
    filtro_tecnico = request.args.get('tecnico_filtro', '').strip() 

    registros_com_consumo = []
    consumo_medio_por_placa = {}
    proximas_trocas_oleo = [] 
    
    # --- NOVO: Lógica para obter a lista de técnicos para o SELECT ---
    tecnicos_list = [] # Inicializa a lista de técnicos
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico' ORDER BY usuario")
                # user[0] pega o primeiro elemento da tupla (o nome do usuário)
                tecnicos_list = [user[0] for user in cursor.fetchall()]
                print(f"DEBUG: Lista de técnicos obtida do DB: {tecnicos_list}") # Debug para verificar
    except Exception as e:
        flash(f'Erro ao carregar lista de técnicos: {e}', 'danger')
        print(f"DEBUG: Erro ao carregar lista de técnicos: {e}")
    # --- Fim da nova lógica ---

    # --- INÍCIO: NOVO BLOCO PARA OBTER A LISTA DE PLACAS PARA O SELECT ---
    placas_list = [] # Inicializa a lista de placas
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # Seleciona todas as placas distintas da tabela controle_veiculos
                cursor.execute("SELECT DISTINCT placa FROM controle_veiculos ORDER BY placa")
                placas_list = [row[0] for row in cursor.fetchall()]
                print(f"DEBUG: Lista de placas obtida do DB: {placas_list}") # Debug para verificar
    except Exception as e:
        flash(f'Erro ao carregar lista de placas para filtro: {e}', 'danger')
        print(f"DEBUG: Erro ao carregar lista de placas para filtro: {e}")
    # --- FIM: NOVO BLOCO PARA OBTER A LISTA DE PLACAS PARA O SELECT ---

    try:
        with get_connection() as conn:
            # Primeiro bloco de cursor para a consulta principal de registros
            with conn.cursor() as cursor:
                query = """
                    SELECT 
                        cv.id, 
                        cv.placa, 
                        u.usuario AS tecnico_nome, 
                        cv.tipo_registro, 
                        cv.data, 
                        cv.km, 
                        cv.litros, 
                        cv.valor, 
                        cv.proxima_troca_km, 
                        cv.proxima_troca_data, 
                        cv.observacoes,
                        cv.usuario_id 
                    FROM controle_veiculos cv
                    LEFT JOIN usuarios u ON cv.usuario_id = u.id 
                """
                conditions = []
                params = []

                if user_type == 'tecnico':
                    conditions.append("cv.tipo_registro = 'abastecimento'")
                    conditions.append("cv.usuario_id = %s")
                    params.append(user_id_logado)
                    # O filtro_tecnico para o HTML precisa ser o nome do próprio técnico
                    # Isso garante que o campo read-only seja preenchido corretamente
                    filtro_tecnico = session.get('nome') or session.get('usuario') 
                else: # Admin ou outro tipo de usuário
                    # AQUI O FILTRO PARA PLACA VAI USAR O VALOR DO SELECT
                    if filtro_placa: # Se uma placa foi selecionada no dropdown
                        conditions.append("cv.placa = %s") # Usamos '=' para correspondência exata
                        params.append(filtro_placa) # Não precisa de % para correspondência exata
                    
                    if filtro_data:
                        try:
                            data_obj = datetime.strptime(filtro_data, '%Y-%m-%d').date()
                            conditions.append("cv.data = %s")
                            params.append(data_obj)
                        except ValueError:
                            flash('Formato de data inválido para o filtro.', 'danger')
                    
                    if filtro_tipo:
                        conditions.append("cv.tipo_registro = %s")
                        params.append(filtro_tipo)

                    # AQUI O FILTRO PARA TÉCNICO VAI USAR O VALOR DO SELECT
                    if filtro_tecnico: # Se um técnico foi selecionado no dropdown
                        # Você já tem o nome do técnico em filtro_tecnico
                        # Precisa buscar o ID do técnico pelo nome para filtrar a tabela controle_veiculos
                        # ou usar o nome na condição do JOIN (u.usuario)
                        conditions.append("u.usuario ILIKE %s") # Usamos ILIKE para o nome
                        params.append(f"%{filtro_tecnico}%") # Filtra pelo nome completo
                
                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
                
                query += " ORDER BY cv.data DESC, cv.id DESC"

                print(f"DEBUG: Executando query principal: {query}") # DEBUG PRINT
                print(f"DEBUG: Com parâmetros principais: {params}") # DEBUG PRINT
                cursor.execute(query, tuple(params))
                registros_db = cursor.fetchall() 
                print(f"DEBUG: Resultado da query principal (registros_db): {registros_db}") # DEBUG PRINT

                if registros_db:
                    def format_km(value):
                        if isinstance(value, (int, float)):
                            return f"{value:,.0f}".replace(',', '.')
                        return value

                    registros_para_processar = [list(reg) for reg in registros_db]

                    # Adiciona um placeholder para o consumo (r[12])
                    for reg_list in registros_para_processar:
                        reg_list.append('-') # Este será o r[12] para consumo

                    registros_por_id = {reg[0]: reg for reg in registros_para_processar}

                    # Segundo bloco de cursor para a consulta de abastecimentos
                    with conn.cursor() as cursor_abastecimentos:
                        cursor_abastecimentos.execute("""
                            SELECT id, placa, data, km, litros
                            FROM controle_veiculos
                            WHERE tipo_registro = 'abastecimento'
                            ORDER BY placa, km ASC
                        """)
                        abastecimentos_ordenados = cursor_abastecimentos.fetchall()
                    
                    # Processamento do consumo médio
                    ultimo_km_por_placa = {}
                    for ab in abastecimentos_ordenados:
                        ab_id, ab_placa, ab_data, ab_km, ab_litros = ab

                        if ab_placa in ultimo_km_por_placa:
                            km_anterior = ultimo_km_por_placa[ab_placa]['km']
                            
                            if ab_litros is not None and ab_litros > 0 and ab_km > km_anterior:
                                distancia_percorrida = ab_km - km_anterior
                                consumo_km_litro = distancia_percorrida / ab_litros
                                
                                if ab_id in registros_por_id:
                                    if len(registros_por_id[ab_id]) > 12:
                                        registros_por_id[ab_id][12] = f'{consumo_km_litro:.2f} Km/L'
                                    else:
                                        registros_por_id[ab_id].append(f'{consumo_km_litro:.2f} Km/L')
                                    
                                    if ab_placa not in consumo_medio_por_placa:
                                        consumo_medio_por_placa[ab_placa] = {'total_consumo': 0.0, 'contagem': 0}
                                    consumo_medio_por_placa[ab_placa]['total_consumo'] += consumo_km_litro
                                    consumo_medio_por_placa[ab_placa]['contagem'] += 1
                            else:
                                if ab_id in registros_por_id:
                                    if len(registros_por_id[ab_id]) > 12:
                                        registros_por_id[ab_id][12] = 'N/A'
                                    else:
                                        registros_por_id[ab_id].append('N/A')
                        
                        ultimo_km_por_placa[ab_placa] = {'km': ab_km, 'litros': ab_litros, 'id': ab_id}
                    
                    registros_com_consumo = list(registros_por_id.values())

                    for reg_list in registros_com_consumo:
                        reg_list[5] = format_km(reg_list[5]) # Km atual (r[5])
                        if reg_list[8] is not None:
                            reg_list[8] = format_km(reg_list[8]) # Próxima troca Km (r[8])

                registros_com_consumo.sort(key=lambda x: x[4], reverse=True) # Ordena pela data (r[4])
                print(f"DEBUG: Registros processados para o template (registros_com_consumo): {registros_com_consumo}") # DEBUG PRINT

            # Lógica para calcular o contador de troca de óleo (APENAS PARA ADMIN)
            proximas_trocas_oleo = [] 
            if user_type == 'admin':
                with conn.cursor() as cursor_trocas_oleo:
                    # Obter o KM mais recente para cada placa
                    cursor_trocas_oleo.execute("SELECT placa, MAX(km) FROM controle_veiculos GROUP BY placa")
                    latest_kms_all_vehicles = {row[0]: row[1] for row in cursor_trocas_oleo.fetchall()}
                    print(f"DEBUG: Últimos KMs por veículo: {latest_kms_all_vehicles}") # DEBUG PRINT

                    # Obter a última 'proxima_troca_km' para cada placa
                    query_proxima_troca = """
                        SELECT
                            cv.placa,
                            cv.proxima_troca_km
                        FROM controle_veiculos cv
                        WHERE cv.id IN (
                            SELECT MAX(id)
                            FROM controle_veiculos
                            WHERE tipo_registro = 'troca_oleo'
                            GROUP BY placa
                        ) AND cv.tipo_registro = 'troca_oleo'
                    """
                    cursor_trocas_oleo.execute(query_proxima_troca)
                    proximas_trocas_db = cursor_trocas_oleo.fetchall()
                    print(f"DEBUG: Próximas trocas de óleo do DB: {proximas_trocas_db}") # DEBUG PRINT

                    temp_proxima_troca_km = {}
                    for row in proximas_trocas_db:
                        placa, proxima_km = row
                        if proxima_km is not None:
                            temp_proxima_troca_km[placa] = proxima_km
                    
                    print(f"DEBUG: Proximas_trocas_km mapeado: {temp_proxima_troca_km}") # DEBUG PRINT

                    for placa, latest_km_val in latest_kms_all_vehicles.items():
                        if placa in temp_proxima_troca_km:
                            km_proxima_troca = temp_proxima_troca_km[placa]
                            if latest_km_val is not None and km_proxima_troca is not None:
                                km_restantes = km_proxima_troca - latest_km_val
                                proximas_trocas_oleo.append({
                                    'placa': placa,
                                    'km_restantes': f"{km_restantes:,.0f}".replace(',', '.') if km_restantes > 0 else 'VENCIDO'
                                })
                    proximas_trocas_oleo.sort(key=lambda x: int(x['km_restantes'].replace('.', '')) if isinstance(x['km_restantes'], str) and x['km_restantes'].replace('.', '').isdigit() else float('inf'))
                    print(f"DEBUG: Lista final de próximas trocas: {proximas_trocas_oleo}") # DEBUG PRINT

    except Exception as e:
        flash(f'Erro ao carregar registros: {e}', 'danger')
        print(f"DEBUG: Erro geral no bloco GET: {e}") # DEBUG PRINT
        registros_com_consumo = []
        consumo_medio_por_placa = {} # Adicionado para garantir que esteja vazia em caso de erro
        proximas_trocas_oleo = []

    consumos_finais_por_placa = {}
    for placa, dados in consumo_medio_por_placa.items():
        if dados['contagem'] > 0:
            media = dados['total_consumo'] / dados['contagem']
            consumos_finais_por_placa[placa] = f'{media:.2f} Km/L'
        else:
            consumos_finais_por_placa[placa] = 'N/A'

    # --- Renderiza o template com todas as variáveis necessárias ---
    return render_template('controle_veiculos.html',
                            registros=registros_com_consumo,
                            consumo_por_placa=consumos_finais_por_placa,
                            filtro_placa=filtro_placa,
                            filtro_data=filtro_data,
                            filtro_tipo=filtro_tipo,
                            filtro_tecnico=filtro_tecnico,
                            user_type=user_type, 
                            user_id_logado=user_id_logado,
                            proximas_trocas_oleo=proximas_trocas_oleo,
                            tecnicos=tecnicos_list,
                            # --- NOVO: Passe a lista de placas para o template ---
                            placas=placas_list 
                            )
from fpdf import FPDF
from io import BytesIO
from datetime import datetime, date # Ensure date is imported
# Assume get_connection, DictCursor, etc. are also imported as needed
# from psycopg2.extras import DictCursor # Example import if not already there

class PDF(FPDF):
    def header(self):
        try:
            self.image('static/Geramaster logo Preto fundo transparente.png', 10, 8, 33)
        except Exception as e:
            print(f"DEBUG: Não foi possível carregar o logo: {e}. Certifique-se que o caminho 'static/Geramaster logo Preto fundo transparente.png' está correto.")
            pass

        self.set_font('Arial', 'B', 15)
        self.cell(80)
        self.cell(30, 10, 'Relatório de RDV', 0, 1, 'C')
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Página {self.page_no()}/{{nb}}', 0, 0, 'C')

    def chapter_body(self, data):
        self.set_font('Arial', '', 10)
        self.set_fill_color(220, 220, 220)

        # Headers atualizados: REMOVIDO 'Técnico'
        headers = ['Data', 'Descrição', 'Valor (R$)']

        # Larguras de coluna ajustadas: A largura que era do 'Técnico' (60mm) foi para 'Descrição'
        # Data(25) + Descrição(70+60=130) + Valor(35) = 190mm
        col_widths = [25, 130, 25] 

        # Desenha o cabeçalho da tabela
        self.set_line_width(0.2)
        self.set_draw_color(0, 0, 0) # Cor da borda da célula (preto)
        for i, header in enumerate(headers):
            self.cell(col_widths[i], 7, header, 1, 0, 'C', 1)
        self.ln()
        self.set_fill_color(255, 255, 255) # Cor de fundo das linhas de dados

        for item in data: # Cada 'item' é um dicionário (ex: {'data': ..., 'valor': ..., 'descricao': ...})
            # Acessando os dados pelas CHAVES do dicionário (pois você usa DictCursor)
            rdv_data = item['data']
            rdv_valor = item['valor']
            rdv_descricao = item['descricao']
            # nome_tecnico_username = item['nome_tecnico_username'] # Não mais necessário na tabela
            # nome_completo_tecnico = item['nome_completo_tecnico'] # Não mais necessário na tabela

            data_formatada = rdv_data.strftime('%d/%m/%Y') if isinstance(rdv_data, date) else ''
            valor_float = float(rdv_valor)
            valor_formatado = f"R$ {valor_float:.2f}".replace('.', ',')
            
            # O nome do técnico não será mais usado na tabela, pois já está no topo.
            # nome_tecnico = nome_completo_tecnico if nome_completo_tecnico else nome_tecnico_username 

            descricao_limpa = rdv_descricao.replace('\u2070', '') if isinstance(rdv_descricao, str) else ''

            # --- CÁLCULO MANUAL DA ALTURA DA CÉLULA DE DESCRIÇÃO ---
            # effective_width agora é a largura da coluna "Descrição" (col_widths[1])
            effective_width = col_widths[1] - 2 * self.c_margin 
            
            # Simula a multi_cell para obter o número de linhas sem desenhar
            try:
                # Tenta usar multi_cell com dry_run para obter o número de linhas (fpdf2)
                # O último parâmetro 'True' é para dry_run, ou seja, só calcula, não desenha.
                lines = self.multi_cell(effective_width, self.font_size, descricao_limpa, 0, 'L', False, True)
                num_lines = len(lines) if isinstance(lines, list) else 1
            except TypeError:
                # Fallback para fpdf mais antigo ou se dry_run não funciona como esperado
                # Isso é uma estimativa mais simples
                text_width = self.get_string_width(descricao_limpa)
                num_lines = int(text_width / effective_width) + 1 if effective_width > 0 else 1
            
            desc_height = max(7, num_lines * self.font_size * 1.2) # Altura mínima de 7mm, com espaçamento

            # Salva posições X e Y para alinhar células horizontalmente
            x_pos_start = self.get_x()
            y_pos_start = self.get_y()

            # Célula de Data (primeira coluna)
            self.cell(col_widths[0], desc_height, data_formatada, 1, 0, 'C', 0)

            # Célula de Descrição (segunda coluna - usa multi_cell)
            # Posiciona o cursor para o início da célula de Descrição
            # x_pos_start + col_widths[0] é o X após a coluna de Data
            self.set_xy(x_pos_start + col_widths[0], y_pos_start) 
            
            # Para multi_cell, o segundo parâmetro é a altura de CADA LINHA.
            # Divida a altura total calculada pela quantidade de linhas.
            line_height_for_multi = desc_height / num_lines if num_lines > 0 else 7 # Evita divisão por zero
            self.multi_cell(col_widths[1], line_height_for_multi, descricao_limpa, 1, 'L', 0)
            
            # Célula de Valor (terceira coluna)
            # Posiciona o cursor para o início da célula de Valor
            # x_pos_start + col_widths[0] + col_widths[1] é o X após a coluna de Descrição
            self.set_xy(x_pos_start + col_widths[0] + col_widths[1], y_pos_start)
            self.cell(col_widths[2], desc_height, valor_formatado, 1, 0, 'R', 0)

            # Pula para a próxima linha baseada na altura da célula mais alta da linha.
            self.ln(desc_height)

        self.ln(10) # Espaçamento final após a tabela

    # NOVO MÉTODO PARA ADICIONAR O TOTAL
    def add_total_summary(self, total_value):
        self.ln(5) # Pula 5mm para dar um pequeno espaço depois da tabela
        self.set_font('Arial', 'B', 12) # Define fonte em negrito e tamanho 12
        
        # Formata o valor total para R$ XX,YY e alinha à direita
        total_formatado = f"R$ {total_value:.2f}".replace('.', ',')
        
        # Imprime o valor total. 'R' para alinhar à direita.
        self.cell(0, 10, f'VALOR TOTAL: {total_formatado}', 0, 1, 'R')
        self.ln(10) # Pula 10mm após o total para espaçamento extra

    def add_signature_field(self, technician_name):
        self.ln(20)
        self.set_font('Arial', '', 10)
        self.cell(0, 10, 'Assinatura do Técnico: _________________________________________', 0, 1)
        self.ln(10)
            

# ... (Sua função get_and_increment_pdf_counter) ...
def get_and_increment_pdf_counter(counter_key): # O parâmetro agora é 'counter_key'
    current_year = datetime.now().year
    current_month = datetime.now().month
    
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # Tenta buscar o contador existente para a CHAVE ESPECÍFICA (ex: 'rdv_henrique_07-2025')
            cursor.execute("""
                SELECT last_number, last_year, last_month FROM pdf_counters
                WHERE type = %s;
            """, (counter_key,)) # Usa counter_key aqui
            counter_data = cursor.fetchone()

            new_number = 1 
            # O mês e ano a serem exibidos no PDF serão os do período do contador
            display_month = current_month
            display_year = current_year

            if counter_data:
                last_number, last_year, last_month = counter_data
                # Se for o mesmo mês e ano para esta CHAVE, incrementa o contador
                if last_year == current_year and last_month == current_month:
                    new_number = last_number + 1
                # Se for um novo mês/ano para esta CHAVE, o new_number continua sendo 1 (resetado)
                # display_month e display_year já são os 'current'
            
            # Upsert (INSERT OR UPDATE) o contador para esta CHAVE
            cursor.execute("""
                INSERT INTO pdf_counters (type, last_number, last_year, last_month)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (type) DO UPDATE SET
                    last_number = EXCLUDED.last_number,
                    last_year = EXCLUDED.last_year,
                    last_month = EXCLUDED.last_month;
            """, (counter_key, new_number, current_year, current_month)) # Usa counter_key aqui
            conn.commit()
            
            # Retorna o novo número E o mês/ano para a exibição no PDF
            return new_number, display_month, display_year
@app.route('/exportar_rdv_pdf', methods=['GET'])
def exportar_rdv_pdf():
    if 'usuario' not in session or session.get('tipo') != 'admin':
        flash('Você não tem permissão para exportar relatórios de RDV.', 'danger')
        return redirect(url_for('login'))

    print(f"DEBUG FLASK - request.args COMPLETO: {request.args}")

    filtro_data_unica_str = request.args.get('filtro_data', '').strip()
    filtro_data_inicio_str = request.args.get('filtro_data_inicio', '').strip()
    filtro_data_fim_str = request.args.get('filtro_data_fim', '').strip()
    filtro_tecnico_username = request.args.get('filtro_usuario', '').strip()

    print(f"DEBUG FLASK - VALORES RAW: data_unica='{request.args.get('filtro_data')}', data_inicio='{request.args.get('filtro_data_inicio')}', data_fim='{request.args.get('filtro_data_fim')}', tecnico='{request.args.get('filtro_usuario')}'")
    print(f"DEBUG FLASK: Filtros recebidos - Data Unica: '{filtro_data_unica_str}', Inicio: '{filtro_data_inicio_str}', Fim: '{filtro_data_fim_str}', Tecnico: '{filtro_tecnico_username}'")

    registros_rdv = []
    nome_tecnico_para_pdf = "Todos os Técnicos" # Padrão para admin

    try:
        with get_connection() as conn:
            # Mantém DictCursor aqui para facilitar o acesso aos dados por nome
            with conn.cursor(cursor_factory=DictCursor) as cursor: 
                query = """
                        SELECT
                            rdv.data,
                            rdv.valor,
                            rdv.descricao,
                            u.usuario AS nome_tecnico_username,
                            u.nome AS nome_completo_tecnico,
                            rdv.usuario
                        FROM rdvs rdv
                        LEFT JOIN usuarios u ON rdv.usuario = u.usuario
                    """
                conditions = []
                params = []

                if filtro_tecnico_username:
                    conditions.append("u.usuario = %s")
                    params.append(filtro_tecnico_username)

                    cursor.execute("SELECT nome FROM usuarios WHERE usuario = %s", (filtro_tecnico_username,))
                    resultado_nome = cursor.fetchone()
                    if resultado_nome:
                        nome_tecnico_para_pdf = resultado_nome['nome'] 
                    else:
                        nome_tecnico_para_pdf = filtro_tecnico_username

                if filtro_data_inicio_str and filtro_data_fim_str:
                    try:
                        data_inicio_obj = datetime.strptime(filtro_data_inicio_str, '%Y-%m-%d').date()
                        data_fim_obj = datetime.strptime(filtro_data_fim_str, '%Y-%m-%d').date()
                        conditions.append("rdv.data BETWEEN %s AND %s")
                        params.append(data_inicio_obj)
                        params.append(data_fim_obj)
                    except ValueError:
                        flash('Formato de data de início/fim inválido.', 'danger')
                        return redirect(url_for('rdv'))
                elif filtro_data_unica_str:
                    try:
                        data_obj = datetime.strptime(filtro_data_unica_str, '%Y-%m-%d').date()
                        conditions.append("rdv.data = %s")
                        params.append(data_obj)
                    except ValueError:
                        flash('Formato de data única inválido.', 'danger')
                        return redirect(url_for('rdv'))

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
                query += " ORDER BY rdv.data ASC"

                print(f"DEBUG FLASK: Query SQL final: {query}")
                print(f"DEBUG FLASK: Parâmetros da Query: {params}")

                cursor.execute(query, tuple(params))
                registros_rdv = cursor.fetchall()

    except Exception as e:
        flash(f'Erro ao gerar PDF de RDV: {e}', 'danger')
        print(f"DEBUG FLASK: Erro ao gerar PDF de RDV: {e}")
        return redirect(url_for('rdv'))
    
    # CALCULAR O VALOR TOTAL DOS RDVs AQUI
    total_valor_rdv = 0.0
    print(f"DEBUG: Iniciando cálculo de total_valor_rdv. Registros: {len(registros_rdv) if registros_rdv else 0}")
    if registros_rdv:
        for i, item in enumerate(registros_rdv):
            try:
                # Com DictCursor ativo, acessamos por item['nome_da_coluna']
                valor_float = float(item['valor']) 
                total_valor_rdv += valor_float
                print(f"DEBUG: Item {i}: Valor '{item['valor']}' (float: {valor_float}). Total parcial: {total_valor_rdv:.2f}")
            except (ValueError, TypeError) as e:
                print(f"DEBUG: Erro ao converter valor '{item.get('valor', 'N/A')}' para float no item {i}. Pulando item. Erro: {e}")
                pass 
    
    print(f"DEBUG: Total valor RDV calculado FINAL: R$ {total_valor_rdv:.2f}")


    # DEBUG para ver a estrutura dos dados retornados:
    print(f"DEBUG PDF: Registros_rdv para PDF (APÓS CONSULTA): {registros_rdv}")

    # --- NOVO: Lógica para gerar o número do relatório por técnico/mês/ano ---
    current_month_year_str_for_key = datetime.now().strftime('%m-%Y') # Ex: "07-2025"

    pdf_number_display = "N/A" # Valor padrão se não for gerado um número sequencial
    report_filename_number_part = "" # Parte do nome do arquivo (sem barras)

    # Determine a chave do contador e o formato de exibição
    if filtro_tecnico_username:
        # Se um técnico específico foi filtrado, usamos ele na chave do contador
        counter_key = f"rdv_{filtro_tecnico_username}_{current_month_year_str_for_key}"
        
        # Chama a função para obter o número e o mês/ano da contagem
        report_num, count_month, count_year = get_and_increment_pdf_counter(counter_key)
        
        # Formata o número do relatório para exibição (ex: "1/07-2025")
        formatted_month = str(count_month).zfill(2) # Garante dois dígitos para o mês (ex: 07)
        pdf_number_display = f"{report_num}/{formatted_month}-{count_year}"
        report_filename_number_part = f"{report_num}_{formatted_month}-{count_year}" # Para o nome do arquivo

    else:
        # Se nenhum técnico específico foi filtrado ("Todos os Técnicos"),
        # o número do relatório será "N/A" no PDF.
        # O nome do arquivo pode ter uma marcação 'GLOBAL' ou similar.
        pdf_number_display = "N/A"
        report_filename_number_part = f"GLOBAL_{current_month_year_str_for_key}"
        # Se você QUISER um contador global para "Todos os Técnicos", e não "N/A":
        # counter_key = f"rdv_global_{current_month_year_str_for_key}"
        # report_num, count_month, count_year = get_and_increment_pdf_counter(counter_key)
        # formatted_month = str(count_month).zfill(2)
        # pdf_number_display = f"{report_num}/{formatted_month}-{count_year}"
        # report_filename_number_part = f"{report_num}_{formatted_month}-{count_year}_GLOBAL"
    
    print(f"DEBUG: Número de PDF gerado para exibição: {pdf_number_display}")

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()

    # Usando pdf_number_display no cabeçalho do PDF
    pdf.set_font('Arial', 'B', 15) # O tamanho da fonte aqui parece ter sido 15 antes
    pdf.cell(0, 10, f'Relatório # {pdf_number_display}', 0, 1, 'L')
    pdf.set_font('Arial', 'B', 12) # Volta para o tamanho padrão para as próximas células
    pdf.cell(0, 10, f'Técnico: {nome_tecnico_para_pdf}', 0, 1, 'L')
    
    if filtro_data_inicio_str and filtro_data_fim_str:
        pdf.cell(0, 10, f'Período: {datetime.strptime(filtro_data_inicio_str, "%Y-%m-%d").strftime("%d/%m/%Y")} a {datetime.strptime(filtro_data_fim_str, "%Y-%m-%d").strftime("%d/%m/%Y")}', 0, 1, 'L')
    elif filtro_data_unica_str:
        pdf.cell(0, 10, f'Data Específica: {datetime.strptime(filtro_data_unica_str, "%Y-%m-%d").strftime("%d/%m/%Y")}', 0, 1, 'L')
    
    pdf.ln(5)

    if registros_rdv:
        print(f"DEBUG: Número de registros encontrados em 'registros_rdv': {len(registros_rdv)}")
        # AQUI CHAMAMOS chapter_body!
        pdf.chapter_body(registros_rdv) 
        
        # CHAMA O NOVO MÉTODO PARA ADICIONAR O TOTAL
        print(f"DEBUG: Chamando pdf.add_total_summary com valor: {total_valor_rdv:.2f}")
        pdf.add_total_summary(total_valor_rdv)

    else:
        pdf.set_font('Arial', 'I', 12)
        pdf.cell(0, 10, 'Nenhum registro de RDV encontrado para os filtros selecionados.', 0, 1, 'C')

    # A lógica para nome_para_assinatura ajustada para usar chaves do dicionário (DictCursor)
    if filtro_tecnico_username:
        nome_para_assinatura = ""
        # Buscar o nome completo na lista de registros_rdv (que são dicionários se DictCursor estiver ativo)
        for r in registros_rdv:
            # r['usuario'] é o nome de usuário do técnico, r['nome_completo_tecnico'] é o nome completo
            if r['usuario'] == filtro_tecnico_username:
                nome_para_assinatura = r['nome_completo_tecnico']
                break # Encontrou, pode sair do loop
        if not nome_para_assinatura: # Fallback se não achou nos registros (raro, mas possível se o filtro não retornou dados)
            nome_para_assinatura = nome_tecnico_para_pdf
    else:
        nome_para_assinatura = ""

    pdf.add_signature_field(nome_para_assinatura)

    # O nome do arquivo agora usa 'report_filename_number_part'
    filename = f'RDV_{report_filename_number_part}.pdf'

    pdf_output = BytesIO(pdf.output(dest='S'))
    return send_file(pdf_output, download_name=filename, as_attachment=True, mimetype='application/pdf')


# --- NOVA ROTA: GERAR PROPOSTA NO BLING A PARTIR DE UMA PENDÊNCIA ---
@app.route('/gerar_proposta_bling/<int:pendencia_id>', methods=['POST'])
def gerar_proposta_bling(pendencia_id):
    if 'usuario' not in session:
        flash("Você precisa estar logado para gerar propostas.", "danger")
        return redirect(url_for('login'))
    
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
            cursor.execute("SELECT * FROM pendencias_os WHERE id = %s", (pendencia_id,))
            pendencia = cursor.fetchone()

    if not pendencia:
        flash("Pendência não encontrada.", "danger")
        return redirect(url_for('pendencias'))

    # --- 1. Obter produtos selecionados do frontend ---
    # Assume que o frontend enviará um payload JSON.
    try:
        dados_recebidos = request.get_json()
    except Exception as e:
        print(f"Erro ao parsear JSON do request: {e}")
        flash("Erro ao processar dados da proposta. Formato inválido.", "danger")
        return redirect(url_for('pendencias'))

    produtos_selecionados_frontend = dados_recebidos.get('produtos_selecionados', [])
    
    if not produtos_selecionados_frontend and not pendencia['pecas_necessarias']: # Se não vier nada do front e não tiver peça na pendência
        flash("Nenhum produto ou serviço foi selecionado para a proposta.", "warning")
        return redirect(url_for('pendencias'))

    # Constantes para o SERV1 (IDs e preços reais que você confirmou)
    ID_REAL_SERV1 = 16060442102 
    PRECO_REAL_SERV1 = 421.89
    FORMA_PAGAMENTO_PADRAO_ID = 397375 

    # --- 2. Buscar Cliente no Bling ---
    cliente_bling_id = None
    contato_obj_completo = None 
    cliente_nome_pendencia = pendencia['cliente']
    
    print(f"Buscando cliente '{cliente_nome_pendencia}' no Bling...")
    
    clientes_encontrados_bling_simples = bling_api_call(
        'contatos', 
        method='GET', 
        data={'filters': f'nome[equals][{cliente_nome_pendencia}]'}
    )
    
    if clientes_encontrados_bling_simples and isinstance(clientes_encontrados_bling_simples, dict) and 'data' in clientes_encontrados_bling_simples:
        for item_data in clientes_encontrados_bling_simples['data']:
            current_contato = item_data.get('contato') if 'contato' in item_data and isinstance(item_data['contato'], dict) else item_data
            if current_contato and current_contato.get('nome', '').upper() == cliente_nome_pendencia.upper():
                cliente_bling_id = current_contato.get('id')
                print(f"Cliente '{cliente_nome_pendencia}' encontrado no Bling com ID: {cliente_bling_id} (correspondência exata).")
                break 
    
    if not cliente_bling_id:
        print(f"Cliente '{cliente_nome_pendencia}' NÃO encontrado via 'equals'. Tentando busca por 'contem'.")
        clientes_contem_bling_simples = bling_api_call(
            'contatos', 
            method='GET', 
            data={'filters': f'nome[contem][{cliente_nome_pendencia}]'}
        )
        if clientes_contem_bling_simples and isinstance(clientes_contem_bling_simples, dict) and 'data' in clientes_contem_bling_simples:
            for item_data_contem in clientes_contem_bling_simples['data']:
                current_contato_contem = item_data_contem.get('contato') if 'contato' in item_data_contem and isinstance(item_data_contem['contato'], dict) else item_data_contem
                if current_contato_contem and current_contato_contem.get('nome', '').upper() == cliente_nome_pendencia.upper():
                    cliente_bling_id = current_contato_contem.get('id')
                    print(f"Cliente '{cliente_nome_pendencia}' encontrado no Bling via 'contem' com ID: {cliente_bling_id}.")
                    break 
        
    if not cliente_bling_id:
        print(f"Cliente '{cliente_nome_pendencia}' NÃO encontrado no Bling após todas as tentativas de busca.")
        flash(f"Cliente '{cliente_nome_pendencia}' não encontrado no Bling. Por favor, cadastre o cliente no Bling primeiro com o nome EXATO.", "danger")
        return redirect(url_for('pendencias'))

    print(f"Buscando detalhes completos do contato Bling com ID: {cliente_bling_id}")
    detalhes_contato_bling = bling_api_call(f'contatos/{cliente_bling_id}', method='GET')
    
    if detalhes_contato_bling and isinstance(detalhes_contato_bling, dict) and 'data' in detalhes_contato_bling:
        contato_obj_completo = detalhes_contato_bling['data']
        print(f"Detalhes completos do contato Bling obtidos: {contato_obj_completo}")
    else:
        print(f"AVISO: Não foi possível obter detalhes completos do contato Bling com ID {cliente_bling_id}. Falhando na criação da proposta.")
        flash(f"Erro ao obter detalhes completos do cliente '{cliente_nome_pendencia}' no Bling. Não foi possível gerar a proposta.", "danger")
        return redirect(url_for('pendencias'))

    # --- DADOS DO CONTATO PARA O PAYLOAD DA PROPOSTA COMERCIAL (COMPLETO) ---
    contato_para_proposta_payload = {
        "id": contato_obj_completo.get('id'),
        "nome": contato_obj_completo.get('nome', cliente_nome_pendencia),
        "codigo": contato_obj_completo.get('codigo', ''),
        "situacao": contato_obj_completo.get('situacao', 'A'), 
        "numeroDocumento": contato_obj_completo.get('numeroDocumento', ''),
        "telefone": contato_obj_completo.get('telefone', ''),
        "celular": contato_obj_completo.get('celular', ''),
        "fantasia": contato_obj_completo.get('fantasia', ''),
        "tipo": contato_obj_completo.get('tipo', 'J'), 
        "indicadorIe": contato_obj_completo.get('indicadorIe', 1), 
        "ie": contato_obj_completo.get('ie', ''),
        "rg": contato_obj_completo.get('rg', ''),
        "inscricaoMunicipal": contato_obj_completo.get('inscricaoMunicipal', ''),
        "orgaoEmissor": contato_obj_completo.get('orgaoEmissor', ''),
        "email": contato_obj_completo.get('email') or 'contato@email.com', # Fallback para um email padrão
        "endereco": {
            "geral": contato_obj_completo.get('endereco', {}).get('geral', {
                'endereco': '', 'cep': '', 'bairro': '', 'municipio': '', 'uf': '', 'numero': '', 'complemento': ''
            }),
            "cobranca": contato_obj_completo.get('endereco', {}).get('cobranca', {
                'endereco': '', 'cep': '', 'bairro': '', 'municipio': '', 'uf': '', 'numero': '', 'complemento': ''
            })
        },
        "dadosAdicionais": contato_obj_completo.get('dadosAdicionais', {
            'dataNascimento': '0000-00-00', 'sexo': '', 'naturalidade': ''
        }),
        "financeiro": contato_obj_completo.get('financeiro', {
            'limiteCredito': 0, 'condicaoPagamento': '', 'categoria': {'id': 0}
        }),
        "pais": contato_obj_completo.get('pais', {'nome': ''}),
        "tiposContato": contato_obj_completo.get('tiposContato', []),
        "pessoasContato": contato_obj_completo.get('pessoasContato', [])
    }

    # --- 3. Preparar Itens da Proposta Comercial ---
    itens_proposta = []
    total_itens_calculado = 0.0 

    # Adicionar o serviço principal (SERV1) automaticamente
    print(f"Adicionando 'SERV1' por padrão com ID {ID_REAL_SERV1} e preço {PRECO_REAL_SERV1}.")
    item_serv1_payload = { 
        "produto": {
            "id": ID_REAL_SERV1, 
            "descricao": pendencia['pendencia'] # Usar descrição da pendência como descrição do serviço
        },
        "codigo": "SERV1", 
        "unidade": "UN",
        "quantidade": 1,
        "valor": PRECO_REAL_SERV1, 
        "desconto": 0.0 
    }
    itens_proposta.append(item_serv1_payload)
    total_itens_calculado += PRECO_REAL_SERV1 

    # Adicionar os produtos selecionados pelo usuário no frontend
    for produto_selecionado in produtos_selecionados_frontend:
        produto_id = produto_selecionado.get('id')
        quantidade = produto_selecionado.get('quantidade', 1)
        preco_item = produto_selecionado.get('preco') # Vem do frontend

        if produto_id and quantidade > 0 and preco_item is not None:
            # Buscar detalhes completos do produto no Bling para ter certeza do nome/descrição e preço atualizado
            print(f"Buscando detalhes do produto Bling com ID: {produto_id}")
            detalhes_produto_bling = bling_api_call(f'produtos/{produto_id}', method='GET')
            
            product_obj_completo = None
            if detalhes_produto_bling and isinstance(detalhes_produto_bling, dict) and 'data' in detalhes_produto_bling:
                product_obj_completo = detalhes_produto_bling['data']
            
            if product_obj_completo and product_obj_completo.get('id') == produto_id:
                # Usar o preço de venda atualizado do Bling, se disponível
                bling_preco_venda = float(product_obj_completo.get('preco', {}).get('venda', preco_item)) if isinstance(product_obj_completo.get('preco'), dict) else float(product_obj_completo.get('preco', preco_item))
                
                item_payload = {
                    "produto": { 
                        "id": produto_id, 
                        "descricao": product_obj_completo.get('descricao', produto_selecionado.get('nome', ''))
                    },
                    "codigo": product_obj_completo.get('codigo', produto_selecionado.get('sku', '')), 
                    "unidade": product_obj_completo.get('unidade', 'UN'),
                    "quantidade": quantidade, 
                    "valor": bling_preco_venda,
                    "desconto": 0.0
                }
                itens_proposta.append(item_payload)
                total_itens_calculado += (bling_preco_venda * quantidade)
                print(f"Produto '{item_payload['produto']['descricao']}' (ID: {produto_id}) adicionado da seleção do frontend.")
            else:
                print(f"AVISO: Não foi possível obter detalhes atualizados do produto ID {produto_id} do Bling. Adicionando com dados do frontend/padrão.")
                # Fallback se não conseguir os detalhes atualizados (confia nos dados do frontend)
                itens_proposta.append({
                    "produto": { 
                        "id": produto_id, 
                        "descricao": produto_selecionado.get('nome', f"Produto ID: {produto_id}")
                    },
                    "codigo": produto_selecionado.get('sku', ''), 
                    "unidade": produto_selecionado.get('unidade', 'UN'), # Usar unidade do frontend
                    "quantidade": quantidade, 
                    "valor": preco_item,
                    "desconto": 0.0
                })
                total_itens_calculado += (preco_item * quantidade)
        else:
            print(f"AVISO: Produto selecionado do frontend inválido ou incompleto: {produto_selecionado}")

    if not itens_proposta: # Com o SERV1 automático, isso não deve ocorrer, mas é uma segurança
        flash("Erro interno: Nenhum item (serviço ou peça) foi adicionado à proposta.", "danger")
        return redirect(url_for('pendencias'))

    # --- 4. Montar Payload Final para a Proposta Comercial ---
    proposta_data_bling = {
        "data": datetime.now().strftime('%Y-%m-%d'),
        "situacao": "Em aberto", 
        "numero": pendencia['numero_os'],
        "contato": contato_para_proposta_payload, 
        "loja": { 
            "id": 0 
        },
        "desconto": 0, 
        "outrasDespesas": 0, 
        "garantia": 0, 
        "dataProximoContato": (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d'), 
        "observacoes": f"Proposta gerada para OS: {pendencia['numero_os']}. Descrição da Pendência: {pendencia['pendencia']}. Técnico: {session.get('nome', 'N/A')}",
        "observacaoInterna": f"Proposta Comercial gerada automaticamente pelo sistema para OS {pendencia['numero_os']}.",
        "introducao": "Prezado(a) Cliente, segue nossa proposta para o serviço/produto solicitado.",
        "prazoEntrega": "A combinar",
        "itens": itens_proposta,
        "parcelas": [ 
            {
                "numeroDias": 0, 
                "dataVencimento": datetime.now().strftime('%Y-%m-%d'),
                "valor": total_itens_calculado, 
                "observacoes": "Pagamento à vista",
                "formaPagamento": {
                    "id": FORMA_PAGAMENTO_PADRAO_ID 
                }
            }
        ],
        "vendedor": {    
            "id": 0 
        },  
        "transporte": {
            "freteModalidade": 0, 
            "frete": 0.0,
            "volumes": {} 
        }
    }

    print(f"\n--- Dados da Proposta Bling a serem enviados ---\n{json.dumps(proposta_data_bling, indent=2)}\n------------------------------------------------")
    
    # --- 5. Enviar para a API do Bling (endpoint 'propostas-comerciais') ---
    response_bling = bling_api_call(
        'propostas-comerciais', 
        method='POST', 
        data={'propostaComercial': proposta_data_bling} 
    )

    if response_bling and 'data' in response_bling and response_bling['data'] and response_bling['data'][0] and response_bling['data'][0].get('id'):
        proposta_retornada = response_bling['data'][0]
        bling_proposta_id = proposta_retornada.get('id')
        numero_proposta_bling = proposta_retornada.get('numero') 
        
        valor_total_bling = float(proposta_retornada.get('total', proposta_retornada.get('valorTotal', 0.0)))
        
        situacao_id_bling = proposta_retornada.get('situacao', {}).get('id') 
        situacao_descricao_bling = proposta_retornada.get('situacao', {}).get('descricao')

        try:
            with get_connection() as conn_db: 
                with conn_db.cursor() as cursor_db:
                    cursor_db.execute(
                        '''
                        INSERT INTO propostas_bling_local (
                            bling_id, numero_proposta, data_proposta, cliente_nome, 
                            cliente_bling_id, valor_total, situacao_id, situacao_descricao, 
                            observacoes, data_ultima_atualizacao
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (bling_id) DO UPDATE SET
                            numero_proposta = EXCLUDED.numero_proposta,
                            data_proposta = EXCLUDED.data_proposta,
                            cliente_nome = EXCLUDED.cliente_nome,
                            cliente_bling_id = EXCLUDED.cliente_bling_id,
                            valor_total = EXCLUDED.valor_total,
                            situacao_id = EXCLUDED.situacao_id,
                            situacao_descricao = EXCLUDED.situacao_descricao,
                            observacoes = EXCLUDED.observacoes,
                            data_ultima_atualizacao = NOW()
                        ''',
                        (
                            bling_proposta_id,
                            numero_proposta_bling,
                            proposta_retornada.get('data'), 
                            proposta_retornada.get('contato', {}).get('nome'), 
                            proposta_retornada.get('contato', {}).get('id'), 
                            valor_total_bling,
                            situacao_id_bling, 
                            situacao_descricao_bling,
                            proposta_retornada.get('observacoes')
                        )
                    )
                    conn_db.commit()
                    print(f"Proposta Comercial Bling {bling_proposta_id} (Número: {numero_proposta_bling}) salva/atualizada na tabela local 'propostas_bling_local'.")
            
            with get_connection() as conn_main: 
                with conn_main.cursor() as cursor_main:
                    cursor_main.execute(
                        '''
                        UPDATE pendencias_os
                        SET bling_proposta_id = %s, status_proposta_bling = %s
                        WHERE id = %s
                        ''',
                        (bling_proposta_id, situacao_descricao_bling, pendencia_id)
                    )
                    conn_main.commit()

        except Exception as db_err:
            print(f"Erro ao salvar proposta Bling na tabela local: {db_err}")
            flash(f"Proposta Comercial criada no Bling, mas houve um erro ao salvar localmente: {db_err}", "warning")

        flash(f"Proposta Comercial Bling criada com sucesso para OS {pendencia['numero_os']} (ID Bling: {bling_proposta_id})!", "success")
        return redirect(url_for('pendencias'))
    else:
        error_message = "Falha ao gerar Proposta Comercial no Bling."
        if response_bling and response_bling.get('error'):
            error_details = response_bling['error'].get('description', response_bling['error'].get('message', ''))
            if response_bling['error'].get('fields'):
                field_errors = []
                for field in response_bling['error']['fields']:
                    field_errors.append(f"Campo '{field.get('element')}': {field.get('msg')} (código: {field.get('code')})")
                error_details += " Erros de campo: " + "; ".join(field_errors)
            error_message += f" Detalhes da API: {error_details}"
        else:
            error_message += " Resposta inesperada da API do Bling ou erro de conexão."
            
        print(f"\n--- Erro ao criar Proposta Comercial Bling ---\nResposta da API: {response_bling}\n------------------------------------")
        flash(error_message, "danger")
        return redirect(url_for('pendencias'))
    

    
@app.route('/buscar_produtos_bling', methods=['GET'])
def buscar_produtos_bling():
    if 'usuario' not in session:
        return jsonify({"error": "Não autorizado"}), 401
    
    termo_busca = request.args.get('termo', '').strip()
    if not termo_busca:
        return jsonify([]), 200 # Retorna lista vazia se não houver termo de busca

    print(f"Buscando produtos no Bling com o termo: '{termo_busca}'")
    
    # Tenta buscar por código exato primeiro (prioridade para SKUs)
    produtos_data = bling_api_call('produtos', method='GET', data={'filters': f'codigo[equals][{termo_busca}]'})
    
    produtos_encontrados = []
    
    if produtos_data and isinstance(produtos_data, dict) and 'data' in produtos_data:
        for item_data in produtos_data['data']:
            product_obj = None
            if 'produto' in item_data and isinstance(item_data['produto'], dict):
                product_obj = item_data['produto']
            else:
                product_obj = item_data # Caso o produto esteja no nível superior

            if product_obj and product_obj.get('id') and product_obj.get('descricao') and product_obj.get('preco') is not None:
                item_price = 0.0
                if isinstance(product_obj['preco'], dict) and 'venda' in product_obj['preco']:
                    item_price = float(product_obj['preco'].get('venda', 0.0))
                elif isinstance(product_obj['preco'], (int, float)):
                    item_price = float(product_obj['preco'])

                produtos_encontrados.append({
                    "id": product_obj['id'],
                    "nome": product_obj['descricao'],
                    "sku": product_obj.get('codigo', ''),
                    "preco": item_price
                })
    
    # Se não encontrou por código exato ou se encontrou, mas queremos mais resultados, busca por nome (contém)
    # Evita duplicar se já achou por código e o nome é o mesmo
    if not produtos_encontrados or len(produtos_encontrados) < 5: # Ajuste o limite de resultados se necessário
        produtos_por_nome_data = bling_api_call('produtos', method='GET', data={'filters': f'nome[contem][{termo_busca}]'})
        if produtos_por_nome_data and isinstance(produtos_por_nome_data, dict) and 'data' in produtos_por_nome_data:
            for item_data in produtos_por_nome_data['data']:
                product_obj = None
                if 'produto' in item_data and isinstance(item_data['produto'], dict):
                    product_obj = item_data['produto']
                else:
                    product_obj = item_data

                if product_obj and product_obj.get('id') and product_obj.get('descricao') and product_obj.get('preco') is not None:
                    item_price = 0.0
                    if isinstance(product_obj['preco'], dict) and 'venda' in product_obj['preco']:
                        item_price = float(product_obj['preco'].get('venda', 0.0))
                    elif isinstance(product_obj['preco'], (int, float)):
                        item_price = float(product_obj['preco'])
                    
                    # Evita adicionar duplicatas se já foi encontrado por código
                    if not any(p['id'] == product_obj['id'] for p in produtos_encontrados):
                        produtos_encontrados.append({
                            "id": product_obj['id'],
                            "nome": product_obj['descricao'],
                            "sku": product_obj.get('codigo', ''),
                            "preco": item_price
                        })

    print(f"Encontrados {len(produtos_encontrados)} produtos para '{termo_busca}'.")
    return jsonify(produtos_encontrados), 200


# --- ROTAS DE AUTENTICAÇÃO E AUTORIZAÇÃO BLING OAUTH2 ---

@app.route('/connect_bling')
def connect_bling():
    """Redireciona o usuário para a página de autorização do Bling."""
    scope = "produtos clientes notasfiscais vendas" # Escopos necessários
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state

    auth_url = (
        f"{BLING_AUTHORIZE_URL}?"
        f"response_type=code&"
        f"client_id={BLING_CLIENT_ID}&"
        f"state={state}&"
        f"return_url={BLING_REDIRECT_URI}&"
        f"scope={scope}"
    )
    return redirect(auth_url)

@app.route('/callback')
def callback():
    """Endpoint de callback para receber o código de autorização do Bling."""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')

    if error:
        flash(f"Erro na autorização do Bling: {error}", "danger")
        return redirect(url_for('menu'))

    if 'oauth_state' not in session or state != session['oauth_state']:
        flash("Erro de segurança: estado inválido.", "danger")
        return redirect(url_for('menu'))
    session.pop('oauth_state', None)

    if code:
        print(f"Código de autorização recebido: {code}")
        token_data = {
            'grant_type': 'authorization_code',
            'code': code,
        }
        try:
            response = requests.post(
                BLING_TOKEN_URL,
                data=token_data,
                auth=(BLING_CLIENT_ID, BLING_CLIENT_SECRET)
            )
            response.raise_for_status()
            tokens = response.json()

            access_token = tokens.get('access_token')
            refresh_token = tokens.get('refresh_token')
            expires_in = tokens.get('expires_in')

            if access_token and refresh_token and expires_in:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("DELETE FROM bling_tokens") # Limpa tokens antigos
                        cursor.execute(
                            """
                            INSERT INTO bling_tokens (access_token, refresh_token, expires_at)
                            VALUES (%s, %s, %s)
                            """,
                            (access_token, refresh_token, expires_at)
                        )
                        conn.commit()
                flash("Conectado ao Bling com sucesso!", "success")
                return redirect(url_for('menu'))
            else:
                flash("Resposta de token inválida do Bling.", "danger")
                print(f"Resposta de token Bling incompleta: {tokens}")
                return redirect(url_for('menu'))

        except requests.exceptions.RequestException as e:
            flash(f"Erro ao obter tokens do Bling: {e}", "danger")
            if e.response is not None:
                try:
                    error_details = e.response.json()
                except ValueError:
                    error_details = e.response.text
                print(f"Detalhes do erro do Bling no callback: {e.response.status_code} - {error_details}")
            return redirect(url_for('menu'))
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {e}", "danger")
            print(f"Erro inesperado no callback do Bling: {e}")
            return redirect(url_for('menu'))
    else:
        flash("Nenhum código de autorização recebido.", "danger")
        return redirect(url_for('menu'))

# --- ROTAS PARA AUTOCOMPLETAR COM A API DO BLING ---

@app.route('/api/autocompletar-clientes')
def autocompletar_clientes():
    if 'usuario' not in session:
        return jsonify([])
    
    q = request.args.get('q', '')
    if not q:
        return jsonify([])

    data_for_api = {
        'filters': f'nome[contem]{q}',
        'pagina': request.args.get('page', 1),
        'limite': request.args.get('limit', 10),
        'tipo': 'C' # Adiciona o filtro para tipo Cliente ('C')
    }
    
    print(f"Buscando clientes com filtro: {data_for_api}")
    
    # ALTERADO: 'clientes' para 'contatos'
    response_data = bling_api_call('contatos', method='GET', data=data_for_api) 

    if response_data and 'data' in response_data:
        clientes = []
        for item in response_data['data']:
            if 'id' in item and 'nome' in item:
                clientes.append({'id': item['id'], 'text': item['nome']})
        return jsonify(clientes)
    else:
        print(f"Nenhum dado de cliente encontrado ou erro na resposta: {response_data}")
        return jsonify([])
    
@app.route('/pedidos-bling') # Nova rota para exibir Pedidos de Venda
def listar_pedidos_bling():
    if 'usuario' not in session:
        flash("Você precisa estar logado para visualizar os pedidos.", "warning")
        return redirect(url_for('login'))
    
    # VERIFICAÇÃO DE ADMIN E DE TÉCNICO PARA CONTROLE DE ACESSO E EXIBIÇÃO
    user_type = session.get('tipo')
    is_admin_user = (user_type == 'admin')
    # Definimos 'is_tech_user' como True se o tipo de usuário NÃO for 'admin'
    # Ajuste 'tecnico' para o valor real do seu tipo de usuário técnico, se houver um tipo específico
    # Por exemplo: is_tech_user = (user_type == 'tecnico')
    is_tech_user = (user_type == 'tecnico') # Agora, especificamos que é técnico apenas se o tipo for 'tecnico'
    
    # Se nem admin nem técnico puderem acessar, você pode adicionar uma verificação mais rigorosa aqui
    # Por exemplo: if not is_admin_user and not is_tech_user: ...
    # No seu código atual, se não for admin, ele redireciona, então vamos ajustar isso.

    # Nova lógica: Se não é admin, mas é para ser visto por técnicos (que não são admin), permite.
    # Se um tipo de usuário que não é nem 'admin' nem 'tecnico' tentar acessar, você pode restringir aqui.
    # Por enquanto, vou manter a lógica de que se não é admin, mas queremos que técnicos vejam, ele passa.
    # O redirecionamento abaixo só ocorrerá se você **realmente** quiser que APENAS ADMINS vejam tudo.
    # Pelo que entendi, técnicos devem ver SEM valores, admins com valores.

    # Se a intenção é que **só administradores** vejam TUDO e **técnicos** vejam PARCIALMENTE,
    # então o redirecionamento abaixo deve ser ajustado para permitir técnicos, mas com o flag `is_tech_user`
    # Já que o pedido é para técnicos verem, vamos remover o redirecionamento total para não-admins
    # e usar a flag `is_tech_user` para controlar a visibilidade no template.
    
    print("Buscando pedidos de venda e seus itens no banco de dados local...")
    pedidos = []
    
    # --- Parâmetros de Pesquisa ---
    search_observacoes = request.args.get('observacoes', '').strip()
    search_cliente = request.args.get('cliente', '').strip()
    search_data_str = request.args.get('data', '').strip() # Apenas um campo de data
    search_status = request.args.get('status', '').strip()

    # Mapeamento de IDs de status para descrições amigáveis (usado para o filtro)
    # As chaves são as descrições que aparecem no dropdown
    status_options_display = {
        "Todos": "Todos", # Opção para não filtrar por status
        "Em Aberto": "Em Aberto", 
        "Em Digitação": "Em Digitação",
        "Atendido": "Atendido",
        "Cancelado": "Cancelado",
        "Em Andamento": "Em Andamento",
        "Aprovada": "Aprovada",
        "Status Desconhecido": "Status Desconhecido" # Para casos não mapeados
    }
    
    query_conditions = []
    query_params = []

    if search_observacoes:
        query_conditions.append("observacoes ILIKE %s")
        query_params.append(f"%{search_observacoes}%")
    
    if search_cliente:
        query_conditions.append("nome_cliente ILIKE %s")
        query_params.append(f"%{search_cliente}%")

    if search_data_str: # Filtro por data única
        try:
            search_data = datetime.strptime(search_data_str, '%Y-%m-%d').date()
            query_conditions.append("data_pedido = %s") # Condição de igualdade para data
            query_params.append(search_data)
        except ValueError:
            flash("Formato de Data inválido. Use AAAA-MM-DD.", "danger")
            search_data_str = "" # Limpa o campo para o template se for inválido

    if search_status:
        # Se o status de pesquisa for "Todos", não adiciona condição
        if search_status != "Todos":
            query_conditions.append("situacao_pedido_descricao = %s")
            query_params.append(search_status)

    base_query = "SELECT * FROM pedidos_bling"
    if query_conditions:
        base_query += " WHERE " + " AND ".join(query_conditions)
    base_query += " ORDER BY data_pedido DESC, numero DESC"


    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                cursor.execute(base_query, query_params)
                pedidos_raw = cursor.fetchall()
                
                for pedido_raw in pedidos_raw:
                    pedido_dict = dict(pedido_raw)
                    
                    # Converte Decimal ou None para float para valor_total
                    if isinstance(pedido_dict.get('valor_total'), decimal.Decimal):
                        pedido_dict['valor_total'] = float(pedido_dict['valor_total'])
                    elif pedido_dict.get('valor_total') is None:
                        pedido_dict['valor_total'] = 0.0

                    # Garante que situacao_pedido_descricao seja uma string não-None
                    situacao_desc = pedido_dict.get('situacao_pedido_descricao')
                    if situacao_desc is None:
                        pedido_dict['situacao_pedido_descricao'] = ""
                    elif not isinstance(situacao_desc, str):
                        pedido_dict['situacao_pedido_descricao'] = str(situacao_desc)

                    cursor.execute(
                        "SELECT * FROM pedido_itens_bling WHERE bling_pedido_id = %s ORDER BY tipo_item, descricao_item",
                        (pedido_dict['bling_pedido_id'],)
                    )
                    itens_do_pedido_raw = cursor.fetchall()
                    itens_do_pedido_processados = []

                    for item_raw in itens_do_pedido_raw:
                        item_dict = dict(item_raw)
                        
                        # --- NOVO: Conversão inicial de tipos para float para todos os valores ---
                        # Garante que os valores brutos do DB são floats ou 0.0
                        valor_unitario_db = float(item_dict.get('valor_unitario')) if item_dict.get('valor_unitario') is not None else 0.0
                        valor_total_item_db = float(item_dict.get('valor_total_item')) if item_dict.get('valor_total_item') is not None else 0.0
                        quantidade_db = float(item_dict.get('quantidade')) if item_dict.get('quantidade') is not None else 0.0

                        # Atribui os valores convertidos de volta ao dicionário
                        item_dict['valor_unitario'] = valor_unitario_db
                        item_dict['valor_total_item'] = valor_total_item_db
                        item_dict['quantidade'] = quantidade_db
                        
                        # --- CÁLCULO PARA EXIBIÇÃO: Apenas exibe o que está no DB, que será corrigido pelo webhook ---
                        # Não há mais lógica de recálculo complexa aqui, pois o webhook já deve ter salvo corretamente.
                        # Garante que os valores são floats para formatação.
                        item_dict['valor_unitario'] = float(item_dict['valor_unitario'])
                        item_dict['valor_total_item'] = float(item_dict['valor_total_item'])
                        item_dict['quantidade'] = float(item_dict['quantidade'])
                        
                        itens_do_pedido_processados.append(item_dict)

                    pedido_dict['itens'] = itens_do_pedido_processados
                    pedidos.append(pedido_dict)

    except Exception as e:
        print(f"Erro ao carregar pedidos e itens do banco de dados: {e}")
        flash("Não foi possível carregar os pedidos e seus itens do Bling.", "danger")

    return render_template(
        'pedidos_bling.html', 
        pedidos=pedidos, 
        is_admin=is_admin_user,
        is_tech=is_tech_user, # <--- ADICIONAMOS ESTA LINHA
        # Passa os parâmetros de pesquisa de volta para o template
        search_observacoes=search_observacoes,
        search_cliente=search_cliente,
        search_data=search_data_str, # Passa a data única
        search_status=search_status,
        status_options=list(status_options_display.keys()) # Passa as opções de status para o dropdown
    )

@app.route('/webhook-bling-pedidos', methods=['POST'])
def webhook_bling_pedidos():
    try:
        data = request.json
        print(f"Webhook Bling de Pedido de Venda recebido: {json.dumps(data, indent=2)}")

        event_type = data.get('event')
        pedido_id_bling = data['data']['id']

        if event_type == 'order.deleted':
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM pedido_itens_bling WHERE bling_pedido_id = %s",
                        (pedido_id_bling,)
                    )
                    cursor.execute(
                        "DELETE FROM pedidos_bling WHERE bling_pedido_id = %s",
                        (pedido_id_bling,)
                    )
                    conn.commit()
                    print(f"Pedido Bling {pedido_id_bling} e seus itens foram excluídos do banco.")
            return jsonify({"status": "success", "message": "Webhook de exclusão de pedido processado"}), 200

        elif event_type and event_type.startswith('order.'):
            endpoint_detalhes_pedido = f'pedidos/vendas/{pedido_id_bling}'
            detalhes_pedido_api = bling_api_call(endpoint_detalhes_pedido, method='GET')

            if not detalhes_pedido_api or not detalhes_pedido_api.get('data'):
                print(f"Não foi possível obter detalhes completos para o pedido {pedido_id_bling} via API. Resposta da API: {detalhes_pedido_api}")
                return jsonify({"status": "error", "message": "Falha ao obter detalhes do pedido da API do Bling"}), 500

            full_pedido_data = detalhes_pedido_api['data']

            numero_pedido = full_pedido_data.get('numero')
            data_pedido_str = full_pedido_data.get('data')
            valor_total = full_pedido_data.get('total')
            nome_cliente = full_pedido_data.get('contato', {}).get('nome') 
            situacao_pedido_id = full_pedido_data.get('situacao', {}).get('id')
            
            situacao_pedido_descricao_api = full_pedido_data.get('situacao', {}).get('descricao')
            
            mapeamento_status = {
                6: "Em Aberto", # Definindo a descrição para ID 6
                9: "Atendido", # ajustado
                12: "Cancelado",
                18: "Aprovada",
                21: "Em digitação",
                15: "Em Andamento", # Definindo a descrição para ID 15
                # Adicione outros IDs de status e suas descrições conforme necessário
            }

            if situacao_pedido_descricao_api and situacao_pedido_descricao_api.strip() != '':
                situacao_pedido_descricao = situacao_pedido_descricao_api
            else:
                situacao_pedido_descricao = mapeamento_status.get(situacao_pedido_id, "Status Desconhecido")

            observacoes = full_pedido_data.get('observacoes') 

            link_bling = f"https://www.bling.com.br/vendas.php#edit/{pedido_id_bling}"

            data_pedido = None
            if data_pedido_str:
                data_pedido = datetime.strptime(data_pedido_str, '%Y-%m-%d').date()

            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        '''
                        UPDATE pedidos_bling
                        SET numero = %s, data_pedido = %s, valor_total = %s, nome_cliente = %s, 
                            situacao_pedido_id = %s, situacao_pedido_descricao = %s, link_bling = %s, 
                            observacoes = %s, 
                            data_ultima_atualizacao = CURRENT_TIMESTAMP
                        WHERE bling_pedido_id = %s
                        ''',
                        (numero_pedido, data_pedido, valor_total, nome_cliente,
                         situacao_pedido_id, situacao_pedido_descricao, link_bling, 
                         observacoes, 
                         pedido_id_bling)
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            '''
                            INSERT INTO pedidos_bling 
                            (bling_pedido_id, numero, data_pedido, valor_total, nome_cliente, 
                             situacao_pedido_id, situacao_pedido_descricao, link_bling, observacoes) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ''',
                            (pedido_id_bling, numero_pedido, data_pedido, valor_total, nome_cliente,
                             situacao_pedido_id, situacao_pedido_descricao, link_bling, 
                             observacoes) 
                        )
                    
                    print(f"Pedido Bling {pedido_id_bling} (Num: {numero_pedido}) processado/atualizado na tabela 'pedidos_bling'.")

                    cursor.execute(
                        "DELETE FROM pedido_itens_bling WHERE bling_pedido_id = %s",
                        (pedido_id_bling,)
                    )
                    print(f"Itens antigos do pedido {pedido_id_bling} removidos para atualização.")

                    itens_bling = full_pedido_data.get('itens', []) 
                    if not itens_bling: 
                        print(f"AVISO: A lista de itens do Bling para o pedido {pedido_id_bling} está vazia ou não pôde ser extraída. Nada será inserido na tabela de itens.")

                    for item_dict in itens_bling:
                        item_info = item_dict 
                        
                        if item_info:
                            tipo = item_info.get('tipo')
                            codigo = item_info.get('codigo')
                            descricao = item_info.get('descricao')
                            
                            # Garante que quantidade é um float
                            quantidade = float(item_info.get('quantidade')) if item_info.get('quantidade') is not None else 0.0
                            
                            # Pega os valores brutos da API
                            # O valor 'valor' da API é o que você disse que é o unitário correto (40.00)
                            # O valor 'valorUnitario' da API é o que você disse que é o incorreto (20.00)
                            valor_unitario_from_bling_api_field = item_info.get('valorUnitario') # O campo Bling 'valorUnitario'
                            valor_from_bling_api_field = item_info.get('valor') # O campo Bling 'valor' (que é o unitário correto)

                            # Converte para float, tratando None como 0.0
                            valor_unitario_from_bling_api_float = float(valor_unitario_from_bling_api_field) if valor_unitario_from_bling_api_field is not None else 0.0
                            valor_from_bling_api_float = float(valor_from_bling_api_field) if valor_from_bling_api_field is not None else 0.0

                            # --- NOVA LÓGICA DE CÁLCULO PARA SALVAR NO DB (FORÇANDO CONSISTÊNCIA) ---
                            # O valor unitário final para salvar é o que vem no campo 'valor' da API do Bling
                            final_valor_unitario = valor_from_bling_api_float 
                            
                            # O valor total final para salvar é o valor unitário final * quantidade
                            final_valor_total_item = final_valor_unitario * quantidade
                            
                            # --- FIM DA NOVA LÓGICA ---

                            tipo_item_bd = 'produto'
                            if tipo and tipo.upper() == 'S':
                                tipo_item_bd = 'servico'

                            try:
                                cursor.execute(
                                    '''
                                    INSERT INTO pedido_itens_bling 
                                    (bling_pedido_id, tipo_item, codigo_item, descricao_item, 
                                     quantidade, valor_unitario, valor_total_item)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                                    ''',
                                    (pedido_id_bling, tipo_item_bd, codigo, descricao,
                                     quantidade, final_valor_unitario, final_valor_total_item) # Usa os valores calculados
                                )
                                print(f"  - SUCESSO: Item/Serviço '{descricao}' adicionado ao pedido {pedido_id_bling}.")
                            except Exception as item_e:
                                print(f"  - ERRO ao inserir item '{descricao}' ({codigo}) para o pedido {pedido_id_bling}: {item_e}")
                    
                    conn.commit()
                    print(f"Processamento completo do webhook para pedido {pedido_id_bling}. Transação commitada.")

            return jsonify({"status": "success", "message": "Webhook de pedido processado"}), 200

        print(f"Webhook recebido, mas não é um pedido de venda ou tipo desconhecido/não suportado. Evento: {data.get('event', 'N/A')}")
        return jsonify({"status": "ignored", "message": "Tipo de webhook não processado"}), 200

    except Exception as e:
        print(f"Erro ao processar webhook do Bling (Pedido de Venda): {e}")
        return jsonify({"status": "error", "message": str(e)}), 500



@app.route('/api/autocompletar-produtos')
def autocompletar_produtos():
    if 'usuario' not in session:
        return jsonify([])

    q = request.args.get('q', '')
    if not q:
        return jsonify([])

    data_for_api = {'filters': f'nome[contem]{q}'}
    data_for_api['pagina'] = request.args.get('page', 1)
    data_for_api['limite'] = request.args.get('limit', 10)

    print(f"Buscando produtos com filtro: {data_for_api}")

    response_data = bling_api_call('produtos', method='GET', data=data_for_api)

    if response_data and 'data' in response_data:
        produtos = []
        for item in response_data['data']:
            if 'id' in item and 'nome' in item:
                produtos.append({'id': item['id'], 'text': item['nome']})
        return jsonify(produtos)
    else:
        print(f"Nenhum dado de produto encontrado ou erro na resposta: {response_data}")
        return jsonify([])

# --- INICIALIZAÇÃO DA APLICAÇÃO ---
if __name__ == '__main__':
    # Define as variáveis de ambiente para o ambiente local de desenvolvimento.
    # Estas serão usadas por os.environ.get() no início do script.
    os.environ['FLASK_SECRET_KEY'] = '2c306f12cb2487428dec7af91fe8c021ef4e1cc439bc0638c9e4debe7abe00b3'
    os.environ['BLING_CLIENT_ID'] = 'd2b6ea30918ada35ce9475a1040705eec542a504'
    # Use a chave correta para o BLING_CLIENT_SECRET
    os.environ['BLING_CLIENT_SECRET'] = 'baa38ff9a1730897e4daf78ed555bb8c1a08364899d9f3c1f7e2fac2915c' 
    # ATENÇÃO: SUBSTITUA ESTA URL PELO SEU ENDEREÇO ATUAL DO NGROK!
    # REMOVA OU COMENTE ESTA LINHA SE JÁ DEFINIU GLOBALMENTE NO TOPO
    # Esta linha está duplicada. Mantenha apenas a definição no topo do arquivo.
    # os.environ['BLING_REDIRECT_URI'] = 'https://5fda-187-115-202-58.ngrok-free.app/callback' 

    init_db()
    app.run(debug=True)