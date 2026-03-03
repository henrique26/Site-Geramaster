import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash, send_file, make_response
import pytz
from datetime import datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import DictCursor
import requests
import secrets
import base64
import cloudinary
import cloudinary.uploader
from flask import send_from_directory
from io import BytesIO
from fpdf import FPDF
from collections import Counter, defaultdict
from psycopg2.extras import RealDictCursor
import json
from werkzeug.security import check_password_hash, generate_password_hash
import decimal
from werkzeug.middleware.proxy_fix import ProxyFix
#from flask_jwt_extended import create_access_token, JWTManager
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename
import time
from datetime import timedelta  
# --- Configuração do Flask ---
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)
if os.environ.get('RENDER'):
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
@app.template_filter('data_brasil')
def data_brasil_filter(dt):
    if dt is None:
        return '-'
    
    # Se o servidor estiver em UTC (Render), subtraímos 3 horas
    # Se você estiver rodando local no Brasil, talvez não precise subtrair.
    # Para garantir que funcione na nuvem, fazemos a conta:
    dt_brasilia = dt - timedelta(hours=3)
    
    return dt_brasilia.strftime('%d/%m/%Y às %H:%M')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', '2c306f12cb2487428dec7af91fe8c021ef4e1cc439bc0638c9e4debe7abe00b3')

# --- Credenciais e Endpoints do Bling ---
BLING_CLIENT_ID = os.environ.get('BLING_CLIENT_ID', 'd2b6ea30918ada35ce9475a1040705eec542a504')
BLING_CLIENT_SECRET = os.environ.get('BLING_CLIENT_SECRET', 'baa38ff9a1730897e4daf78ed555bb8c1a08364899d9f3c1f7e2fac2915c')
# ATENÇÃO: LEMBRE-SE DE ATUALIZAR ESTA URL DO NGROK REGULARMENTE!
BLING_REDIRECT_URI = os.environ.get('BLING_REDIRECT_URI', 'https://5098b827410e.ngrok-free.app/callback') 

BLING_AUTHORIZE_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
BLING_TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"
BLING_API_BASE_URL = "https://api.bling.com.br/Api/v3/"

# --- Configuração de Fuso Horário ---
fuso_brasilia = pytz.timezone('America/Sao_Paulo')
# Configuração do JWT

app.config["JWT_SECRET_KEY"] = os.environ.get('JWT_SECRET_KEY', 'sua_chave_secreta_jwt_aqui') # Use uma chave mais forte!
#jwt = JWTManager(app)


cloudinary.config( 
  cloud_name = "dt1nmvf2d", 
  api_key = "565796149712317", 
  api_secret = "AGC5kBV7S90pY_CGmghncgdjEO0",
  secure = True
)

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
        current_token_obj = get_bling_tokens_from_db()

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
        print(f"DEBUG: Status da resposta do Bling: {response.status_code}")
        print(f"DEBUG: Dados da resposta do Bling: {response.text}")
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
@app.route('/api/login', methods=['POST'])
def api_login():
    # Apenas aceita JSON do aplicativo
    data = request.get_json()

    if not data or 'usuario' not in data or 'senha' not in data:
        return jsonify({'message': 'Dados de login ausentes.'}), 400

    usuario = data['usuario']
    senha = data['senha']
    
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # 1. Tenta autenticar na tabela de usuários da empresa
            # NOTE: Não estou usando check_password_hash porque seu código armazena a senha em texto (senha = %s)
            cursor.execute("SELECT id, usuario, tipo, nome FROM usuarios WHERE usuario = %s AND senha = %s", (usuario, senha))
            user_data = cursor.fetchone()

            user_type = None
            if user_data:
                user_id, user_name, user_type, user_full_name = user_data
            else:
                # 2. Se falhar, tenta na tabela de usuários particulares (Se essa tabela for usada no app)
                # OBS: Como não temos o CREATE TABLE de 'usuarios_particulares', assumiremos que ela segue um modelo similar.
                cursor.execute("SELECT id, usuario, nome FROM usuarios_particulares WHERE usuario = %s AND senha = %s", (usuario, senha))
                user_data_particular = cursor.fetchone()
                if user_data_particular:
                    user_id, user_name, user_full_name = user_data_particular
                    user_type = 'particular' # Define o tipo como 'particular'

            if user_data or user_data_particular:
                # O token JWT precisa de uma identidade. Usaremos o 'id' e 'tipo' para o payload
                identity = {
                    'id': user_id,
                    'usuario': user_name,
                    'tipo': user_type
                }
                
                # Cria o token de acesso (expira em algum tempo, e o app o guardará)
                access_token = create_access_token(identity=identity, expires_delta=timedelta(hours=24))
                
                return jsonify({
                    'success': True,
                    'message': 'Login API bem-sucedido!',
                    'access_token': access_token,
                    'user_type': user_type,
                    'full_name': user_full_name
                }), 200

            return jsonify({'success': False, 'message': 'Credenciais inválidas.'}), 401
    
    return jsonify({'success': False, 'message': 'Erro de servidor.'}), 500

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Tenta obter dados do JSON (App)
        data = request.get_json(silent=True)

        if data:
            usuario = data.get('email_ou_usuario')
            senha = data.get('senha')
            remember = False # App geralmente gerencia sessão por token, ignoramos aqui
        else:
            # Site
            usuario = request.form.get('usuario')
            senha = request.form.get('senha')
            # Captura o Checkbox "Lembrar de mim"
            remember = request.form.get('remember')

        if not usuario or not senha:
            if data:
                return jsonify({'success': False, 'message': 'Por favor, preencha todos os campos.'}), 400
            else:
                flash('Por favor, preencha todos os campos.', 'danger')
                return render_template('login.html')

        with get_connection() as conn:
            with conn.cursor() as cursor:
                # 1. Autenticação EMPRESA (Técnicos/Admins)
                cursor.execute("SELECT id, usuario, senha, tipo, nome FROM usuarios WHERE usuario = %s AND senha = %s", (usuario, senha))
                user_empresa = cursor.fetchone()

                if user_empresa:
                    session.clear()
                    
                    # --- LÓGICA DE LEMBRAR SESSÃO ---
                    if remember:
                        session.permanent = True # Dura 1 ano
                    else:
                        session.permanent = False # Dura até fechar o navegador

                    # Salvamos ID com dois nomes para garantir compatibilidade com todo seu sistema
                    session['user_id'] = user_empresa[0] 
                    session['id'] = user_empresa[0]
                    
                    session['usuario'] = user_empresa[1]
                    session['tipo'] = user_empresa[3]
                    session['nome'] = user_empresa[4]
                    
                    # Lógica de Alertas do Admin (Mantida igual)
                    if user_empresa[3] == 'admin':
                        cursor.execute("""
                            SELECT u.usuario, u.nome,
                            COALESCE(dep.total, 0) - COALESCE(rdv.total, 0) AS saldo
                            FROM usuarios u
                            LEFT JOIN (SELECT usuario, SUM(valor) AS total FROM depositos GROUP BY usuario) dep ON dep.usuario = u.usuario
                            LEFT JOIN (SELECT usuario, SUM(CAST(valor AS NUMERIC)) AS total FROM rdvs GROUP BY usuario) rdv ON rdv.usuario = u.usuario
                            WHERE u.tipo = 'tecnico'
                            AND (COALESCE(dep.total, 0) - COALESCE(rdv.total, 0)) < 100
                        """)
                        alertas = cursor.fetchall()
                        session['alertas'] = [(usuario, nome, float(saldo)) for usuario, nome, saldo in alertas]
                    
                    if data:
                        return jsonify({'success': True, 'message': 'Login bem-sucedido!', 'user_id': user_empresa[0], 'user_type': user_empresa[3]})
                    else:
                        # flash('Login bem-sucedido!', 'success') # Opcional
                        return redirect(url_for('menu'))

                # 2. Autenticação PARTICULAR
                cursor.execute("SELECT id, usuario, senha, nome FROM usuarios_particulares WHERE usuario = %s AND senha = %s", (usuario, senha))
                user_particular = cursor.fetchone()

                if user_particular:
                    session.clear()
                    
                    if remember: session.permanent = True

                    session['is_private_user'] = True
                    session['user_id'] = user_particular[0]
                    session['id'] = user_particular[0]
                    session['usuario'] = user_particular[1]
                    session['nome'] = user_particular[3]
                    session['tipo'] = 'particular'
                    
                    if data:
                        return jsonify({'success': True, 'message': 'Login bem-sucedido!', 'user_id': user_particular[0], 'user_type': 'particular'})
                    else:
                        return redirect(url_for('meu_dashboard'))

        # Falha
        if data:
            return jsonify({'success': False, 'message': 'Usuário ou senha inválidos.'}), 401
        else:
            flash('Usuário ou senha inválidos.', 'danger')
            return render_template('login.html')

    return render_template('login.html')

@app.route('/logout')
def logout():
    # 1. Limpa a sessão no servidor
    session.clear()
    
    # 2. Redireciona com timestamp para evitar cache de URL
    # O parametro 't' garante que o navegador trate como uma nova requisição
    resp = make_response(redirect(url_for('login', logged_out='true', t=datetime.now().timestamp())))
    
    # 3. Mata o cookie de todas as formas possíveis
    resp.set_cookie('session', '', expires=0, path='/')
    
    # 4. Cabeçalhos Anti-Cache (Obrigatórios)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    
    return resp

@app.route('/menu')
def menu():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    # Coleta o tipo de usuário da sessão
    tipo_usuario = session.get('tipo') 

    # Inicializa as listas de avisos como vazias por padrão
    alertas = []
    avisos_troca_oleo = []
    # alertas_geradores = gerar_alertas_geradores()
    # alertas_geradores = buscar_alertas_manutencao_geradores()

    alertas_geradores_resumidos = buscar_alertas_manutencao_geradores_resumido()

    # Carrega os avisos apenas se o usuário for 'admin'
    if tipo_usuario == 'admin':
        # Carrega os alertas de saldo da sessão para o admin
        alertas = session.get('alertas', [])
        print("DEBUG: Alertas de Geradores Encontrados:")

        with get_connection() as conn:
            with conn.cursor() as cursor:
                # Consulta para avisos de troca de óleo, apenas para o admin
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
                            tipo=tipo_usuario,
                            user_type=tipo_usuario,
                            alertas=alertas,
                            avisos_troca_oleo=avisos_troca_oleo,
                            alertas_geradores=alertas_geradores_resumidos)

# Sua função calcular_saldo existente
def calcular_saldo(usuario):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT SUM(valor) FROM depositos WHERE usuario = %s", (usuario,))
            total_depositos = cursor.fetchone()[0] or 0
            cursor.execute("SELECT SUM(CAST(valor AS REAL)) FROM rdvs WHERE usuario = %s", (usuario,))
            total_gastos = cursor.fetchone()[0] or 0
    return total_depositos - total_gastos

def buscar_alertas_manutencao_geradores_resumido():
    """
    Busca no BD todas as datas de vencimento e retorna um resumo para o menu principal:
    apenas duas mensagens, indicando se há geradores com manutenção vencida ou próxima.
    """
    alertas_resumidos = []
    # Usamos o fuso horário atual (Contagem, MG)
    now = datetime.now()
    today = now.date() 
    limite_proximo = today + timedelta(days=45) 
    
    # Flags para rastrear se encontramos pelo menos um alerta em cada categoria
    has_vencido = False
    has_proximo = False

    # Esta query traz a data de vencimento MAIS DISTANTE (MAX) para cada item, por gerador.
    SQL_ALERTS_MAX = """
        SELECT 
            gerador,
            MAX(data_proxima_oleo),          -- 1
            MAX(data_proximo_filtro_diesel), -- 2
            MAX(data_proximo_filtro),        -- 3
            MAX(data_proximo_liquido),       -- 4
            MAX(data_proxima_correia),       -- 5
            MAX(data_proxima_baterias),      -- 6
            MAX(data_proxima_regulagem)      -- 7
        FROM 
            registros_manutencao_geradores
        GROUP BY 
            gerador
        HAVING 
            MAX(data_proxima_oleo) IS NOT NULL OR 
            MAX(data_proximo_filtro_diesel) IS NOT NULL OR
            MAX(data_proximo_filtro) IS NOT NULL OR
            MAX(data_proximo_liquido) IS NOT NULL OR
            MAX(data_proxima_correia) IS NOT NULL OR
            MAX(data_proxima_baterias) IS NOT NULL OR
            MAX(data_proxima_regulagem) IS NOT NULL
        ORDER BY 
            gerador;
    """
    
    # Os índices de 1 a 7 são as datas de vencimento
    date_indices = range(1, 8) 
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(SQL_ALERTS_MAX)
                resultados = cursor.fetchall()
        
        # --- DEBUG: Imprime o número de registros encontrados ---
        print(f"DEBUG: Registros de Geradores MAX encontrados: {len(resultados)}")

        for r in resultados:
            # Itera sobre todas as datas de vencimento (índices 1 a 7)
            for index in date_indices:
                due_date_raw = r[index]
                
                if due_date_raw:
                    # Garante que seja um objeto date para comparação
                    # Se vier como datetime (data e hora), usa apenas a data.
                    due_date = due_date_raw.date() if isinstance(due_date_raw, datetime) else due_date_raw
                    
                    if due_date <= today:
                        # Achou um vencido
                        has_vencido = True
                    elif due_date <= limite_proximo:
                        # Achou um próximo (que não é vencido, mas está a 45 dias)
                        has_proximo = True

            # Se as duas flags forem True, podemos parar o loop (otimização)
            if has_vencido and has_proximo:
                break 

        # --- DEBUG FINAL: Imprime o estado das flags ---
        print(f"DEBUG FINAL: Status das Flags: VENCIDO={has_vencido}, PRÓXIMO={has_proximo}")

        # Gera as mensagens de resumo se as flags forem True
        if has_vencido:
            alertas_resumidos.append({
                'item': 'VENCIDA', 
                'mensagem': 'Há geradores com manutenção VENCIDA, conferir na página de controle de geradores.'
            })
            
        if has_proximo:
            alertas_resumidos.append({
                'item': 'PRÓXIMA', 
                'mensagem': 'Há geradores com manutenção PRÓXIMA de vencer, conferir na página de controle de geradores.'
            })
        
        return alertas_resumidos
            
    except Exception as e:
        print(f"Erro ao buscar alertas de geradores: {e}")
        return []

import requests

def enviar_aviso_telegram(mensagem):
    """
    Envia uma mensagem para o Telegram usando o bot e o chat ID.
    Requer que as variáveis de ambiente 'token_do_bot' e 'id_do_chat' estejam definidas.
    """

    token_do_bot = os.getenv("token_do_bot")
    id_do_chat = os.getenv("id_do_chat")

    
    
    # Adicionando uma verificação para garantir que as variáveis existam
    if not token_do_bot or not id_do_chat:
        print("Erro: Variáveis de ambiente 'token_do_bot' ou 'id_do_chat' não estão configuradas.")
        print("Verifique seu arquivo .env ou a configuração do ambiente.")
        return False
        
    url = f'https://api.telegram.org/bot{token_do_bot}/sendMessage'
    payload = {
        'chat_id': id_do_chat,
        'text': mensagem,
        'parse_mode': 'Markdown'  # Recomendo usar Markdown para formatação
    }
    
    try:
        response = requests.post(url, data=payload)
        # Lança um erro se a resposta não for 200 OK
        response.raise_for_status()
        print("Mensagem enviada com sucesso para o Telegram!")
        return True
            
    except requests.exceptions.HTTPError as http_err:
        print(f"Erro HTTP ao enviar mensagem: {http_err}")
        print(f"Resposta da API: {response.text}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Erro de conexão com o Telegram: {e}")
        return False

# ----- NOVA ROTA QUE DISPARA OS ALERTAS -----
@app.route('/enviar_alertas_telegram')
def enviar_alertas_telegram():
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return "Acesso Negado", 403
    
    mensagens_enviadas = [] # Lista para rastrear quais alertas foram enviados
    
    try:
        # --- 1. PROCESSAMENTO DE ALERTAS DE SALDO (Seu código original) ---
        
        # O bloco with/get_connection do seu código original
        with get_connection() as conn: 
            with conn.cursor() as cursor:
                # Busca todos os usuários do tipo 'tecnico'
                cursor.execute("SELECT id, usuario FROM usuarios WHERE tipo = 'tecnico'")
                tecnicos = cursor.fetchall()

            # Percorre cada técnico e calcula o saldo (fora do cursor, se 'calcular_saldo' usar nova conexão)
            for id_tecnico, nome_tecnico in tecnicos:
                saldo_tecnico = calcular_saldo(nome_tecnico) 

                if saldo_tecnico < 100:
                    mensagem_saldo = (
                        f"⚠️ Alerta de Saldo Baixo!\n"
                        f"O técnico *{nome_tecnico}* está com saldo de R$ {saldo_tecnico:.2f}."
                    )
                    enviado = enviar_aviso_telegram(mensagem_saldo)
                    if enviado:
                        mensagens_enviadas.append("Saldo Baixo")

        
        # --- 2. NOVO: PROCESSAMENTO DE ALERTAS DE MANUTENÇÃO DE GERADORES ---
        
        # Chama a função auxiliar que busca todos os alertas (próximos e vencidos)
        alertas_geradores = gerar_alertas_geradores()
        
        if alertas_geradores:
            mensagem_geradores = "🔧 *ALERTAS DE MANUTENÇÃO DE GERADORES* 🔧\n\n"
            
            for alerta in alertas_geradores:
                # Usa emoji diferente para vencido/próximo
                emoji = "🚨" if "VENCIDO" in alerta['status'] else "⚠️"
                
                mensagem_geradores += (
                    f"{emoji} TAG: *{alerta['tag']}*\n"
                    f"  - Item: {alerta['item']}\n"
                    f"  - Status: _{alerta['status']} \n_"
                    f"  - Vencimento: {alerta['vencimento']}\n\n"
                )
            
            # Envia a mensagem com todos os alertas de geradores
            enviado = enviar_aviso_telegram(mensagem_geradores)
            if enviado:
                mensagens_enviadas.append("Manutenção Geradores")
        
        # --- 3. RETORNO FINAL ---
        
        if mensagens_enviadas:
            return f"Alertas ({', '.join(mensagens_enviadas)}) enviados com sucesso para o Telegram!", 200
        else:
            return "Nenhum alerta de saldo ou manutenção de geradores pendente para envio.", 200

    except Exception as e:
        print(f"Erro ao gerar e enviar alertas: {e}")
        return "Erro ao processar e enviar alertas.", 500
    
def enviar_aviso_manutencao_telegram(placa, km_restante):
    # Por favor, use o seu token e o ID do seu grupo

    import os
    token_do_bot = os.getenv("token_do_bot")
    id_do_chat = os.getenv("id_do_chat")
    
    
    
    mensagem = (
        f"🚨 ALERTA DE MANUTENÇÃO! 🚨\n"
        f"O veículo de placa {placa} está com a próxima troca de óleo próxima.\n"
        f"Faltam apenas {km_restante:.0f} km para a manutenção."
    )
    
    url = f'https://api.telegram.org/bot{token_do_bot}/sendMessage'
    payload = {
        'chat_id': id_do_chat,
        'text': mensagem
    }
    
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            return True
        else:
            print(f"Erro ao enviar aviso de manutenção: Status {response.status_code}")
            print(f"Resposta da API: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"Erro de conexão com o Telegram: {e}")
        return False

@app.route('/rdv', methods=['GET', 'POST'])
def rdv():
    if 'usuario' not in session: return redirect(url_for('login'))

    tipo_usuario = session.get('tipo')
    usuario = session.get('usuario')
    user_id_logado = session.get('user_id')
    is_admin = (tipo_usuario == 'admin')

    # --- 1. REGISTRO DE NOVO RDV OU EXCLUSÃO (POST) ---
    if request.method == 'POST' and tipo_usuario in ['tecnico', 'admin']:
        acao = request.form.get('acao')
        
        try:
            # === INSERIR NOVO RDV ===
            if acao != 'excluir_rdv':
                valor = float(request.form['valor'].replace(',', '.'))
                descricao = request.form['descricao']
                data_obj = datetime.strptime(request.form['data'], '%Y-%m-%d').date()
                
                # Define de quem é o RDV: se for admin, pega do formulário. Se for técnico, é ele mesmo.
                usuario_alvo = usuario
                if is_admin:
                    # 'tecnico_alvo' será o nome do campo <select> no seu HTML
                    usuario_alvo = request.form.get('tecnico_alvo', usuario)

                # Trava de 14 dias para INSERÇÃO (Técnicos)
                if not is_admin:
                    hoje = datetime.today().date()
                    if (hoje - data_obj).days > 14:
                        flash('Ação Negada: Você não pode inserir registros com mais de 14 dias retroativos.', 'erro_modal')
                        return redirect(url_for('rdv'))

                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("INSERT INTO rdvs (usuario, valor, descricao, data) VALUES (%s, %s, %s, %s)",
                                       (usuario_alvo, valor, descricao, data_obj))
                        conn.commit()
                flash('RDV registrado com sucesso!', 'success')

            # === EXCLUIR RDV ===
            elif acao == 'excluir_rdv':
                rdv_id = request.form.get('rdv_id')
                
                # Variáveis de controle para executar o bloqueio com segurança fora do banco
                bloquear_exclusao = False
                mensagem_bloqueio = ""
                
                with get_connection() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                        # 1º Passo: Consultar a data e o dono do RDV
                        cursor.execute("SELECT data, usuario FROM rdvs WHERE id = %s", (rdv_id,))
                        registro = cursor.fetchone()
                        
                        if registro:
                            # Trava de segurança para Técnicos
                            if not is_admin:
                                # Garantir que o Python leia a data corretamente
                                data_registro = registro['data']
                                if isinstance(data_registro, str):
                                    data_registro = datetime.strptime(data_registro, '%Y-%m-%d').date()
                                elif hasattr(data_registro, 'date'):
                                    data_registro = data_registro.date()
                                
                                hoje = datetime.today().date()
                                dias_passados = (hoje - data_registro).days
                                
                                # Bloqueio 1: Técnico tentando apagar RDV de outra pessoa
                                if registro['usuario'] != usuario:
                                    bloquear_exclusao = True
                                    mensagem_bloqueio = 'Ação Negada: Você só pode excluir os seus próprios registros.'
                                    
                                # Bloqueio 2: A trava dos 14 dias
                                elif dias_passados > 14:
                                    bloquear_exclusao = True
                                    mensagem_bloqueio = f'Ação Negada: Este registro tem {dias_passados} dias e ultrapassa o limite de exclusão (14 dias).'
                            
                            # Se não foi bloqueado (ou se for admin), apaga do banco
                            if not bloquear_exclusao:
                                cursor.execute("DELETE FROM rdvs WHERE id = %s", (rdv_id,))
                                conn.commit()
                        else:
                            bloquear_exclusao = True
                            mensagem_bloqueio = 'Registro não encontrado no banco de dados.'

                # Dispara as mensagens
                if bloquear_exclusao:
                    flash(mensagem_bloqueio, 'erro_modal')
                else:
                    flash('Registro excluído com sucesso!', 'success')
                    
        except Exception as e:
            flash(f'Erro: {e}', 'danger')
            
        return redirect(url_for('rdv'))

    # --- 2. VISUALIZAÇÃO E FILTROS (GET) ---
    filtro_usuario = request.args.get('filtro_usuario', '').strip()
    filtro_data_ini = request.args.get('filtro_data_inicio', '').strip()
    filtro_data_fim = request.args.get('filtro_data_fim', '').strip()

    historico_unificado = [] 
    tecnicos = []
    saldos = {}
    saldo_usuario = 0
    ultimo_deposito = None

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                
                # Preparar Filtros de Data
                d_ini, d_fim = None, None
                if filtro_data_ini and filtro_data_fim:
                    try:
                        d_ini = datetime.strptime(filtro_data_ini, '%Y-%m-%d').date()
                        d_fim = datetime.strptime(filtro_data_fim, '%Y-%m-%d').date()
                    except ValueError: 
                        pass

                # === A. BUSCA RDVs (SAÍDAS) ===
                q_rdv = "SELECT rdv.id, rdv.usuario, rdv.valor, rdv.descricao, rdv.data, u.nome FROM rdvs rdv LEFT JOIN usuarios u ON rdv.usuario = u.usuario"
                c_rdv = []
                p_rdv = []

                if not is_admin:
                    c_rdv.append("rdv.usuario = %s")
                    p_rdv.append(usuario)
                elif filtro_usuario:
                    c_rdv.append("rdv.usuario = %s")
                    p_rdv.append(filtro_usuario)

                if d_ini and d_fim:
                    c_rdv.append("rdv.data BETWEEN %s AND %s")
                    p_rdv.extend([d_ini, d_fim])

                if c_rdv: q_rdv += " WHERE " + " AND ".join(c_rdv)
                cursor.execute(q_rdv, tuple(p_rdv))
                
                for r in cursor.fetchall():
                    historico_unificado.append({
                        'tipo': 'saida',
                        'id': r['id'],
                        'usuario': r['usuario'],
                        'nome': r['nome'],
                        'valor': float(r['valor']),
                        'descricao': r['descricao'],
                        'data_obj': r['data'],
                        'data_str': r['data'].strftime('%d/%m/%Y')
                    })

                # === B. BUSCA DEPÓSITOS (ENTRADAS) ===
                q_dep = "SELECT d.id, d.usuario, d.valor, d.data, d.admin_responsavel, u.nome FROM depositos d LEFT JOIN usuarios u ON d.usuario = u.usuario"
                c_dep = []
                p_dep = []

                if not is_admin:
                    c_dep.append("d.usuario = %s")
                    p_dep.append(usuario)
                elif filtro_usuario:
                    c_dep.append("d.usuario = %s")
                    p_dep.append(filtro_usuario)

                if d_ini and d_fim:
                    c_dep.append("d.data BETWEEN %s AND %s")
                    p_dep.extend([d_ini, d_fim])

                if c_dep: q_dep += " WHERE " + " AND ".join(c_dep)
                cursor.execute(q_dep, tuple(p_dep))

                for d in cursor.fetchall():
                    historico_unificado.append({
                        'tipo': 'entrada',
                        'id': d['id'],
                        'usuario': d['usuario'],
                        'nome': d['nome'],
                        'valor': float(d['valor']),
                        'descricao': f"Depósito (Por: {d['admin_responsavel']})",
                        'data_obj': d['data'],
                        'data_str': d['data'].strftime('%d/%m/%Y')
                    })

                # C. ORDENAR
                historico_unificado.sort(key=lambda x: x['data_obj'], reverse=True)

                # D. DADOS AUXILIARES
                if is_admin:
                    cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico' ORDER BY usuario")
                    tecnicos = [row[0] for row in cursor.fetchall()]
                    
                    for tec in tecnicos:
                        cursor.execute("SELECT COALESCE(SUM(valor),0) FROM depositos WHERE usuario=%s", (tec,))
                        dep = cursor.fetchone()[0]
                        cursor.execute("SELECT COALESCE(SUM(CAST(valor AS NUMERIC)),0) FROM rdvs WHERE usuario=%s", (tec,))
                        gas = cursor.fetchone()[0]
                        saldos[tec] = float(dep) - float(gas)
                else:
                    cursor.execute("SELECT ( (SELECT COALESCE(SUM(valor),0) FROM depositos WHERE usuario=%s) - (SELECT COALESCE(SUM(CAST(valor AS NUMERIC)),0) FROM rdvs WHERE usuario=%s) )", (usuario, usuario))
                    saldo_usuario = float(cursor.fetchone()[0])
                    cursor.execute("SELECT valor, data FROM depositos WHERE usuario = %s ORDER BY data DESC LIMIT 1", (usuario,))
                    res = cursor.fetchone()
                    if res: ultimo_deposito = {'valor': float(res['valor']), 'data': res['data']}

    except Exception as e:
        print(f"Erro RDV: {e}")
        flash("Erro ao carregar dados.", "danger")

    return render_template('rdv.html',
                           historico=historico_unificado,
                           tipo=tipo_usuario,
                           usuario=usuario,
                           tecnicos=tecnicos,
                           filtro_usuario=filtro_usuario,
                           filtro_data_inicio=filtro_data_ini,
                           filtro_data_fim=filtro_data_fim,
                           is_admin=is_admin,
                           saldo=saldo_usuario,
                           saldos=saldos,
                           ultimo_deposito=ultimo_deposito)

@app.route('/deposito', methods=['GET', 'POST'])
def deposito():
    if 'usuario' not in session:
        flash('Você precisa estar logado para acessar esta página.', 'danger')
        return redirect(url_for('login'))

    tipo_usuario = session.get('tipo')
    usuario_logado_username = session.get('usuario') 
    
    # --- Lógica de Inserção (POST) ---
    if request.method == 'POST' and tipo_usuario == 'admin':
        try:
            usuario_para_deposito_username = request.form['usuario']
            valor = float(request.form['valor'].replace(',', '.'))
            data_str = request.form['data']
            data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()
            admin_responsavel_username = usuario_logado_username
            
            # Pega a observação do formulário (vazio se não tiver)
            observacoes = request.form.get('observacoes', '')

            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO depositos (usuario, valor, data, admin_responsavel, observacoes) 
                        VALUES (%s, %s, %s, %s, %s)
                    """, (usuario_para_deposito_username, valor, data_obj, admin_responsavel_username, observacoes))
                    conn.commit()
            
            flash('Depósito registrado com sucesso!', 'success')
            
        except ValueError:
            flash('Erro: Valor inválido ou formato de data incorreto.', 'danger')
        except Exception as e:
            flash(f'Erro ao registrar depósito: {e}', 'danger')
            print(f"DEBUG ERROR: {e}")
        
        return redirect(url_for('deposito'))

    # --- Lógica de Visualização (GET) ---
    depositos_registrados = []
    tecnicos_disponiveis = []
    
    # Filtros
    filtro_usuario_selected = request.args.get('filtro_usuario', '').strip()
    filtro_data_inicio_str = request.args.get('filtro_data_inicio', '').strip()
    filtro_data_fim_str = request.args.get('filtro_data_fim', '').strip()

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                
                # 1. Se for Admin, busca lista de técnicos para o filtro
                if tipo_usuario == 'admin':
                    cursor.execute("SELECT usuario, nome FROM usuarios WHERE tipo = 'tecnico' ORDER BY nome")
                    tecnicos_disponiveis = cursor.fetchall()

                # 2. Query Principal (AQUI ESTAVA O ERRO)
                # Adicionei 'd.observacoes' na lista de campos
                query = """
                    SELECT
                        d.id,
                        d.usuario AS tecnico_username,
                        u_tec.nome AS tecnico_nome_completo,
                        d.valor,
                        d.data,
                        d.admin_responsavel,
                        u_admin.nome AS admin_nome_completo,
                        d.observacoes 
                    FROM depositos d
                    LEFT JOIN usuarios u_tec ON d.usuario = u_tec.usuario
                    LEFT JOIN usuarios u_admin ON d.admin_responsavel = u_admin.usuario
                """
                
                conditions = []
                params = []

                # --- Filtros ---
                if tipo_usuario == 'tecnico':
                    # Técnico só vê o dele
                    conditions.append("d.usuario = %s")
                    params.append(usuario_logado_username)
                    filtro_usuario_selected = usuario_logado_username 
                
                elif tipo_usuario == 'admin':
                    # Admin vê filtro se selecionado
                    if filtro_usuario_selected:
                        conditions.append("d.usuario = %s")
                        params.append(filtro_usuario_selected)

                # Filtro de Data
                if filtro_data_inicio_str and filtro_data_fim_str:
                    try:
                        # Valida datas
                        datetime.strptime(filtro_data_inicio_str, '%Y-%m-%d')
                        datetime.strptime(filtro_data_fim_str, '%Y-%m-%d')
                        
                        conditions.append("d.data BETWEEN %s AND %s")
                        params.append(filtro_data_inicio_str)
                        params.append(filtro_data_fim_str)
                    except ValueError:
                        flash('Datas inválidas no filtro.', 'warning')

                # Monta o SQL final
                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
                
                query += " ORDER BY d.data DESC, d.id DESC"

                # Executa
                cursor.execute(query, tuple(params))
                depositos_registrados = cursor.fetchall()

    except Exception as e:
        flash(f'Erro ao carregar dados: {e}', 'danger')
        print(f"DEBUG LOAD ERROR: {e}")

    return render_template('deposito.html',
                           depositos=depositos_registrados,
                           tipo_usuario=tipo_usuario,
                           tecnicos_disponiveis=tecnicos_disponiveis,
                           filtro_usuario_selected=filtro_usuario_selected,
                           filtro_data_inicio=filtro_data_inicio_str,
                           filtro_data_fim=filtro_data_fim_str,
                           usuario_logado_username=usuario_logado_username)



@app.route('/deposito/delete/<int:deposito_id>', methods=['POST'])
def deletar_deposito(deposito_id):
    if 'usuario' not in session or session.get('tipo') != 'admin':
        flash('Você não tem permissão para excluir depósitos.', 'danger')
        return redirect(url_for('login')) # Redireciona para login ou outra página de erro

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM depositos WHERE id = %s", (deposito_id,))
                conn.commit()
        flash('Depósito excluído com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir depósito: {e}', 'danger')
        print(f"DEBUG: Erro ao excluir depósito {deposito_id}: {e}")
    
    return redirect(url_for('deposito')) # Redireciona para a rota /deposito

@app.route('/delete_rdv/<int:rdv_id>', methods=['POST'])
def delete_rdv(rdv_id):
    # 1. Verifica se está logado
    if 'usuario' not in session: 
        return redirect(url_for('login'))

    usuario = session.get('usuario')
    tipo_usuario = session.get('tipo')
    is_admin = (tipo_usuario == 'admin')

    try:
        with get_connection() as conn:
            # Usando DictCursor para facilitar a leitura das colunas
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                
                # 2. Busca a data e o dono do RDV antes de apagar
                cursor.execute('SELECT data, usuario FROM rdvs WHERE id = %s', (rdv_id,))
                registro = cursor.fetchone()

                if not registro:
                    flash('Registro não encontrado.', 'warning')
                    return redirect(url_for('rdv'))

                # 3. Regras de Bloqueio para Técnicos (Não-admins)
                if not is_admin:
                    
                    # Bloqueio A: Tentando apagar RDV de outro técnico
                    if str(registro['usuario']).strip() != str(usuario).strip():
                        flash('Ação Negada: Você só pode excluir os seus próprios registros.', 'erro_modal')
                        return redirect(url_for('rdv'))
                    
                    # Tratar a data para garantir que o Python faça a conta de dias corretamente
                    data_registro = registro['data']
                    if isinstance(data_registro, str):
                        data_limpa = data_registro.split(' ')[0]
                        try:
                            data_registro = datetime.strptime(data_limpa, '%Y-%m-%d').date()
                        except ValueError:
                            data_registro = datetime.strptime(data_limpa, '%d/%m/%Y').date()
                    elif hasattr(data_registro, 'date'):
                        data_registro = data_registro.date()
                    
                    hoje = datetime.today().date()
                    dias_passados = (hoje - data_registro).days

                    # Bloqueio B: Trava dos 14 dias
                    if dias_passados > 14:
                        flash(f'Ação Negada: Este registro tem {dias_passados} dias. O limite para exclusão é de 14 dias retroativos.', 'erro_modal')
                        return redirect(url_for('rdv'))

                # 4. Se for admin ou se passou pelas travas do técnico, apaga com sucesso!
                cursor.execute('DELETE FROM rdvs WHERE id = %s', (rdv_id,))
                conn.commit()
                flash('Registro excluído com sucesso!', 'success')

    except Exception as e:
        flash(f'Erro ao excluir: {e}', 'danger')

    return redirect(url_for('rdv'))


@app.route('/login_privado', methods=['GET', 'POST'])
def login_privado():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        senha = request.form.get('senha')
        
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # Consulta na tabela de usuários particular
                cursor.execute('SELECT * FROM usuarios_particulares WHERE usuario = %s', (usuario,))
                user = cursor.fetchone()
        
        if user and check_password_hash(user[2], senha):
            # Se a senha for válida, inicie a sessão privada
            session.clear() # Limpa a sessão anterior
            session['is_private_user'] = True
            session['usuario'] = user[1]
            return redirect(url_for('meu_dashboard'))
        else:
            return render_template('login_privado.html', error='Usuário ou senha inválidos.')
            
    return render_template('login_privado.html')

@app.route('/meu_dashboard', methods=['GET', 'POST'])
def meu_dashboard():
    # Valida se o usuário tem a sessão privada ativa
    if 'is_private_user' not in session or not session.get('is_private_user'):
        return redirect(url_for('login_privado'))

    usuario = session['usuario']
    
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
            return redirect(url_for('meu_dashboard'))

        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO registros_particulares (usuario, tipo_registro, data, hora, latitude, longitude) VALUES (%s, %s, %s, %s, %s, %s)",
                    (usuario, tipo, data, hora, latitude, longitude)
                )
            conn.commit()
        return redirect(url_for('meu_dashboard'))

    filtro_data = request.args.get('filtro_data', '')
    filtro_data_inicio = request.args.get('filtro_data_inicio', '')
    filtro_data_fim = request.args.get('filtro_data_fim', '')
    
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # O SELECT para a folha de ponto particular é feito na tabela 'registros_particulares'
            query = "SELECT * FROM registros_particulares WHERE usuario = %s"
            params = [usuario]

            if filtro_data_inicio and filtro_data_fim:
                query += " AND data BETWEEN %s AND %s"
                params.append(filtro_data_inicio)
                params.append(filtro_data_fim)
            elif filtro_data:
                query += " AND data = %s"
                params.append(filtro_data)
            
            query += " ORDER BY data ASC, hora ASC"
            cursor.execute(query, tuple(params))
            registros = cursor.fetchall()

            registros_agrupados = {}
            for r in registros:
                # Adapte os índices de acordo com a sua tabela 'registros_particulares'
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
                    registros_agrupados[key]['entrada'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}
                elif tipo_registro == 'saida':
                    registros_agrupados[key]['saida'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}
                elif tipo_registro == 'entrada_janta':
                    registros_agrupados[key]['entrada_janta'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}
                elif tipo_registro == 'retorno_janta':
                    registros_agrupados[key]['retorno_janta'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}
                elif tipo_registro == 'saida_hora_extra':
                    registros_agrupados[key]['saida_hora_extra'] = {'id': id_registro, 'hora': hora, 'latitude': latitude, 'longitude': longitude}
                    
    return render_template(
        'folha_ponto_particular.html',
        registros_agrupados=registros_agrupados,
        registros=registros,
        usuario=usuario,
        filtro_data=filtro_data,
        filtro_data_inicio=filtro_data_inicio,
        filtro_data_fim=filtro_data_fim
    )

@app.route('/meu_excluir/<int:id>', methods=['POST'])
def meu_excluir_registro(id):
    if 'is_private_user' not in session or not session.get('is_private_user'):
        return redirect(url_for('login_privado'))

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM registros_particulares WHERE id = %s", (id,))
        conn.commit()

    return redirect(url_for('meu_dashboard'))

@app.route('/meu_excluir_dia/<data>', methods=['POST'])
def meu_excluir_dia(data):
    if 'is_private_user' not in session or not session.get('is_private_user'):
        return redirect(url_for('login_privado'))

    usuario = session['usuario']

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM registros_particulares WHERE usuario = %s AND data = %s", (usuario, data))
        conn.commit()
    return redirect(url_for('meu_dashboard'))

@app.route('/meu_exportar_pdf')
def meu_exportar_pdf():
    if 'is_private_user' not in session or not session.get('is_private_user'):
        return redirect(url_for('login'))

    usuario = session['usuario']

    filtro_data_inicio = request.args.get('filtro_data_inicio')
    filtro_data_fim = request.args.get('filtro_data_fim')

    query = "SELECT * FROM registros_particulares WHERE usuario = %s"
    params = [usuario]

    if filtro_data_inicio:
        query += " AND data >= %s"
        params.append(filtro_data_inicio)
    if filtro_data_fim:
        query += " AND data <= %s"
        params.append(filtro_data_fim)

    query += " ORDER BY data ASC, hora ASC"

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, tuple(params))
            registros = cursor.fetchall()
            
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT nome FROM usuarios_particulares WHERE usuario = %s", (usuario,))
            nome_result = cursor.fetchone()
            nome_completo = nome_result[0] if nome_result else "Usuário Pessoal"

    data_dict = {}
    for r in registros:
        key = (r[1], r[3])
        if key not in data_dict:
            data_dict[key] = {
                'entrada': None, 
                'saida': None,
                'total_horas': None
            }
        
        tipo_registro = r[2]
        hora = r[4]
        
        if tipo_registro == 'entrada':
            data_dict[key]['entrada'] = hora
        elif tipo_registro == 'saida':
            data_dict[key]['saida'] = hora

    total_geral_timedelta = timedelta(0)
    for key in data_dict:
        entrada = data_dict[key]['entrada']
        saida = data_dict[key]['saida']
        
        if entrada and saida:
            data_registro = key[1]
            entrada_dt = datetime.combine(data_registro, entrada)
            saida_dt = datetime.combine(data_registro, saida)

            if saida_dt < entrada_dt:
                saida_dt += timedelta(days=1)
                
            tempo_total = saida_dt - entrada_dt
            horas = int(tempo_total.total_seconds() / 3600)
            minutos = int((tempo_total.total_seconds() % 3600) / 60)
            
            data_dict[key]['total_horas'] = f"{horas:02d}:{minutos:02d}"
            
            # Adiciona o total diário ao total geral
            total_geral_timedelta += tempo_total

    # Formata o total geral
    total_segundos = int(total_geral_timedelta.total_seconds())
    total_horas = total_segundos // 3600
    total_minutos = (total_segundos % 3600) // 60
    total_geral_formatado = f"{total_horas:02d}:{total_minutos:02d}"


    datas = [r[3] for r in registros if r[3]]
    mes_ano = "Data Indisponível"
    if datas:
        contagem = Counter([d.strftime('%Y-%m-%d') for d in datas])
        data_mais_comum = contagem.most_common(1)[0][0]
        dt = datetime.strptime(data_mais_comum, '%Y-%m-%d')
        mes_ano = dt.strftime('%m/%Y')
        
    
    titulo = f"MÊS/ANO - {mes_ano}"
    

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    
    pdf.image('static/logo-small.png', x=143, y=10, w=60)
    pdf.set_y(25) 
    pdf.set_font("Arial", "B", 12)
    pdf.ln(5)
    
    pdf.cell(0, 7, 'CURSO DE BIOMEDICINA.', 0, 1, 'C')
    pdf.ln(1)
    pdf.cell(0, 7, 'REGISTRO DE FREQUÊNCIA - ESTÁGIO SUPERVISIONADO II/III.', 0, 1, 'C')
    pdf.ln(1)
    pdf.cell(0, 10, titulo, 0, 1, 'C')
    
    # Texto simples
    pdf.set_font("Arial", "BU", 12)
    pdf.cell(0, 7, 'LOCAL: Códon Biotecnologia.', 0, 1, 'L')
    pdf.ln(1)
    pdf.set_font("Arial", "BU", 12)
    pdf.cell(0, 7, f"ALUNO(A): {nome_completo}", 0, 1, 'L')
    pdf.ln(5)

    headers = ["Data", "Entrada", "Saída", "Total Horas", "Assinatura"]
    col_widths = [40, 40, 40, 40, 40]

    pdf.set_font("Arial", "B", 10)
    
    x_pos = (pdf.w - sum(col_widths)) / 2 
    y_pos = pdf.get_y()
    for i, header in enumerate(headers):
        pdf.set_xy(x_pos + sum(col_widths[:i]), y_pos)
        pdf.multi_cell(col_widths[i], 10, header, border=1, align='C', ln=0)
    
    pdf.set_font("Arial", "", 10)
    
    for (tec, data), times in data_dict.items():
        data_obj = data
        dias_semana = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
        dia_semana_str = dias_semana[data_obj.weekday()]
        data_formatada = f"{dia_semana_str} - {data_obj.strftime('%d/%m/%Y')}"

        pdf.set_x(x_pos)
        pdf.cell(col_widths[0], 10, data_formatada, 1, 0, 'C')
        pdf.cell(col_widths[1], 10, times['entrada'].strftime("%H:%M") if times['entrada'] else '', 1, 0, 'C')
        pdf.cell(col_widths[2], 10, times['saida'].strftime("%H:%M") if times['saida'] else '', 1, 0, 'C')
        pdf.cell(col_widths[3], 10, times['total_horas'] if times['total_horas'] else 'N/A', 1, 0, 'C')
        pdf.cell(col_widths[4], 10, '', 1, 0, 'C')
        pdf.ln(10)

    pdf.ln(10)
    
    # Adiciona o total geral
    pdf.set_font("Arial", "B", 12)
    pdf.set_x(x_pos)
    pdf.cell(sum(col_widths), 10, f"Total Mês: {total_geral_formatado}", 1, 1, 'C')
    pdf.ln(10)
    
    pdf.set_font("Arial", "BU", 12)
    pdf.cell(0, 7, 'Reposição (data, horário e setor): ', 0, 1, 'L')
    pdf.ln(1)

    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 7, '______________________________________________________________________________', 0, 1, 'L')
    pdf.cell(0, 7, '______________________________________________________________________________', 0, 1, 'L')
    pdf.cell(0, 7, '______________________________________________________________________________', 0, 1, 'L')
    pdf.cell(0, 7, '______________________________________________________________________________', 0, 1, 'L')
    pdf.ln(8)
    pdf.cell(0, 10, 'Data: _____/_____/________', 0, 1, 'C')  # 'ln=1' para pular para a próxima linha
    pdf.ln(5)
    pdf.set_font("Arial", "", 10)
    pdf.set_x(x_pos)
    pdf.cell(0, 10, 'Assinatura: _________________________________________', 0, 1, 'C') 
    pdf_output = BytesIO(pdf.output(dest='S'))

    return send_file(pdf_output, download_name='folha_ponto.pdf', as_attachment=True, mimetype='application/pdf')



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


from flask import render_template, request, redirect, url_for, flash, session
# Assuma que get_connection() e as tabelas já foram criadas e importadas
# (estoque_geral, estoque_tecnicos, historico_estoque, usuarios)

@app.route('/estoque', methods=['GET', 'POST'])
# @login_required # Removido para simplificar, mas você deve mantê-lo
def estoque():
    # Verifica a permissão do usuário para acessar a página
    if 'usuario' not in session or session.get('tipo') not in ['admin', 'tecnico']:
        flash('Você não tem permissão para acessar esta página.', 'danger')
        return redirect(url_for('login'))

    user_type = session.get('tipo')
    
    # ... (restante do seu código da rota estoque) ...
    if request.method == 'POST':
        acao = request.form.get('acao')
        responsavel_nome = session.get('usuario')

        if acao == 'adicionar_item_estoque':
            nome = request.form.get('nome_item')
            serial = request.form.get('serial_number')
            categoria = request.form.get('categoria')
            quantidade = int(request.form.get('quantidade_adicao'))
            descricao = request.form.get('descricao')
            data_cadastro = datetime.now()

            try:
                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            INSERT INTO estoque_geral (nome, serial_number, categoria, quantidade, descricao, data_cadastro, ativo)
                            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                        """, (nome, serial, categoria, quantidade, descricao, data_cadastro))
                        conn.commit()
                flash('Item adicionado ao estoque com sucesso!', 'success')
            except Exception as e:
                flash(f'Erro ao adicionar item: {e}', 'danger')
            return redirect(url_for('estoque'))

        elif acao == 'retirar_item_estoque':
            item_id = request.form.get('item_id')
            tecnico_id = request.form.get('tecnico_id')
            quantidade_str = request.form.get('quantidade')

            if not quantidade_str:
                flash('Erro: A quantidade não foi fornecida.', 'danger')
                return redirect(url_for('estoque'))

            quantidade = int(quantidade_str)
            data_retirada = datetime.now()

            try:
                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT nome, serial_number FROM estoque_geral WHERE id = %s AND ativo = TRUE", (item_id,))
                        item_info = cursor.fetchone()
                        
                        if not item_info:
                            flash('Erro: Item não encontrado ou inativo.', 'danger')
                            return redirect(url_for('estoque'))
                        
                        nome_item, serial_number = item_info

                        cursor.execute("SELECT usuario FROM usuarios WHERE id = %s", (tecnico_id,))
                        tecnico_nome = cursor.fetchone()[0]

                        cursor.execute("SELECT quantidade FROM estoque_geral WHERE id = %s AND ativo = TRUE", (item_id,))
                        estoque_disponivel = cursor.fetchone()

                        if not estoque_disponivel or estoque_disponivel[0] < quantidade:
                            flash('Erro: Quantidade insuficiente no estoque geral ou item inativo.', 'danger')
                            return redirect(url_for('estoque'))

                        cursor.execute("UPDATE estoque_geral SET quantidade = quantidade - %s WHERE id = %s", (quantidade, item_id))
                        cursor.execute("""
                            INSERT INTO historico_estoque (data_movimentacao, tipo_movimentacao, item_id, tecnico_id, quantidade, observacao, responsavel_nome)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (data_retirada, 'Retirada', item_id, tecnico_id, quantidade, 'Retirada para uso em serviço', responsavel_nome))
                        cursor.execute("""
                            INSERT INTO estoque_tecnicos (item_id, tecnico_id, quantidade_entregue, data_retirada, responsavel_nome)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (item_id, tecnico_id) DO UPDATE
                            SET quantidade_entregue = estoque_tecnicos.quantidade_entregue + EXCLUDED.quantidade_entregue
                        """, (item_id, tecnico_id, quantidade, data_retirada, responsavel_nome))

                        conn.commit()
                        flash('Item retirado com sucesso!', 'success')
                        
                        # --- INÍCIO DA ADIÇÃO: LÓGICA DE NOTIFICAÇÃO ---
                        mensagem = (
                            f"📦 *NOVA RETIRADA DE ESTOQUE*\n"
                            f"----------------------------------------\n"
                            f"• Item: {nome_item}\n"
                            f"• Nº de Série: {serial_number}\n"
                            f"• Quantidade: {quantidade}\n"
                            f"• Técnico: {tecnico_nome}\n"
                            f"• Retirado por: {responsavel_nome}\n"
                            f"• Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                        )
                        enviar_aviso_telegram(mensagem)
                        # --- FIM DA ADIÇÃO ---

            except Exception as e:
                flash(f'Erro ao retirar item: {e}', 'danger')
            return redirect(url_for('estoque'))

        elif acao == 'devolver_item_estoque':
            item_id_tecnico = request.form.get('item_id_tecnico')
            tecnico_id = request.form.get('tecnico_id_devolucao')
            quantidade_str = request.form.get('quantidade_devolucao')

            if not quantidade_str or not item_id_tecnico or not tecnico_id:
                flash('Erro: Dados incompletos para a devolução.', 'danger')
                return redirect(url_for('estoque', tecnico_id=tecnico_id))

            quantidade = int(quantidade_str)
            data_devolucao = datetime.now()

            try:
                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        # Buscando dados para a notificação
                        cursor.execute("SELECT T2.nome, T2.serial_number FROM estoque_tecnicos T1 JOIN estoque_geral T2 ON T1.item_id = T2.id WHERE T1.id = %s", (item_id_tecnico,))
                        item_info = cursor.fetchone()
                        if not item_info:
                            flash('Erro: Item do técnico não encontrado.', 'danger')
                            return redirect(url_for('estoque', tecnico_id=tecnico_id))
                            
                        nome_item, serial_number = item_info
                        
                        cursor.execute("SELECT usuario FROM usuarios WHERE id = %s", (tecnico_id,))
                        tecnico_nome = cursor.fetchone()[0]

                        cursor.execute("SELECT quantidade_entregue, item_id FROM estoque_tecnicos WHERE id = %s AND tecnico_id = %s", (item_id_tecnico, tecnico_id))
                        estoque_tecnico = cursor.fetchone()

                        if not estoque_tecnico or estoque_tecnico[0] < quantidade:
                            flash('Erro: Quantidade insuficiente no estoque do técnico.', 'danger')
                            return redirect(url_for('estoque', tecnico_id=tecnico_id))

                        item_id_estoque_geral = estoque_tecnico[1]
                        nova_quantidade = estoque_tecnico[0] - quantidade
                        
                        if nova_quantidade > 0:
                            cursor.execute("UPDATE estoque_tecnicos SET quantidade_entregue = %s WHERE id = %s", (nova_quantidade, item_id_tecnico))
                        else:
                            cursor.execute("DELETE FROM estoque_tecnicos WHERE id = %s", (item_id_tecnico,))
                        
                        cursor.execute("UPDATE estoque_geral SET quantidade = quantidade + %s WHERE id = %s", (quantidade, item_id_estoque_geral))
                        cursor.execute("""
                            INSERT INTO historico_estoque (data_movimentacao, tipo_movimentacao, item_id, tecnico_id, quantidade, observacao, responsavel_nome)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (data_devolucao, 'Devolução', item_id_estoque_geral, tecnico_id, quantidade, 'Devolução para estoque geral', responsavel_nome))

                        conn.commit()
                        flash('Item devolvido ao estoque geral com sucesso!', 'success')
                        
                        # --- INÍCIO DA ADIÇÃO: LÓGICA DE NOTIFICAÇÃO ---
                        mensagem = (
                            f"📦 *NOVA DEVOLUÇÃO DE ESTOQUE*\n"
                            f"----------------------------------------\n"
                            f"• Item: {nome_item}\n"
                            f"• Nº de Série: {serial_number}\n"
                            f"• Quantidade: {quantidade}\n"
                            f"• Técnico: {tecnico_nome}\n"
                            f"• Devolvido por: {responsavel_nome}\n"
                            f"• Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                        )
                        enviar_aviso_telegram(mensagem)
                        # --- FIM DA ADIÇÃO ---
                        
            except Exception as e:
                flash(f'Erro ao devolver item: {e}', 'danger')
            return redirect(url_for('estoque', tecnico_id=tecnico_id))

        elif acao == 'inativar_item':
            if user_type != 'admin':
                flash('Você não tem permissão para inativar itens.', 'danger')
                return redirect(url_for('estoque'))
            
            item_id = request.form.get('item_id')
            
            try:
                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("UPDATE estoque_geral SET ativo = FALSE WHERE id = %s", (item_id,))
                        conn.commit()
                flash('Item inativado com sucesso.', 'success')
            except Exception as e:
                flash(f'Erro ao inativar item: {e}', 'danger')
            return redirect(url_for('estoque'))

        elif acao == 'ativar_item':
            if user_type != 'admin':
                flash('Você não tem permissão para ativar itens.', 'danger')
                return redirect(url_for('estoque'))
                
            item_id = request.form.get('item_id')
            
            try:
                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("UPDATE estoque_geral SET ativo = TRUE WHERE id = %s", (item_id,))
                        conn.commit()
                flash('Item ativado com sucesso.', 'success')
            except Exception as e:
                flash(f'Erro ao ativar item: {e}', 'danger')
            return redirect(url_for('estoque'))
    # ... (O CÓDIGO DO MÉTODO GET CONTINUA SEM ALTERAÇÕES) ...
    termo_busca = request.args.get('termo_busca', '').strip()
    filtro_tecnico_id = request.args.get('tecnico_id')
    termo_busca_historico = request.args.get('termo_busca_historico', '').strip()
    estoque_geral = []
    historico = []
    tecnicos = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, nome, usuario FROM usuarios WHERE tipo IN ('tecnico', 'admin') ORDER BY nome ASC")
                tecnicos = cursor.fetchall()
                if termo_busca and not filtro_tecnico_id:
                    termo_busca_formatado = f"%{termo_busca}%"
                    cursor.execute("""
                        SELECT 
                            E.id, 
                            E.nome, 
                            E.serial_number, 
                            E.categoria, 
                            E.quantidade, 
                            E.ativo,
                            (SELECT 
                                U.usuario
                            FROM historico_estoque AS H
                            JOIN usuarios AS U ON H.tecnico_id = U.id
                            WHERE H.item_id = E.id
                            ORDER BY H.data_movimentacao DESC
                            LIMIT 1) AS ultimo_tecnico,
                            (SELECT 
                                H.data_movimentacao
                            FROM historico_estoque AS H
                            WHERE H.item_id = E.id
                            ORDER BY H.data_movimentacao DESC
                            LIMIT 1) AS ultima_data_retirada,
                            (SELECT 
                                H.tipo_movimentacao
                            FROM historico_estoque AS H
                            WHERE H.item_id = E.id
                            ORDER BY H.data_movimentacao DESC
                            LIMIT 1) AS ultima_movimentacao_tipo
                        FROM estoque_geral AS E
                        WHERE E.nome ILIKE %s OR E.serial_number ILIKE %s
                        ORDER BY E.nome ASC
                    """, (termo_busca_formatado, termo_busca_formatado))
                else:
                    cursor.execute("""
                        SELECT 
                            E.id, 
                            E.nome, 
                            E.serial_number, 
                            E.categoria, 
                            E.quantidade, 
                            E.ativo,
                            (SELECT 
                                U.usuario
                            FROM historico_estoque AS H
                            JOIN usuarios AS U ON H.tecnico_id = U.id
                            WHERE H.item_id = E.id
                            ORDER BY H.data_movimentacao DESC
                            LIMIT 1) AS ultimo_tecnico,
                            (SELECT 
                                H.data_movimentacao
                            FROM historico_estoque AS H
                            WHERE H.item_id = E.id
                            ORDER BY H.data_movimentacao DESC
                            LIMIT 1) AS ultima_data_retirada,
                            (SELECT 
                                H.tipo_movimentacao
                            FROM historico_estoque AS H
                            WHERE H.item_id = E.id
                            ORDER BY H.data_movimentacao DESC
                            LIMIT 1) AS ultima_movimentacao_tipo
                        FROM estoque_geral AS E
                        ORDER BY E.nome ASC
                    """)
                estoque_geral = cursor.fetchall()
                if termo_busca_historico:
                    termo_busca_historico_formatado = f"%{termo_busca_historico}%"
                    cursor.execute("""
                        SELECT 
                            H.data_movimentacao, 
                            H.tipo_movimentacao, 
                            E.nome, 
                            U.usuario, 
                            H.quantidade, 
                            H.observacao,
                            H.responsavel_nome
                        FROM historico_estoque AS H
                        JOIN estoque_geral AS E ON H.item_id = E.id
                        JOIN usuarios AS U ON H.tecnico_id = U.id
                        WHERE E.nome ILIKE %s OR U.usuario ILIKE %s
                        ORDER BY H.data_movimentacao DESC
                    """, (termo_busca_historico_formatado, termo_busca_historico_formatado))
                else:
                    cursor.execute("""
                        SELECT 
                            H.data_movimentacao, 
                            H.tipo_movimentacao, 
                            E.nome, 
                            U.usuario, 
                            H.quantidade, 
                            H.observacao,
                            H.responsavel_nome
                        FROM historico_estoque AS H
                        JOIN estoque_geral AS E ON H.item_id = E.id
                        JOIN usuarios AS U ON H.tecnico_id = U.id
                        ORDER BY H.data_movimentacao DESC
                    """)
                historico = cursor.fetchall()
                if filtro_tecnico_id:
                    cursor.execute("""
                        SELECT T1.id, T2.nome, T2.serial_number, T1.quantidade_entregue, T1.data_retirada, T1.responsavel_nome
                        FROM estoque_tecnicos T1
                        INNER JOIN estoque_geral T2 ON T1.item_id = T2.id
                        WHERE T1.tecnico_id = %s
                        ORDER BY T2.nome ASC
                    """, (filtro_tecnico_id,))
                    estoque_geral = cursor.fetchall()
            
    except Exception as e:
        flash(f'Erro ao carregar dados: {e}', 'danger')
    return render_template('estoque.html',
                            estoque_geral=estoque_geral,
                            historico=historico,
                            tecnicos=tecnicos,
                            filtro_tecnico_id=filtro_tecnico_id,
                            is_admin=user_type == 'admin',
                            termo_busca=termo_busca,
                            termo_busca_historico=termo_busca_historico)
     
def criar_conta_a_pagar(data_registro, valor_registro, placa_veiculo, nome_usuario, bling_id):
    """
    Cria uma conta a pagar no Bling, usando o ID de contato do usuário.
    """
    # ----------------------------------------------------------------------
    # AVISO: FUNÇÃO TEMPORARIAMENTE DESATIVADA PARA IMPEDIR CHAMADAS AO BLING
    # ----------------------------------------------------------------------
    print("AVISO: CHAMADA 'criar_conta_a_pagar' IGNORADA (Função desativada).")
    return None
    
    # O código original ABAIXO não será executado por causa do 'return None' acima.

    data_formatada = data_registro.strftime('%Y-%m-%d')
    
    # Valida se o ID do Bling foi fornecido
    if not bling_id:
        print("Erro: ID do Bling do usuário não encontrado. Não foi possível criar a conta a pagar.")
        return None
        
    payload = {
        "vencimento": data_formatada,
        "valor": valor_registro,
        "contato": {
            # --- AGORA USAMOS O ID DINÂMICO QUE VOCÊ PASSOU ---
            "id": bling_id
        },
        "dataEmissao": data_formatada,
        "competencia": data_formatada,
        "formaPagamento": {
            "id": 397375 # Substitua pelo ID real
        },
        "categoria": {
            "id": 10106422671 # Substitua pelo ID real
        },
        "portador": {
            "id": 11482039255 # Substitua pelo ID real do portador
        },
        "historico": f"Abastecimento do veículo {placa_veiculo} realizado por {nome_usuario}.",
        
        "ocorrencia": {
            "tipo": 1 # Tipo 1 = Conta a Pagar
        }
    }
    
    # return bling_api_call(endpoint="contas/pagar", method="POST", data=payload)
    return None # Retorno simulado se fosse reativada, retire esta linha com o "return None" de cima.
def obter_datas_alerta_max():
    """
    Busca a data de vencimento mais distante (MAX) para cada tipo de manutenção
    por gerador, garantindo que as datas de 2 anos sejam preservadas 
    mesmo após registros de 1 ano.
    """
    conn = get_connection()
    registros_max = []
    
    # A QUERY CORRETA: MAX(data) por gerador
    query_max_dates = """
        SELECT 
            gerador,
            MAX(data_proxima_oleo) AS max_oleo,
            MAX(horimetro_proximo_oleo) AS max_hor_oleo,
            MAX(data_proximo_filtro_diesel) AS max_diesel,
            MAX(data_proximo_filtro) AS max_filtro_ar,
            MAX(data_proximo_liquido) AS max_liquido,
            MAX(data_proxima_correia) AS max_correia,
            MAX(data_proxima_baterias) AS max_baterias,
            MAX(data_proxima_regulagem) AS max_regulagem
        FROM 
            registros_manutencao_geradores 
        GROUP BY 
            gerador
        ORDER BY 
            gerador;
    """
    
    try:
        cursor = conn.cursor()
        cursor.execute(query_max_dates)
        # Os resultados virão como tuplas: (gerador, max_oleo, max_hor_oleo, ...)
        registros_max = cursor.fetchall() 
        
    except Exception as e:
        print(f"Erro ao buscar datas máximas para alertas: {e}")
    finally:
        conn.close()
        
    return registros_max

# A lógica de alertas de vencimento será separada, usando o resultado acima
def gerar_alertas_com_max(registros_max):
    alerts = []
    today = date.today() 
    ALERT_THRESHOLD_DAYS = 45

    # Mapeamento de índices no resultado da query_max_dates (Atenção aos índices!)
    # Índice 0 é 'gerador', 1 é 'max_oleo', 3 é 'max_diesel', 4 é 'max_filtro_ar', etc.
    maintenance_items = { 
        1: "Troca de Óleo (Data)",       # max_oleo (Índice 1)
        3: "Filtro Diesel",             # max_diesel (Índice 3)
        4: "Filtro de Ar (2 Anos)",     # max_filtro_ar (Índice 4)
        5: "Líquido de Arrefecimento (2 Anos)", # max_liquido (Índice 5)
        6: "Correia (2 Anos)",          # max_correia (Índice 6)
        7: "Baterias (2 Anos)",         # max_baterias (Índice 7)
        8: "Regulagem de Válvulas (2 Anos)" # max_regulagem (Índice 8)
    }

    for r in registros_max:
        tag = r[0] # O nome do gerador está no índice 0
        
        for index, item_name in maintenance_items.items():
            due_date = r[index]
            
            if due_date and (isinstance(due_date, datetime) or isinstance(due_date, date)):
                if isinstance(due_date, datetime):
                    due_date = due_date.date()
                
                days_left = (due_date - today).days

                # Lógica de Alerta de Vencido ou Próximo (inalterada)
                if days_left <= 0:
                    alerts.append({
                        "tag": tag, "item": item_name,
                        "vencimento": due_date.strftime('%d/%m/%Y'), "status": "VENCIDO"
                    })
                elif 0 < days_left <= ALERT_THRESHOLD_DAYS:
                    alerts.append({
                        "tag": tag, "item": item_name,
                        "vencimento": due_date.strftime('%d/%m/%Y'), "status": f"PRÓXIMO ({days_left} dias)"
                    })
                        
    return alerts

@app.route('/controle_geradores', methods=['GET', 'POST'])
def controle_geradores():
    # 1. Conexão com o Banco de Dados (inicia fora do POST para ser usada no GET também)
    conn = get_connection()
    
    # --- 1. LÓGICA POST (EXCLUSÃO / INSERÇÃO) ---
    if request.method == 'POST':
        acao = request.form.get('acao') # Captura a ação (excluir_registro ou salvar - implícito)

        # 1.1 Lógica de Exclusão (inalterada)
        if acao == 'excluir_registro':
            try:
                # ... (seu código de exclusão inalterado) ...
                id_registro = request.form.get('id_registro')
                if id_registro:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM registros_manutencao_geradores WHERE id = %s", (id_registro,))
                    conn.commit()
                    flash(f"Registro ID {id_registro} excluído com sucesso!", 'success')
                else:
                    flash("Erro: ID do registro não fornecido para exclusão.", 'error')
            except Exception as e:
                conn.rollback()
                flash(f"Erro ao excluir registro: {e}", 'error')
            finally:
                conn.close()
                return redirect(url_for('controle_geradores')) # Redireciona após exclusão

        # 1.2 Lógica de Inserção (salvar_registro) - Inalterada
        elif acao == 'salvar_registro': 
            
            try:
                # 1. Obter Dados do Formulário
                gerador = request.form.get('gerador') 
                data_manutencao_str = request.form.get('data_manutencao')
                
                try:
                    horimetro_atual = int(request.form.get('horimetro_atual') or 0)
                except ValueError:
                    horimetro_atual = 0 
                    
                pecas_trocadas = request.form.get('pecas_trocadas')
                observacoes = request.form.get('observacoes')
                status = request.form.get('status') 
                regime_operacao = request.form.get('regime_operacao') 
                
                # Checkboxes 
                troca_filtro_ar = True if request.form.get('troca_filtro_ar') else False
                troca_liq_arref = True if request.form.get('troca_liq_arref') else False
                troca_correia = True if request.form.get('troca_correia') else False
                troca_baterias = True if request.form.get('troca_baterias') else False
                troca_regulagem_valvulas = True if request.form.get('troca_regulagem_valvulas') else False 
                filtro_diesel_periodo = request.form.get('filtro_diesel_periodo')
                
                # --- LÓGICA DE "CARRY-OVER" (Passo A: Buscar datas anteriores) ---
                cursor = conn.cursor() 
                cursor.execute("""
                    SELECT 
                        data_proxima_oleo, 
                        data_proximo_filtro_diesel, 
                        data_proximo_filtro, 
                        data_proximo_liquido, 
                        data_proxima_correia, 
                        data_proxima_baterias, 
                        data_proxima_regulagem 
                    FROM registros_manutencao_geradores 
                    WHERE gerador = %s 
                    ORDER BY id DESC 
                    LIMIT 1
                """, (gerador,))
                
                registro_anterior = cursor.fetchone()
                
                datas_anteriores = {
                    'oleo': None, 'diesel': None, 'filtro': None, 'liquido': None, 
                    'correia': None, 'baterias': None, 'regulagem': None
                }
                if registro_anterior:
                    datas_anteriores = {
                        'oleo': registro_anterior[0], # data_proxima_oleo
                        'diesel': registro_anterior[1], # data_proximo_filtro_diesel
                        'filtro': registro_anterior[2], # data_proximo_filtro (Ar)
                        'liquido': registro_anterior[3], # data_proximo_liquido
                        'correia': registro_anterior[4], # data_proxima_correia
                        'baterias': registro_anterior[5], # data_proxima_baterias
                        'regulagem': registro_anterior[6] # data_proxima_regulagem
                    }
                # O cursor (cursor) continua aberto para o INSERT

                # 2. Cálculos de Previsão de Próxima Troca (Inalterado)
                data_manutencao = datetime.strptime(data_manutencao_str, '%Y-%m-%d').date()
                
                DOIS_ANOS = timedelta(days=365 * 2) 
                TRES_MESES = timedelta(days=91) 
                UM_ANO = timedelta(days=365) 

                proximo_horimetro_oleo = horimetro_atual + 250
                
                if regime_operacao == 'horario_ponta':
                    proxima_data_oleo = data_manutencao + TRES_MESES
                else: 
                    proxima_data_oleo = data_manutencao + UM_ANO
                
                if filtro_diesel_periodo == '6meses':
                    data_proximo_filtro_diesel = data_manutencao + timedelta(days=183) 
                elif filtro_diesel_periodo == '1ano':
                    data_proximo_filtro_diesel = data_manutencao + UM_ANO
                else: # Se "Não Trocado"
                    data_proximo_filtro_diesel = datas_anteriores['diesel'] # <-- Carry-over

                proxima_data_filtro = (data_manutencao + DOIS_ANOS) if troca_filtro_ar else datas_anteriores['filtro']
                proxima_data_liquido = (data_manutencao + DOIS_ANOS) if troca_liq_arref else datas_anteriores['liquido']
                proxima_data_correia = (data_manutencao + DOIS_ANOS) if troca_correia else datas_anteriores['correia']
                proxima_data_baterias = (data_manutencao + DOIS_ANOS) if troca_baterias else datas_anteriores['baterias']
                proxima_data_regulagem = (data_manutencao + DOIS_ANOS) if troca_regulagem_valvulas else datas_anteriores['regulagem']
                
                # 3. Formatação dos Dados para SQL (Inalterado)
                data_manutencao_sql = data_manutencao.strftime('%Y-%m-%d')
                data_oleo_sql = proxima_data_oleo.strftime('%Y-%m-%d') if proxima_data_oleo else None
                data_hor_oleo_sql = proximo_horimetro_oleo
                
                # ... (resto das formatações de data inalteradas) ...
                data_filtro_sql = proxima_data_filtro.strftime('%Y-%m-%d') if proxima_data_filtro else None
                data_liquido_sql = proxima_data_liquido.strftime('%Y-%m-%d') if proxima_data_liquido else None
                data_correia_sql = proxima_data_correia.strftime('%Y-%m-%d') if proxima_data_correia else None
                data_baterias_sql = proxima_data_baterias.strftime('%Y-%m-%d') if proxima_data_baterias else None
                data_diesel_sql = data_proximo_filtro_diesel.strftime('%Y-%m-%d') if data_proximo_filtro_diesel else None 
                data_regulagem_sql = proxima_data_regulagem.strftime('%Y-%m-%d') if proxima_data_regulagem else None

                # 4. Inserir no Banco de Dados (Query inalterada)
                cursor.execute("""
                    INSERT INTO registros_manutencao_geradores 
                    (gerador, data_manutencao, horimetro_atual, pecas_trocadas, troca_filtro_ar, 
                    troca_liq_arref, troca_correia, troca_baterias, 
                    data_proxima_oleo, horimetro_proximo_oleo, 
                    data_proximo_filtro, data_proximo_liquido, 
                    data_proxima_correia, data_proxima_baterias, 
                    status, filtro_diesel_periodo, data_proximo_filtro_diesel, 
                    observacoes, 
                    regime_operacao, troca_regulagem_valvulas, data_proxima_regulagem)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    gerador, data_manutencao_sql, horimetro_atual, pecas_trocadas, 
                    troca_filtro_ar, troca_liq_arref, troca_correia, troca_baterias, 
                    data_oleo_sql, data_hor_oleo_sql, 
                    data_filtro_sql, data_liquido_sql, 
                    data_correia_sql, data_baterias_sql, 
                    status, filtro_diesel_periodo, data_diesel_sql, 
                    observacoes, 
                    regime_operacao, troca_regulagem_valvulas, data_regulagem_sql
                ))
                conn.commit()
                flash("Novo registro de manutenção inserido com sucesso!", 'success')
            except Exception as e:
                conn.rollback()
                print(f"Erro ao inserir no PostgreSQL: {e}")
                flash(f"Erro ao inserir registro: {e}", 'error')
            finally:
                pass 
        
        # Redireciona após qualquer POST (exclusão ou inserção)
        conn.close() 
        return redirect(url_for('controle_geradores')) 


    # --- 2. LÓGICA GET (EXIBIR REGISTROS, APLICAR FILTROS E GERAR ALERTAS) ---
    
    # 2.1 Captura dos Filtros (inalterada)
    filtro_gerador = request.args.get('gerador_filtro', '').strip()
    filtro_data = request.args.get('data_filtro', '').strip()
    filtro_status = request.args.get('status_filtro', '').strip()

    registros = []
    alerts = []
    lista_geradores = []
    
    # Variáveis para construção da query dinâmica (inalteradas)
    base_query = 'SELECT * FROM registros_manutencao_geradores'
    conditions = []
    params = []
    
    # 2.2 Construção das Condições de Filtragem (inalterada)
    if filtro_gerador:
        conditions.append("gerador ILIKE %s")
        params.append(f'%{filtro_gerador}%')
    if filtro_data:
        conditions.append("data_manutencao = %s")
        params.append(filtro_data) 
    if filtro_status:
        conditions.append("status = %s")
        params.append(filtro_status)

    if conditions:
        query_completa = base_query + ' WHERE ' + ' AND '.join(conditions) + ' ORDER BY data_manutencao DESC'
    else:
        query_completa = base_query + ' ORDER BY data_manutencao DESC'


    # 2.3 Execução da Query e Lógica de Alerta (BLOCO ALTERADO)
    
    ALERT_THRESHOLD_DAYS = 45
    today = date.today() 
    
    # Mapeamento de índices do resultado da query MAX (abaixo)
    # ATENÇÃO: Os índices mudaram em relação à query SELECT *
    maintenance_items = { 
        1: "Troca de Óleo (Data)",       # max_oleo (Índice 1)
        3: "Filtro Diesel",             # max_diesel (Índice 3)
        4: "Filtro de Ar (2 Anos)",     # max_filtro_ar (Índice 4)
        5: "Líquido de Arrefecimento (2 Anos)", # max_liquido (Índice 5)
        6: "Correia (2 Anos)",          # max_correia (Índice 6)
        7: "Baterias (2 Anos)",         # max_baterias (Índice 7)
        8: "Regulagem de Válvulas (2 Anos)" # max_regulagem (Índice 8)
    }

    try:
        # Cursor aberto para o GET
        cursor = conn.cursor()
        
        # 1. Busca os registros de HISTÓRICO (Com filtros) - PARA A TABELA
        cursor.execute(query_completa, tuple(params))
        registros = cursor.fetchall()

        # 2. Busca lista de geradores únicos para o <datalist>
        cursor.execute("SELECT DISTINCT gerador FROM registros_manutencao_geradores ORDER BY gerador")
        lista_geradores = [row[0] for row in cursor.fetchall()]

        # 3. GERAÇÃO DOS ALERTAS (BASEADO NO MAX DE CADA ITEM - SEM FILTROS DE HISTÓRICO)
        # Esta query busca a data de vencimento mais distante para CADA ITEM, por gerador.
        query_max_dates = """
            SELECT 
                gerador,
                MAX(data_proxima_oleo) AS max_oleo,
                MAX(horimetro_proximo_oleo) AS max_hor_oleo,
                MAX(data_proximo_filtro_diesel) AS max_diesel,
                MAX(data_proximo_filtro) AS max_filtro_ar,
                MAX(data_proximo_liquido) AS max_liquido,
                MAX(data_proxima_correia) AS max_correia,
                MAX(data_proxima_baterias) AS max_baterias,
                MAX(data_proxima_regulagem) AS max_regulagem
            FROM 
                registros_manutencao_geradores 
            GROUP BY 
                gerador
            ORDER BY 
                gerador;
        """
        cursor.execute(query_max_dates)
        registros_max_alerta = cursor.fetchall()
        
        
        for r_max in registros_max_alerta:
            tag = r_max[0] # O nome do gerador está no índice 0
            
            # Aplica filtro de gerador apenas na lista de alertas (se for diferente de ILIKE)
            if filtro_gerador and filtro_gerador.lower() not in tag.lower():
                continue
                
            # Verifica cada data máxima de vencimento
            for index, item_name in maintenance_items.items():
                due_date = r_max[index]
                
                if due_date and (isinstance(due_date, datetime) or isinstance(due_date, date)):
                    if isinstance(due_date, datetime):
                        due_date = due_date.date()
                    
                    days_left = (due_date - today).days

                    # Lógica de Alerta de Vencido ou Próximo
                    if days_left <= 0:
                        alerts.append({
                            "tag": tag, "item": item_name,
                            "vencimento": due_date.strftime('%d/%m/%Y'), "status": "VENCIDO"
                        })
                    elif 0 < days_left <= ALERT_THRESHOLD_DAYS:
                        alerts.append({
                            "tag": tag, "item": item_name,
                            "vencimento": due_date.strftime('%d/%m/%Y'), "status": f"PRÓXIMO ({days_left} dias)"
                        })
                        
    except Exception as e:
        print(f"Erro ao buscar registros ou gerar alertas: {e}")
        flash(f"Erro ao aplicar filtros: {e}", 'error') 
        
    finally:
        conn.close() 
    
    # 2.4 Renderiza o Template
    return render_template('controle_geradores.html', 
                            registros=registros, 
                            today=today, 
                            alerts=alerts, # <-- AGORA CONTÉM APENAS ALERTAS VÁLIDOS SEM DUPLICIDADE
                            lista_geradores=lista_geradores,
                            filtro_gerador=filtro_gerador,
                            filtro_data=filtro_data,
                            filtro_status=filtro_status)

from datetime import date, datetime, timedelta 
# ... (suas outras importações) ...

def gerar_alertas_geradores():
    """
    Busca no BD o registro de manutenção MAIS RECENTE para cada gerador (TAG)
    e retorna a lista de alertas próximos (45 dias) ou vencidos.
    """
    alerts = []
    ALERT_THRESHOLD_DAYS = 45
    today = date.today() 
    
    # Mapeamento de índices e nomes. O índice é posicional na lista de resultados (começa em 0).
    maintenance_items = {
        1: "Troca de Óleo (Data)",
        2: "Filtro Diesel",
        3: "Filtro de Ar (2 Anos)",
        4: "Líquido de Arrefecimento (2 Anos)",
        5: "Correia (2 Anos)",
        6: "Baterias (2 Anos)"
    }
    
    # *** SQL CORRIGIDO COM DISTINCT ON (PostgreSQL): ***
    # Seleciona as colunas distintas baseadas no campo 'gerador',
    # e garante que o registro mais recente (id DESC) seja escolhido.
    SQL_ALERTS = """
    SELECT DISTINCT ON (gerador)
        gerador,
        data_proxima_oleo,
        data_proximo_filtro_diesel,
        data_proximo_filtro,
        data_proximo_liquido,
        data_proxima_correia,
        data_proxima_baterias
    FROM 
        registros_manutencao_geradores
    ORDER BY 
        gerador, id DESC; -- Ordem DEVE ser pela TAG e ID mais recente
    """
    
    try:
        with get_connection() as conn: 
            with conn.cursor() as cursor:
                cursor.execute(SQL_ALERTS)
                registros = cursor.fetchall()
        
        # --- LÓGICA DE ALERTA ---
        for r in registros:
            tag = r[0] # TAG é o primeiro item (índice 0)
            
            # Itera sobre as colunas de data (a partir do índice 1)
            # index_coluna aqui é o índice da tupla 'r'
            for index_coluna, item_name in maintenance_items.items():
                due_date = r[index_coluna]
                
                if due_date and (isinstance(due_date, datetime) or isinstance(due_date, date)):
                    
                    if isinstance(due_date, datetime):
                        due_date = due_date.date()
                        
                    days_left = (due_date - today).days

                    # Checagem de 45 dias OU Vencido ( <= 0 )
                    if days_left <= ALERT_THRESHOLD_DAYS:
                        
                        status = "VENCIDO" if days_left <= 0 else f"PRÓXIMO ({days_left} dias)"
                        
                        alerts.append({
                            "tag": tag,
                            "item": item_name,
                            "vencimento": due_date.strftime('%d/%m/%Y'),
                            "status": status
                        })
    
    except Exception as e:
        # Se ocorrer um erro no SQL ou na conexão, ele será impresso aqui
        print(f"❌ Erro Crítico ao gerar alertas de geradores. Verifique o SQL ou a conexão: {e}")
        return []

    alerts_ordenados = sorted(alerts, key=lambda x: (x['status'] != 'VENCIDO', x['vencimento']))
    return alerts_ordenados

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

    # --- NOVO CÓDIGO AQUI: BUSCA DO NOME E ID DO BLING DO USUÁRIO ---
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cursor:
                cursor.execute("SELECT nome, bling_id FROM usuarios WHERE id = %s", (user_id_logado,))
                user_info = cursor.fetchone()
                if user_info:
                    nome_usuario_logado = user_info['nome']
                    bling_id_logado = user_info['bling_id']
                else:
                    nome_usuario_logado = None
                    bling_id_logado = None
    except Exception as e:
        flash('Erro ao carregar dados do usuário.', 'danger')
        print(f"DEBUG: Erro ao buscar dados do usuário logado: {e}")
        nome_usuario_logado = None
        bling_id_logado = None
    # ------------------------------------------------------------------

    # --- Lógica de POST (salvar/excluir) ---
    if request.method == 'POST':
        acao = request.form.get('acao')

        if acao == 'salvar_registro':
            placa = request.form['placa']
            tipo_registro = request.form['tipo_registro'] 
            data_reg_str = request.form['data']

            # Restrição para técnicos no salvamento de registros
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
                        cursor.execute("""
                            INSERT INTO controle_veiculos
                            (placa, tipo_registro, data, km, litros, valor, proxima_troca_km, proxima_troca_data, observacoes, usuario_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (placa, tipo_registro, data_reg, km, litros, valor, proxima_troca_km, proxima_troca_data, observacoes, user_id_logado))
                        conn.commit()
                flash('Registro salvo com sucesso!', 'success')
                
                # --- NOVO CÓDIGO AQUI: CHAMADA À API DO BLING COM O NOVO ARGUMENTO ---
                if tipo_registro == 'abastecimento' and valor is not None and valor > 0:
                    try:
                        # Passa o nome e o novo bling_id
                        bling_response = criar_conta_a_pagar(data_reg, valor, placa, nome_usuario_logado, bling_id_logado)
                        
                        if bling_response and 'data' in bling_response:
                            # Se a criação da conta for bem-sucedida
                            flash('Contas a pagar criada no Bling.', 'info')
                        else:
                            # Se a API retornou um erro
                            flash('Registro salvo, mas houve um erro ao criar a conta a pagar no Bling.', 'warning')
                            print("DEBUG: Falha na criação da conta a pagar no Bling:", bling_response)
                            
                    except Exception as e_bling:
                        # Captura qualquer erro que a função `criar_conta_a_pagar` possa ter levantado
                        flash('Registro salvo, mas houve um erro crítico ao se comunicar com o Bling.', 'warning')
                        print(f"DEBUG: Erro ao chamar serviço do Bling: {e_bling}")

                # Lógica para verificar km para aviso de manutenção
                if tipo_registro == 'abastecimento':
                    try:
                        with get_connection() as conn_check:
                            with conn_check.cursor() as cursor_check:
                                # Busca o último registro de 'troca_oleo' para a placa
                                cursor_check.execute("""
                                    SELECT proxima_troca_km
                                    FROM controle_veiculos
                                    WHERE placa = %s AND tipo_registro = 'troca_oleo'
                                    ORDER BY data DESC, id DESC
                                    LIMIT 1
                                """, (placa,))
                                resultado = cursor_check.fetchone()
                                
                                if resultado and resultado[0] is not None:
                                    proxima_troca_km_db = resultado[0]
                                    km_restante = proxima_troca_km_db - km
                                    
                                    # Verifica se faltam menos de 1000km
                                    if km_restante <= 1000 and km_restante > 0:
                                        # Assumindo que esta função está definida em algum lugar
                                        enviar_aviso_manutencao_telegram(placa, km_restante)
                                    
                    except Exception as e:
                        print(f"DEBUG: Erro ao verificar km para aviso de manutenção: {e}")
                
                if tipo_registro == 'abastecimento' and user_type == 'tecnico':
                    return redirect(url_for('controle_veiculos',
                                            acao_rdv='confirmar',
                                            placa=placa,
                                            data=data_reg,
                                            valor=valor,
                                            km=km,
                                            usuario=user_id_logado))
                return redirect(url_for('controle_veiculos'))

            except Exception as e:
                flash(f'Erro ao salvar registro: {e}', 'danger')
                print(f"DEBUG: Erro ao salvar registro: {e}") # DEBUG PRINT
                return redirect(url_for('controle_veiculos'))

        elif acao == 'excluir_registro':
            registro_id = request.form.get('id_registro')
            if registro_id:
                try:
                    registro_id = int(registro_id)
                    with get_connection() as conn:
                        with conn.cursor() as cursor:
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

    registros_com_calculos = [] 
    proximas_trocas_oleo = []
    tecnicos_list = []
    placas_list = []
    
    # Dicionários para armazenar médias por placa
    consumo_por_placa = {}
    custo_por_km_por_placa = {}

    # --- NOVAS INICIALIZAÇÕES PARA TOTAIS GERAIS AQUI ---
    total_km_desde_inicio = 0
    total_custo_desde_inicio = 0

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # Lógica para obter a lista de técnicos para o SELECT
                cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico' ORDER BY usuario")
                tecnicos_list = [user[0] for user in cursor.fetchall()]

                # --- MUDANÇA AQUI: Busca da tabela Mestra 'veiculos' ---
                # Antes buscava do histórico (controle_veiculos), agora busca do cadastro (veiculos)
                try:
                    cursor.execute("SELECT placa FROM veiculos WHERE ativo = true ORDER BY placa")
                    placas_list = [row[0] for row in cursor.fetchall()]
                except Exception as e_placa:
                    print(f"Erro ao buscar da tabela mestra de veiculos (usando fallback): {e_placa}")
                    # Fallback: se der erro (tabela não existir ainda), busca do histórico antigo
                    cursor.execute("SELECT DISTINCT placa FROM controle_veiculos ORDER BY placa")
                    placas_list = [row[0] for row in cursor.fetchall()]
                # -------------------------------------------------------

                # Consulta principal para buscar todos os registros relevantes para cálculos
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
                    filtro_tecnico = session.get('nome') or session.get('usuario')
                else:
                    if filtro_placa:
                        conditions.append("cv.placa = %s")
                        params.append(filtro_placa)

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

                    if filtro_tecnico:
                        conditions.append("u.usuario ILIKE %s")
                        params.append(f"%{filtro_tecnico}%")

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                # Ordena por placa e depois por KM para facilitar o cálculo de distância percorrida
                query += " ORDER BY cv.placa, cv.km ASC"

                cursor.execute(query, tuple(params))
                registros_db = cursor.fetchall()

                # --- Lógica de cálculo de Consumo por KM, Valor por Litro e Custo por KM por registro e para médias por placa ---
                registros_para_template_temp = [] 
                dados_por_placa = {} # Para acumular dados para cálculos por placa

                for reg_tuple in registros_db:
                    reg = list(reg_tuple) # Converte a tupla para lista para poder adicionar novos elementos
                    
                    placa = reg[1] # cv.placa
                    km_atual = reg[5] # cv.km
                    litros = reg[6] # cv.litros
                    valor_registro = reg[7] # cv.valor
                    tipo_registro = reg[3] # cv.tipo_registro

                    if placa not in dados_por_placa:
                        dados_por_placa[placa] = {
                            'total_km_rodados': 0,
                            'total_valor_gasto': 0, # Acumula o valor total gasto para a placa (para a média)
                            'consumo_total_km_l': 0,
                            'consumo_contagem': 0,
                            'ultimo_km_abastecimento': None,
                            'ultimo_litros_abastecimento': None, # Mantém para referência, mesmo que não usado no custo/km
                            'litros_totais_abastecidos': 0,# <--- ADICIONE ESTA LINHA
                            'valor_total_abastecimentos': 0
                        }
                    
                    # Acumula o valor gasto TOTAL para o cálculo do CUSTO MÉDIO por KM por placa
                    if valor_registro is not None:
                        dados_por_placa[placa]['total_valor_gasto'] += valor_registro

                    # Lógica para cálculo de CONSUMO INDIVIDUAL, VALOR POR LITRO INDIVIDUAL e CUSTO POR KM INDIVIDUAL
                    if tipo_registro == 'abastecimento' and km_atual is not None:
                        if litros is not None:
                            dados_por_placa[placa]['litros_totais_abastecidos'] += litros
                        if valor_registro is not None:
                            dados_por_placa[placa]['valor_total_abastecimentos'] += valor_registro
                            
                        consumo_km_litro_calc = '-'
                        valor_por_litro_calc = '-'
                        custo_por_km_individual_calc = '-' # Inicializa para cada registro

                        if dados_por_placa[placa]['ultimo_km_abastecimento'] is not None and km_atual > dados_por_placa[placa]['ultimo_km_abastecimento']:
                            distancia_percorrida = km_atual - dados_por_placa[placa]['ultimo_km_abastecimento']
                            
                            # Acumula KM rodado total para o cálculo do CUSTO MÉDIO por KM por placa
                            dados_por_placa[placa]['total_km_rodados'] += distancia_percorrida

                            if litros is not None and litros > 0:
                                consumo_km_litro_calc = f'{(distancia_percorrida / litros):.2f}'
                                # Acumula para a média de consumo por placa
                                dados_por_placa[placa]['consumo_total_km_l'] += (distancia_percorrida / litros)
                                dados_por_placa[placa]['consumo_contagem'] += 1
                            
                            # NOVO: Cálculo do Custo por KM INDIVIDUAL para este ABASTECIMENTO
                            if valor_registro is not None and distancia_percorrida > 0:
                                custo_por_km_individual_calc = f'R$ {(valor_registro / distancia_percorrida):.2f}'
                        
                        # Cálculo do Valor por Litro (INDIVIDUAL)
                        if litros is not None and litros > 0 and valor_registro is not None:
                            valor_por_litro_calc = f'R$ {(valor_registro / litros):.2f}'

                        # Adiciona os cálculos individuais ao registro (IMPORTANTE: Mantenha a ordem dos APPENDS!)
                        reg.append(consumo_km_litro_calc) 
                        reg.append(valor_por_litro_calc) 
                        reg.append(custo_por_km_individual_calc)

                        # Atualiza o último KM e litros para o próximo cálculo de consumo/custo individual da mesma placa
                        dados_por_placa[placa]['ultimo_km_abastecimento'] = km_atual
                        dados_por_placa[placa]['ultimo_litros_abastecimento'] = litros
                    else: # Se não for um registro de abastecimento
                        reg.append('-') # Placeholder para consumo_km_litro (índice 12)
                        reg.append('-') # Placeholder para valor_por_litro (índice 13)
                        reg.append('-') # Placeholder para custo por KM individual (índice 14)

                    registros_para_template_temp.append(reg)

                for placa, dados in dados_por_placa.items():
                    media_valor_litro = 0
                    if dados['litros_totais_abastecidos'] > 0:
                        media_valor_litro = dados['valor_total_abastecimentos'] / dados['litros_totais_abastecidos']
                    dados_por_placa[placa]['media_valor_litro'] = media_valor_litro # Adiciona ao dicionário da placa

                # Calcula as médias por placa após processar todos os registros
                for placa, dados in dados_por_placa.items():
                    # Calcula o consumo por placa
                    if dados['consumo_contagem'] > 0:
                        media_consumo_placa = dados['consumo_total_km_l'] / dados['consumo_contagem']
                        consumo_por_placa[placa] = f'{media_consumo_placa:.2f}'
                    else:
                        consumo_por_placa[placa] = '-'

                    # Calcula o custo por KM por placa (média total)
                    if dados['total_km_rodados'] > 0:
                        custo_por_km_placa = dados['total_valor_gasto'] / dados['total_km_rodados']
                        custo_por_km_por_placa[placa] = f'R$ {custo_por_km_placa:.2f}'
                    else:
                        custo_por_km_por_placa[placa] = '-'

                    # --- ACUMULA OS TOTAIS GERAIS AQUI DENTRO DESTE LOOP ---
                    total_km_desde_inicio += dados['total_km_rodados']
                    total_custo_desde_inicio += dados['total_valor_gasto']

                # Formata KM para exibição. Não precisamos mais adicionar o custo por KM aqui,
                # pois ele já foi adicionado ao registro individual no loop anterior.
                final_registros_para_template = []
                for reg in registros_para_template_temp:
                    reg_copy = list(reg) # Garante que estamos trabalhando com uma cópia mutável
                    
                    # Formata KM atual (índice 5)
                    reg_copy[5] = f"{reg_copy[5]:,.0f}".replace(',', '.') if isinstance(reg_copy[5], (int, float)) else reg_copy[5]
                    
                    # Formata Próxima troca Km (índice 8)
                    if reg_copy[8] is not None:
                        reg_copy[8] = f"{reg_copy[8]:,.0f}".replace(',', '.') if isinstance(reg_copy[8], (int, float)) else reg_copy[8]
                    
                    # NÃO ADICIONE NADA AQUI (reg_copy.append(...)) pois o Custo/KM individual já está no índice 14
                    final_registros_para_template.append(reg_copy)

                registros_com_calculos = final_registros_para_template
                registros_com_calculos.sort(key=lambda x: x[4], reverse=True) # Ordena pela data

            # Lógica para calcular o contador de troca de óleo (APENAS PARA ADMIN)
            if user_type == 'admin':
                with conn.cursor() as cursor_trocas_oleo:
                    cursor_trocas_oleo.execute("SELECT placa, MAX(km) FROM controle_veiculos GROUP BY placa")
                    latest_kms_all_vehicles = {row[0]: row[1] for row in cursor_trocas_oleo.fetchall()}

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

                    temp_proxima_troca_km = {}
                    for row in proximas_trocas_db:
                        placa, proxima_km = row
                        if proxima_km is not None:
                            temp_proxima_troca_km[placa] = proxima_km

                    for placa, latest_km_val in latest_kms_all_vehicles.items():
                        if placa in temp_proxima_troca_km:
                            km_proxima_troca = temp_proxima_troca_km[placa]
                            if latest_km_val is not None and km_proxima_troca is not None:
                                km_restantes = km_proxima_troca - latest_km_val
                                proximas_trocas_oleo.append({
                                    'placa': placa,
                                    'km_restantes': f"{km_restantes:,.0f}".replace(',', '.') if km_restantes > 0 else 'VENCIDO'
                                })
                    proximas_trocas_oleo.sort(key=lambda x: int(x['km_restantes'].replace('.', '').replace('VENCIDO', '999999999')) if isinstance(x['km_restantes'], str) else float('inf'))

    except Exception as e:
        flash(f'Erro ao carregar registros: {e}', 'danger')
        print(f"DEBUG: Erro geral no bloco GET: {e}")
        registros_com_calculos = []
        consumo_por_placa = {} # Garante que está vazio em caso de erro
        custo_por_km_por_placa = {} # Garante que está vazio em caso de erro
        proximas_trocas_oleo = []


    # --- Renderiza o template com todas as variáveis necessárias ---
    return render_template('controle_veiculos.html',
                           registros=registros_com_calculos,
                           consumo_por_placa=consumo_por_placa, 
                           custo_por_km_por_placa=custo_por_km_por_placa, 
                           filtro_placa=filtro_placa,
                           filtro_data=filtro_data,
                           filtro_tipo=filtro_tipo,
                           filtro_tecnico=filtro_tecnico,
                           user_type=user_type,
                           user_id_logado=user_id_logado,
                           proximas_trocas_oleo=proximas_trocas_oleo,
                           tecnicos=tecnicos_list,
                           placas=placas_list,
                           dados_por_placa_para_totais=dados_por_placa
                           )



def salvar_rdv_no_banco(usuario_id, data, valor, descricao):
    """Função para salvar um registro de RDV no banco de dados."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO rdvs (usuario, data, valor, descricao)
                    VALUES (%s, %s, %s, %s)
                """, (usuario_id, data, valor, descricao))
                conn.commit()
        return True, "Registro de RDV salvo com sucesso!"
    except Exception as e:
        print(f"Erro ao salvar RDV: {e}")
        return False, "Erro ao salvar o registro de RDV."

# --- Nova Rota para Adicionar o RDV do Veículo ---
@app.route('/adicionar_rdv_do_veiculo')
def adicionar_rdv_do_veiculo():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    placa = request.args.get('placa')
    data = request.args.get('data')
    valor = float(request.args.get('valor'))
    km = request.args.get('km')
    usuario_id_logado = request.args.get('usuario')

    # --- NOVO TRECHO DE CÓDIGO AQUI ---
    # Busca o nome do usuário no banco de dados a partir do ID
    usuario_nome = None
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT usuario FROM usuarios WHERE id = %s", (usuario_id_logado,))
                resultado = cursor.fetchone()
                if resultado:
                    usuario_nome = resultado[0]
    except Exception as e:
        flash("Erro ao buscar nome do usuário para o registro de RDV.", 'danger')
        print(f"DEBUG: Erro ao buscar nome do usuário: {e}")
        return redirect(url_for('controle_veiculos'))

    if not usuario_nome:
        flash("Erro: Nome do usuário não encontrado.", 'danger')
        return redirect(url_for('controle_veiculos'))
    # --- FIM DO NOVO TRECHO DE CÓDIGO ---

    descricao = f"Abastecimento - Placa {placa} - KM {km}"

    # Agora a função é chamada com o NOME do usuário, e não o ID
    sucesso, mensagem = salvar_rdv_no_banco(usuario_nome, data, valor, descricao)

    if sucesso:
        # --- CÓDIGO PARA VERIFICAR E ENVIAR O ALERTA ---
        try:
            saldo_atual = calcular_saldo(usuario_nome)
            
            if saldo_atual < 100:
                aviso_mensagem = f"⚠️ ALERTA DE SALDO BAIXO! ⚠️\nO técnico {usuario_nome} está com saldo de R$ {saldo_atual:.2f} após registrar um RDV de abastecimento."
                enviar_aviso_telegram(aviso_mensagem)
        except Exception as e:
            print(f"DEBUG: Erro ao verificar saldo para alerta: {e}")
        # --- FIM DO CÓDIGO PARA VERIFICAR E ENVIAR O ALERTA ---

        flash(mensagem, 'success')
    else:
        flash(mensagem, 'danger')

    return redirect(url_for('controle_veiculos'))

@app.route('/api/veiculos', methods=['GET', 'POST'])
def get_veiculos_data():
    # Inicialização de variáveis de autenticação
    is_authenticated = False
    user_id_logado = None
    user_type = None

    # ----------------------------------------------------
    # LÓGICA DE AUTENTICAÇÃO
    # ----------------------------------------------------
    
    # 1. Tenta autenticar pela sessão do site (acesso via navegador)
    if 'usuario' in session:
        is_authenticated = True
        user_id_logado = session.get('user_id')
        user_type = session.get('tipo')
    
    # 2. Tenta autenticar pelo JSON do app (sincronização)
    # A rota POST é geralmente usada para o app enviar dados.
    if not is_authenticated:
        data = request.get_json(silent=True)
        # Assumindo que o app pode enviar um token ou ID/Tipo de forma segura
        # ATENÇÃO: Autenticar apenas por user_id/user_type em JSON é inseguro. 
        # Em um sistema real, você usaria um JWT token no cabeçalho.
        if data and 'user_id' in data and 'user_type' in data:
            is_authenticated = True
            user_id_logado = data.get('user_id')
            user_type = data.get('user_type')

    if not is_authenticated:
        return jsonify({'success': False, 'message': 'Não autenticado.'}), 401

    # ----------------------------------------------------
    # LÓGICA DE POST (Sincronização - Salvar/Excluir)
    # ----------------------------------------------------
    if request.method == 'POST':
        data = request.get_json()
        acao = data.get('acao')

        if acao == 'salvar_registro':
            # Sua lógica de SALVAR REGISTRO é robusta e foi mantida
            placa = data.get('placa')
            tipo_registro = data.get('tipo_registro')
            data_reg_str = data.get('data')

            if user_type == 'tecnico' and tipo_registro != 'abastecimento':
                return jsonify({'success': False, 'message': 'Técnicos podem registrar apenas abastecimentos.'}), 403

            try:
                data_reg = datetime.strptime(data_reg_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'success': False, 'message': 'Formato de data inválido. Use AAAA-MM-DD.'}), 400

            km_input = data.get('km')
            try:
                # O app deve enviar o KM limpo, mas mantemos a limpeza por segurança
                km_limpo = km_input.replace('.', '').replace(',', '')
                km = int(km_limpo)
            except (ValueError, AttributeError):
                return jsonify({'success': False, 'message': 'Valor de KM inválido. Por favor, insira apenas números.'}), 400

            try:
                # O app deve enviar valores como float/null
                litros = float(data.get('litros')) if data.get('litros') is not None and data.get('litros') != '' else None
                valor = float(data.get('valor')) if data.get('valor') is not None and data.get('valor') != '' else None
            except ValueError:
                return jsonify({'success': False, 'message': 'Valores de Litros ou Valor inválidos. Use apenas números.'}), 400

            observacoes = data.get('observacoes')
            proxima_troca_km = None
            proxima_troca_data = None

            if tipo_registro == 'troca_oleo':
                proxima_troca_km = km + 10000
                proxima_troca_data = data_reg + timedelta(days=365)

            try:
                with get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            INSERT INTO controle_veiculos
                            (placa, tipo_registro, data, km, litros, valor, proxima_troca_km, proxima_troca_data, observacoes, usuario_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (placa, tipo_registro, data_reg, km, litros, valor, proxima_troca_km, proxima_troca_data, observacoes, user_id_logado))
                        conn.commit()
                return jsonify({'success': True, 'message': 'Registro salvo com sucesso!'})
            except Exception as e:
                print(f"DEBUG: Erro ao salvar registro via API: {e}")
                return jsonify({'success': False, 'message': f'Erro ao salvar registro: {e}'}), 500

        elif acao == 'excluir_registro':
            # Sua lógica de EXCLUIR REGISTRO foi mantida
            registro_id = data.get('id_registro')
            if registro_id:
                try:
                    registro_id = int(registro_id)
                    with get_connection() as conn:
                        with conn.cursor() as cursor:
                            if user_type == 'tecnico':
                                cursor.execute("SELECT usuario_id, tipo_registro FROM controle_veiculos WHERE id = %s", (registro_id,))
                                reg_owner_info = cursor.fetchone()
                                if reg_owner_info and reg_owner_info[0] == user_id_logado and reg_owner_info[1] == 'abastecimento':
                                    cursor.execute("DELETE FROM controle_veiculos WHERE id = %s", (registro_id,))
                                    conn.commit()
                                    return jsonify({'success': True, 'message': 'Registro de abastecimento excluído com sucesso!'})
                                else:
                                    return jsonify({'success': False, 'message': 'Você não tem permissão para excluir este registro ou ele não é um abastecimento seu.'}), 403
                            else: # Admin pode excluir qualquer um
                                cursor.execute("DELETE FROM controle_veiculos WHERE id = %s", (registro_id,))
                                conn.commit()
                                return jsonify({'success': True, 'message': 'Registro excluído com sucesso!'})
                except (ValueError, Exception) as e:
                    print(f"DEBUG: Erro ao excluir registro via API: {e}")
                    return jsonify({'success': False, 'message': f'Erro ao excluir registro: {e}'}), 500
            else:
                return jsonify({'success': False, 'message': 'ID do registro não fornecido para exclusão.'}), 400
        else:
            return jsonify({'success': False, 'message': 'Ação inválida.'}), 400

    # ----------------------------------------------------
    # LÓGICA DE GET (Buscar Dados para Sincronização)
    # ----------------------------------------------------
    # Essa seção faz todo o cálculo e busca do banco
    registros_com_calculos = []
    consumo_por_placa = {}
    custo_por_km_por_placa = {}
    proximas_trocas_oleo = []
    tecnicos_list = []
    placas_list = []
    dados_por_placa = {}

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # Busca de listas de apoio
                cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico' ORDER BY usuario")
                tecnicos_list = [user[0] for user in cursor.fetchall()]

                cursor.execute("SELECT DISTINCT placa FROM controle_veiculos ORDER BY placa")
                placas_list = [row[0] for row in cursor.fetchall()]

                # Query principal
                query = """
                    SELECT
                        cv.id, cv.placa, u.usuario AS tecnico_nome, cv.tipo_registro, cv.data,
                        cv.km, cv.litros, cv.valor, cv.proxima_troca_km, cv.proxima_troca_data,
                        cv.observacoes, cv.usuario_id
                    FROM controle_veiculos cv
                    LEFT JOIN usuarios u ON cv.usuario_id = u.id
                    ORDER BY cv.data DESC, cv.km DESC
                """
                
                # Filtro de permissão
                if user_type == 'tecnico':
                    query = query.replace("ORDER BY", "WHERE cv.tipo_registro = 'abastecimento' AND cv.usuario_id = %s ORDER BY")
                    cursor.execute(query, (user_id_logado,))
                else:
                    cursor.execute(query)

                registros_db = cursor.fetchall()
                
                # --- Lógica de Cálculo (Mantida inalterada) ---
                registros_para_template_temp = [] 
                dados_por_placa = {} 

                for reg_tuple in registros_db:
                    reg = list(reg_tuple)
                    
                    placa = reg[1]
                    km_atual = reg[5]
                    litros = reg[6]
                    valor_registro = reg[7]
                    tipo_registro = reg[3]

                    if placa not in dados_por_placa:
                        dados_por_placa[placa] = {
                            'total_km_rodados': 0, 'total_valor_gasto': 0,
                            'consumo_total_km_l': 0, 'consumo_contagem': 0,
                            'ultimo_km_abastecimento': None, 'litros_totais_abastecidos': 0,
                            'valor_total_abastecimentos': 0
                        }
                    
                    if valor_registro is not None:
                        dados_por_placa[placa]['total_valor_gasto'] += valor_registro

                    if tipo_registro == 'abastecimento' and km_atual is not None:
                        if litros is not None:
                            dados_por_placa[placa]['litros_totais_abastecidos'] += litros
                        if valor_registro is not None:
                            dados_por_placa[placa]['valor_total_abastecimentos'] += valor_registro
                        
                        consumo_km_litro_calc = '-'
                        valor_por_litro_calc = '-'
                        custo_por_km_individual_calc = '-'

                        if dados_por_placa[placa]['ultimo_km_abastecimento'] is not None and km_atual > dados_por_placa[placa]['ultimo_km_abastecimento']:
                            distancia_percorrida = km_atual - dados_por_placa[placa]['ultimo_km_abastecimento']
                            dados_por_placa[placa]['total_km_rodados'] += distancia_percorrida

                            if litros is not None and litros > 0:
                                consumo_km_litro_calc = f'{(distancia_percorrida / litros):.2f}'
                                dados_por_placa[placa]['consumo_total_km_l'] += (distancia_percorrida / litros)
                                dados_por_placa[placa]['consumo_contagem'] += 1
                            
                            if valor_registro is not None and distancia_percorrida > 0:
                                # Aqui, a formatação é feita para o JSON.
                                custo_por_km_individual_calc = f'R$ {(valor_registro / distancia_percorrida):.2f}'
                        
                        if litros is not None and litros > 0 and valor_registro is not None:
                            # Aqui, a formatação é feita para o JSON.
                            valor_por_litro_calc = f'R$ {(valor_registro / litros):.2f}'

                        reg.extend([consumo_km_litro_calc, valor_por_litro_calc, custo_por_km_individual_calc])
                        dados_por_placa[placa]['ultimo_km_abastecimento'] = km_atual
                        dados_por_placa[placa]['ultimo_litros_abastecimento'] = litros
                    else:
                        reg.extend(['-', '-', '-'])

                    registros_para_template_temp.append(reg)

                # CÁLCULO DE MÉDIAS FINAIS POR PLACA
                for placa, dados in dados_por_placa.items():
                    # Cálculo de média de consumo
                    if dados['consumo_contagem'] > 0:
                        media_consumo_placa = dados['consumo_total_km_l'] / dados['consumo_contagem']
                        consumo_por_placa[placa] = f'{media_consumo_placa:.2f}'
                    else:
                        consumo_por_placa[placa] = '0.00' # Retorna 0.00 para facilitar o Kotlin

                    # Cálculo de custo médio por KM
                    if dados['total_km_rodados'] > 0:
                        custo_por_km_placa = dados['total_valor_gasto'] / dados['total_km_rodados']
                        custo_por_km_por_placa[placa] = f'R$ {custo_por_km_placa:.2f}'
                    else:
                        custo_por_km_por_placa[placa] = 'R$ 0.00' # Retorna R$ 0.00 para facilitar o Kotlin

                # PRÓXIMAS TROCAS DE ÓLEO
                proximas_trocas_oleo = []
                if user_type in ['admin', 'tecnico']: # Técnico vê suas próprias trocas (se for um registro dele)
                    # Lógica para encontrar o último KM e próxima troca de óleo
                    # ... (sua lógica original para popular 'proximas_trocas_oleo' é mantida aqui) ...
                    if user_type == 'admin':
                        cursor.execute("SELECT placa, MAX(km) FROM controle_veiculos GROUP BY placa")
                        latest_kms_all_vehicles = {row[0]: row[1] for row in cursor.fetchall()}

                        query_proxima_troca = """
                            SELECT cv.placa, cv.proxima_troca_km
                            FROM controle_veiculos cv
                            WHERE cv.id IN (SELECT MAX(id) FROM controle_veiculos WHERE tipo_registro = 'troca_oleo' GROUP BY placa)
                            AND cv.tipo_registro = 'troca_oleo'
                        """
                        cursor.execute(query_proxima_troca)
                        proximas_trocas_db = cursor.fetchall()
                        temp_proxima_troca_km = {row[0]: row[1] for row in proximas_trocas_db}

                        for placa, latest_km_val in latest_kms_all_vehicles.items():
                            if placa in temp_proxima_troca_km and latest_km_val is not None and temp_proxima_troca_km[placa] is not None:
                                km_restantes = temp_proxima_troca_km[placa] - latest_km_val
                                proximas_trocas_oleo.append({
                                    'placa': placa,
                                    'km_restantes': f"{km_restantes:,.0f}".replace(',', '.') if km_restantes > 0 else 'VENCIDO'
                                })
                        
                        # Ordena, mas garante que VENCIDO vá para o final
                        proximas_trocas_oleo.sort(key=lambda x: int(x['km_restantes'].replace('.', '').replace('VENCIDO', '999999999')) if isinstance(x['km_restantes'], str) else float('inf'))

                # --- FIM DA LÓGICA DE CÁLCULO ---


                # ----------------------------------------------------
                # NOVO FINAL: CONSTRUÇÃO E RETORNO DO JSON OTIMIZADO
                # ----------------------------------------------------
                final_registros = []
                # Mapeia a tupla de retorno do DB + 3 campos de cálculo para um dicionário JSON
                for reg in registros_para_template_temp:
                    # O reg agora tem 15 elementos: [0:id, 1:placa, 2:tecnico, 3:tipo, 4:data(date), 5:km, 6:litros, 7:valor, 
                    # 8:prox_km, 9:prox_data(date), 10:obs, 11:usuario_id, 12:consumo, 13:valor/litro, 14:custo/km_indiv]
                    
                    final_registros.append({
                        'id': reg[0],
                        'placa': reg[1],
                        'tecnico_nome': reg[2],
                        'tipo_registro': reg[3],
                        # Data é convertida para string YYYY-MM-DD
                        'data': reg[4].strftime('%Y-%m-%d') if reg[4] else None, 
                        'km': reg[5], # Int
                        'litros': reg[6], # Float ou None
                        'valor': reg[7], # Float ou None
                        'proxima_troca_km': reg[8], # Int ou None
                        # Próxima data é convertida para string YYYY-MM-DD
                        'proxima_troca_data': reg[9].strftime('%Y-%m-%d') if reg[9] else None, 
                        'observacoes': reg[10],
                        'usuario_id': reg[11], # ID do usuário que registrou
                        
                        # Campos de cálculo (já estão como strings formatadas: 'XX.XX' ou 'R$ XX.XX' ou '-')
                        'consumo_km_l': reg[12], 
                        'valor_por_litro': reg[13], 
                        'custo_por_km_individual': reg[14]
                    })
        
        # Estrutura final que será enviada ao app
        return jsonify({
            'success': True,
            'message': 'Dados de veículos carregados com sucesso!',
            'registros': final_registros,
            'consumo_por_placa': consumo_por_placa, # Dict: {'placa': 'X.XX'}
            'custo_por_km_por_placa': custo_por_km_por_placa, # Dict: {'placa': 'R$ X.XX'}
            'proximas_trocas_oleo': proximas_trocas_oleo, # List of Dicts
            'tecnicos': tecnicos_list, # List of Strings
            'placas': placas_list, # List of Strings
            'user_type': user_type # Tipo do usuário logado
        })

    except Exception as e:
        print(f"DEBUG: Erro no bloco GET da API: {e}")
        return jsonify({'success': False, 'message': f'Erro ao carregar registros: {e}'}), 500

@app.route('/api/clientes', methods=['GET'])
def get_clientes():
    """
    Rota de API para obter a lista de clientes para o aplicativo.
    """
    if 'usuario' not in session:
        return jsonify({"success": False, "message": "Não autorizado."}), 401
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, nome_cliente FROM clientes ORDER BY nome_cliente")
                clientes = [{"id": row[0], "nome_cliente": row[1]} for row in cursor.fetchall()]
                return jsonify(clientes)
    except Exception as e:
        print(f"Erro ao obter clientes: {e}")
        return jsonify({"success": False, "message": "Erro ao carregar clientes."}), 500

@app.route('/api/equipamentos/<int:cliente_id>', methods=['GET'])
def get_equipamentos(cliente_id):
    """
    Rota de API para obter a lista de equipamentos de um cliente específico.
    """
    if 'usuario' not in session:
        return jsonify({"success": False, "message": "Não autorizado."}), 401
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, nome_equipamento, numero_serie_principal
                    FROM equipamentos
                    WHERE cliente_id = %s
                    ORDER BY nome_equipamento
                """, (cliente_id,))
                equipamentos = [
                    {"id": row[0], "nome": row[1], "numero_serie": row[2]} 
                    for row in cursor.fetchall()
                ]
                return jsonify(equipamentos)
    except Exception as e:
        print(f"Erro ao obter equipamentos: {e}")
        return jsonify({"success": False, "message": "Erro ao carregar equipamentos."}), 500

@app.route('/api/ordens_servico', methods=['GET', 'POST'])
def api_ordens_servico():
    """
    Rota de API para criar e listar ordens de serviço.
    - POST: Cria uma nova ordem de serviço.
    - GET: Lista as ordens de serviço do técnico logado.
    """
    if 'usuario' not in session:
        return jsonify({"success": False, "message": "Não autorizado."}), 401

    user_id_logado = session.get('user_id')
    user_type = session.get('tipo')

    # Rota para criar uma nova Ordem de Serviço (POST)
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Dados da OS não fornecidos."}), 400

        try:
            titulo = data.get('titulo')
            descricao = data.get('descricao')
            observacoes = data.get('observacoes')
            cliente_id = data.get('cliente_id')
            equipamento_id = data.get('equipamento_id')
            veiculo = data.get('veiculo')

            if not all([titulo, descricao, cliente_id, veiculo, user_id_logado]):
                return jsonify({"success": False, "message": "Dados obrigatórios faltando."}), 400

            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO ordens_servico 
                        (titulo, descricao, observacoes, tecnico_id, veiculo, cliente_id, equipamento_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (titulo, descricao, observacoes, user_id_logado, veiculo, cliente_id, equipamento_id))
                    os_id = cursor.fetchone()[0]
                    conn.commit()
            
            return jsonify({
                "success": True, 
                "message": "Ordem de Serviço criada com sucesso!", 
                "os_id": os_id
            }), 201

        except Exception as e:
            print(f"Erro ao criar ordem de serviço: {e}")
            return jsonify({"success": False, "message": "Erro interno do servidor ao criar OS."}), 500

    # Rota para listar Ordens de Serviço (GET)
    elif request.method == 'GET':
        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    # Lógica para listar as OSs pendentes para o técnico logado
                    if user_type == 'tecnico':
                        cursor.execute("""
                            SELECT
                                os.id, os.titulo, os.descricao, os.observacoes, os.status,
                                os.data_criacao, os.veiculo, os.cliente_id, os.equipamento_id,
                                c.nome_cliente, e.nome_equipamento
                            FROM ordens_servico os
                            LEFT JOIN clientes c ON os.cliente_id = c.id
                            LEFT JOIN equipamentos e ON os.equipamento_id = e.id
                            WHERE os.tecnico_id = %s AND os.status = 'pendente'
                            ORDER BY os.data_criacao ASC
                        """, (user_id_logado,))
                    else: # Se for admin, lista todas as OSs pendentes
                        cursor.execute("""
                            SELECT
                                os.id, os.titulo, os.descricao, os.observacoes, os.status,
                                os.data_criacao, os.veiculo, os.cliente_id, os.equipamento_id,
                                c.nome_cliente, e.nome_equipamento
                            FROM ordens_servico os
                            LEFT JOIN clientes c ON os.cliente_id = c.id
                            LEFT JOIN equipamentos e ON os.equipamento_id = e.id
                            WHERE os.status = 'pendente'
                            ORDER BY os.data_criacao ASC
                        """)

                    ordens_servico = []
                    for row in cursor.fetchall():
                        os_dict = {
                            "id": row[0],
                            "titulo": row[1],
                            "descricao": row[2],
                            "observacoes": row[3],
                            "status": row[4],
                            "data_criacao": row[5].isoformat(),
                            "veiculo": row[6],
                            "cliente_id": row[7],
                            "equipamento_id": row[8],
                            "nome_cliente": row[9],
                            "nome_equipamento": row[10]
                        }
                        ordens_servico.append(os_dict)

            return jsonify(ordens_servico)

        except Exception as e:
            print(f"Erro ao listar ordens de serviço: {e}")
            return jsonify({"success": False, "message": "Erro ao listar ordens de serviço."}), 500


@app.route('/agenda_telefonica', methods=['GET', 'POST'])
def agenda_telefonica():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    conn = get_connection()
    # Use um cursor que retorna dicion\u00E1rios
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    mensagem = None
    contatos = [] # Inicializa a lista de contatos para evitar erros
    
    if request.method == 'POST':
        # Verifica se o formul\u00E1rio de inser\u00E7\u00E3o foi enviado
        if 'add_contact' in request.form:
            nome_cliente = request.form['nome_cliente']
            nome_responsavel = request.form['nome_responsavel']
            telefone = request.form['telefone']
            observacoes = request.form['observacoes']
            
            try:
                # A coluna data_criacao \u00E9 inserida automaticamente pelo 'DEFAULT CURRENT_TIMESTAMP' do SQL
                cursor.execute("""
                    INSERT INTO agenda_telefonica (nome_cliente, nome_responsavel, telefone, observacoes)
                    VALUES (%s, %s, %s, %s)
                """, (nome_cliente, nome_responsavel, telefone, observacoes))
                conn.commit()
                session['mensagem_sucesso'] = "Contato adicionado com sucesso!"
            except Exception as e:
                conn.rollback()
                session['mensagem_erro'] = f"Erro ao adicionar contato: {e}"
            
            cursor.close()
            conn.close()
            return redirect(url_for('agenda_telefonica'))

        # L\u00F3gica de filtro aqui...
        elif 'filter_contacts' in request.form:
            filtro_cliente = request.form['filtro_cliente']
            filtro_responsavel = request.form['filtro_responsavel']
            
            # Inclui a coluna data_criacao na consulta
            query = "SELECT id, nome_cliente, nome_responsavel, telefone, observacoes, data_criacao FROM agenda_telefonica WHERE 1=1"
            params = []
            
            if filtro_cliente:
                query += " AND nome_cliente ILIKE %s"
                params.append(f"%{filtro_cliente}%")
            if filtro_responsavel:
                query += " AND nome_responsavel ILIKE %s"
                params.append(f"%{filtro_responsavel}%")
            
            cursor.execute(query, tuple(params))
            contatos = cursor.fetchall()

    # C\u00F3digo para solicita\u00E7\u00F5es GET e processamento dos contatos
    if not contatos: # Se n\u00E3o houverem contatos ap\u00F3s o POST ou for um GET
        mensagem = session.pop('mensagem_sucesso', None) or session.pop('mensagem_erro', None)
        # Adicionamos a coluna 'data_criacao' no SELECT para poder exibir e ordenamos por ela
        cursor.execute("SELECT id, nome_cliente, nome_responsavel, telefone, observacoes, data_criacao FROM agenda_telefonica ORDER BY data_criacao DESC")
        contatos = cursor.fetchall()
    
    # L\u00F3gica para converter o fuso hor\u00E1rio
    if contatos:
        brasil_timezone = pytz.timezone('America/Sao_Paulo')
        for contato in contatos:
            if 'data_criacao' in contato and isinstance(contato['data_criacao'], datetime):
                # Assumimos que o banco de dados armazena o datetime sem fuso (naive).
                # Localizamos como UTC e ent\u00E3o convertemos para o fuso do Brasil.
                # A sua implementa\u00E7\u00E3o real pode variar dependendo de como o banco armazena a data.
                if contato['data_criacao'].tzinfo is None:
                    utc_dt = pytz.utc.localize(contato['data_criacao'])
                    contato['data_criacao'] = utc_dt.astimezone(brasil_timezone)
                else:
                    contato['data_criacao'] = contato['data_criacao'].astimezone(brasil_timezone)
                
                # Formatamos a data e hora para o formato desejado (DD/MM/AAAA - HH:MM)
                contato['data_criacao'] = contato['data_criacao'].strftime("%d/%m/%Y - %H:%M")

    cursor.close()
    conn.close()
    
    return render_template('agenda_telefonica.html',
                           usuario=session['usuario'],
                           tipo=session.get('tipo'),
                           contatos=contatos,
                           mensagem=mensagem)

# Rota para exclus\u00E3o de contato
@app.route('/excluir_contato/<int:contato_id>', methods=['POST'])
def excluir_contato(contato_id):
    if 'usuario' not in session or session.get('tipo') != 'admin':
        session['mensagem_erro'] = "Acesso negado."
        return redirect(url_for('agenda_telefonica'))

    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM agenda_telefonica WHERE id = %s", (contato_id,))
        conn.commit()
        session['mensagem_sucesso'] = "Contato exclu\u00EDdo com sucesso!"
    except Exception as e:
        conn.rollback()
        session['mensagem_erro'] = f"Erro ao excluir contato: {e}"
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('agenda_telefonica'))


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({'message': 'API funcionando!'})
                            
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

    def add_signature_and_date_field(self, technician_name):
        # 'ln=0' mantém a próxima célula na mesma linha
        self.cell(120, 10, 'Assinatura do Técnico: ______________________', 0, 0)
        # Ajuste o primeiro valor (largura) da célula para alinhar como desejar
        self.cell(30, 10, 'Data: _____/_____/________', 0, 1) # 'ln=1' para pular para a próxima linha
    
    def add_signature_adm_and_cupons_field(self):
        self.ln(10) # Adiciona um espaço antes da linha
        # 'ln=0' mantém a próxima célula na mesma linha
        self.cell(110, 10, 'Assinatura do Conferente: ______________________', 0, 0)
        # 'ln=1' para pular para a próxima linha
        self.cell(20, 10, 'Qntd de Cupons Fiscais entregues: _____', 0, 1)       

# ... (Sua função get_and_increment_pdf_counter) ...
def get_and_increment_pdf_counter(fixed_counter_identifier): # Renomeei o parâmetro para maior clareza
    
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # 1. Tentar buscar o contador existente usando a CHAVE FIXA (fixed_counter_identifier)
            # É CRÍTICO usar 'FOR UPDATE' aqui para evitar que múltiplos usuários gerem o mesmo número em concorrência.
            cursor.execute("""
                SELECT last_number FROM pdf_counters
                WHERE counter_key = %s FOR UPDATE; -- <--- AGORA USAMOS A NOVA COLUNA 'counter_key' AQUI!
            """, (fixed_counter_identifier,))
            
            counter_data = cursor.fetchone()

            new_number = 1 
            
            if counter_data:
                last_number = counter_data[0] # Pega o número atual da coluna 'last_number'
                new_number = last_number + 1  # Apenas incrementa em 1 (contagem contínua)
            # Se counter_data não for encontrado, 'new_number' permanece 1 (primeira vez para esta chave)
            
            # 2. Upsert (INSERT OR UPDATE) o contador usando a CHAVE FIXA ('counter_key')
            # 'last_year' e 'last_month' agora servem apenas como registro da última atualização,
            # não mais como um critério para reiniciar o contador.
            cursor.execute("""
                INSERT INTO pdf_counters (counter_key, last_number, last_year, last_month, type)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (counter_key) DO UPDATE SET -- <--- AGORA USAMOS 'counter_key' PARA O ON CONFLICT!
                    last_number = EXCLUDED.last_number,
                    last_year = %s,   -- Atualiza para o ano atual da geração
                    last_month = %s;  -- Atualiza para o mês atual da geração
            """, (fixed_counter_identifier, new_number, datetime.now().year, datetime.now().month, fixed_counter_identifier,
                  datetime.now().year, datetime.now().month))
            # No 'type' do VALUES, você pode colocar o mesmo 'fixed_counter_identifier' se não tiver mais uso.
            # Se 'type' ainda tem uma função descritiva (ex: "Relatório Diário de Viagem"), você precisaria
            # passá-lo como outro parâmetro para a função ou defini-lo aqui.
            # Os dois últimos %s são para os valores de last_year e last_month no DO UPDATE SET
            
            conn.commit()
            
            # Retorna apenas o novo número contínuo.
            # O mês e ano para exibição no PDF devem ser obtidos com datetime.now() na rota,
            # pois não dependem mais da lógica do contador.
            return new_number
        

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

    # --- INÍCIO DA CORREÇÃO: Obter Saldo Atual do Técnico Selecionado (Cálculo Completo) ---
    saldo_atual_tecnico = 0.0 # Valor padrão caso não encontre ou não haja saldo
    if filtro_tecnico_username: # Apenas busca saldo se um técnico foi filtrado
        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    # 1. Buscar a soma total dos valores da tabela 'depositos' para o técnico
                    cursor.execute("SELECT COALESCE(SUM(valor), 0.0) FROM depositos WHERE usuario = %s", (filtro_tecnico_username,))
                    total_depositos = cursor.fetchone()[0]

                    # 2. Buscar a soma total dos valores da tabela 'rdvs' para o técnico
                    cursor.execute("SELECT COALESCE(SUM(CAST(valor AS REAL)), 0.0) FROM rdvs WHERE usuario = %s", (filtro_tecnico_username,))
                    total_rdvs = cursor.fetchone()[0]

                    # 3. Calcular o saldo atual: Depósitos - RDVs (despesas)
                    saldo_atual_tecnico = float(total_depositos) - float(total_rdvs)

                    print(f"DEBUG FLASK: Total Depósitos para '{filtro_tecnico_username}': {total_depositos:.2f}")
                    print(f"DEBUG FLASK: Total RDVs (despesas) para '{filtro_tecnico_username}': {total_rdvs:.2f}")
                    print(f"DEBUG FLASK: Saldo atual CALCULADO para '{filtro_tecnico_username}': {saldo_atual_tecnico:.2f}")
        except Exception as e:
            print(f"DEBUG FLASK: Erro ao calcular saldo do técnico '{filtro_tecnico_username}': {e}")
    # --- FIM DA CORREÇÃO ---

    # --- NOVO: Obter Data Atual de Geração do Relatório ---
    data_geracao_relatorio = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
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

                print(f"DEBUG FLASK: Query SQL final para RDVs: {query}")
                print(f"DEBUG FLASK: Parâmetros da Query para RDVs: {params}")

                cursor.execute(query, tuple(params))
                registros_rdv = cursor.fetchall()

    except Exception as e:
        flash(f'Erro ao gerar PDF de RDV: {e}', 'danger')
        print(f"DEBUG FLASK: Erro ao gerar PDF de RDV: {e}")
        return redirect(url_for('rdv'))

    # --- NOVO: Quantidade de Registros Filtrados ---
    quantidade_registros_filtrados = len(registros_rdv)
    print(f"DEBUG FLASK: Quantidade de registros filtrados: {quantidade_registros_filtrados}")

    # CALCULAR O VALOR TOTAL DOS RDVs AQUI (apenas os RDVs exibidos no relatório)
    total_valor_rdv = 0.0
    print(f"DEBUG: Iniciando cálculo de total_valor_rdv. Registros: {len(registros_rdv) if registros_rdv else 0}")
    if registros_rdv:
        for i, item in enumerate(registros_rdv):
            try:
                valor_float = float(item['valor'])
                total_valor_rdv += valor_float
                print(f"DEBUG: Item {i}: Valor '{item['valor']}' (float: {valor_float}). Total parcial: {total_valor_rdv:.2f}")
            except (ValueError, TypeError) as e:
                print(f"DEBUG: Erro ao converter valor '{item.get('valor', 'N/A')}' para float no item {i}. Pulando item. Erro: {e}")
                pass

    print(f"DEBUG: Total valor RDV calculado FINAL: R$ {total_valor_rdv:.2f}")

    # DEBUG para ver a estrutura dos dados retornados:
    print(f"DEBUG PDF: Registros_rdv para PDF (APÓS CONSULTA): {registros_rdv}")

    # --- INÍCIO DA LÓGICA DE CONTADOR CONTÍNUO ---
    # 1. Defina a chave do contador contínuo (sem mês/ano)
    pdf_number_display = "N/A" # Valor padrão se não for gerado um número sequencial
    report_filename_number_part = "" # Parte do nome do arquivo (sem barras)

    # Use a chave fixa para o contador. Ex: 'rdv_tecnico_henrique' ou 'rdv_global'
    if filtro_tecnico_username:
        # Se um técnico específico foi filtrado, o contador é para ele
        fixed_counter_key = f"rdv_tecnico_{filtro_tecnico_username}"
        # Nome para a exibição no PDF e arquivo
        nome_para_exibicao_no_nome_arquivo = filtro_tecnico_username
    else:
        # Se 'Todos os Técnicos', use uma chave global
        fixed_counter_key = "rdv_global"
        nome_para_exibicao_no_nome_arquivo = "GLOBAL"

    # 2. Chame a função get_and_increment_pdf_counter com a chave fixa
    try:
        pdf_report_number = get_and_increment_pdf_counter(fixed_counter_key)
        
        # 3. Formate o número do relatório e a parte do nome do arquivo
        # O mês e ano agora vêm da data atual de geração do PDF
        current_month_str = datetime.now().strftime('%m')
        current_year_str = datetime.now().strftime('%Y')

        # Formata o número do relatório para exibição no PDF (ex: "001/08-2025")
        pdf_number_display = f"{pdf_report_number:03d}/{current_month_str}-{current_year_str}"
        
        # Para o nome do arquivo (ex: "RDV_henrique_001_08-2025")
        report_filename_number_part = f"{nome_para_exibicao_no_nome_arquivo}_{pdf_report_number:03d}_{current_month_str}-{current_year_str}"
        
    except Exception as e:
        print(f"DEBUG: Erro ao obter/incrementar contador do PDF: {e}")
        flash('Erro ao gerar número sequencial do relatório.', 'danger')
        return redirect(url_for('rdv'))
    
    print(f"DEBUG: Número de PDF gerado para exibição: {pdf_number_display}")
    # --- FIM DA LÓGICA DE CONTADOR CONTÍNUO ---


    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()

    # Usando pdf_number_display no cabeçalho do PDF
    pdf.set_font('Arial', 'B', 15)
    pdf.cell(0, 10, f'Relatório # {pdf_number_display}', 0, 1, 'L')
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, f'Técnico: {nome_tecnico_para_pdf}', 0, 1, 'L')

    # --- NOVO: Adicionar Saldo, Data de Geração e Qtd de Registros no PDF ---
    pdf.set_font('Arial', '', 10) # Fonte menor para essas informações
    # Formata o saldo com vírgula para decimal e ponto para milhar
    pdf.cell(0, 7, f"Saldo Atual: R$ {saldo_atual_tecnico:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), 0, 1, 'L')
    pdf.cell(0, 7, f"Data Fechamento: {data_geracao_relatorio}", 0, 1, 'L')
    pdf.cell(0, 7, f"Quantidade de Registros: {quantidade_registros_filtrados}", 0, 1, 'L')
    # --- FIM NOVO ---

    if filtro_data_inicio_str and filtro_data_fim_str:
        pdf.cell(0, 10, f'Período: {datetime.strptime(filtro_data_inicio_str, "%Y-%m-%d").strftime("%d/%m/%Y")} a {datetime.strptime(filtro_data_fim_str, "%Y-%m-%d").strftime("%d/%m/%Y")}', 0, 1, 'L')
    elif filtro_data_unica_str:
        pdf.cell(0, 10, f'Data Específica: {datetime.strptime(filtro_data_unica_str, "%Y-%m-%d").strftime("%d/%m/%Y")}', 0, 1, 'L')

    pdf.ln(5)

    if registros_rdv:
        print(f"DEBUG: Número de registros encontrados em 'registros_rdv': {len(registros_rdv)}")
        pdf.chapter_body(registros_rdv)

        print(f"DEBUG: Chamando pdf.add_total_summary com valor: {total_valor_rdv:.2f}")
        pdf.add_total_summary(total_valor_rdv)

    else:
        pdf.set_font('Arial', 'I', 12)
        pdf.cell(0, 10, 'Nenhum registro de RDV encontrado para os filtros selecionados.', 0, 1, 'C')

    # A lógica para nome_para_assinatura ajustada para usar chaves do dicionário (DictCursor)
    if filtro_tecnico_username:
        nome_para_assinatura = nome_tecnico_para_pdf # Usa o nome já obtido
    else:
        nome_para_assinatura = "" # Se for "Todos os Técnicos", não preenche a assinatura

    # Essas duas linhas precisam estar aqui, fora do 'if' e 'else'
    pdf.add_signature_and_date_field(nome_para_assinatura)
    pdf.add_signature_adm_and_cupons_field()

    # Passando o nome do técnico/admin logado para a assinatura do conferente
    admin_username = session.get('usuario')
    admin_nome_completo = "N/A"
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT nome FROM usuarios WHERE usuario = %s", (admin_username,))
                resultado = cursor.fetchone()
                if resultado:
                    admin_nome_completo = resultado[0]
    except Exception as e:
        print(f"Erro ao buscar nome do admin para assinatura: {e}")
        admin_nome_completo = "Erro ao carregar nome"

     # Se esta função não precisa de argumentos, a chamada é assim

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
    

# --- CONFIGURAÇÃO DAS CHECKLISTS ---
# Aqui definimos o 'slug' (nome interno para o banco) e o 'label' (o que aparece na tela)
CHECKLIST_CONFIG = {
    'preventiva': [
        {'id': 'horimetro', 'label': 'Horas de Funcionamento'},
        {'id': 'nivel_combustivel', 'label': 'Nível Combustível'},
        {'id': 'temperatura_repouso', 'label': 'Temperatura do Motor em Repouso'},
        {'id': 'nivel_agua', 'label': 'Nível do Líquido de Arrefecimento'},
        {'id': 'nivel_oleo', 'label': 'Nível de Óleo Lubrificante'},
        {'id': 'scanner_baterias', 'label': 'Scanner Baterias'},
        {'id': 'flutuacao_bateria', 'label': 'Tensão de Flutuação das Baterias'},
        {'id': 'minima_baterias', 'label': 'Tensão Mínima das Baterias na Partida'},
        {'id': 'tensao_alternador_bateria', 'label': 'Tensão de Carga do Alternador'},
        {'id': 'funci_vazio', 'label': 'Funcionamento a Vazio'},
        {'id': 'funci_carga', 'label': 'Funcionamento em Carga'},
        {'id': 'pecas_trocadas', 'label': 'Peças Substituídas'}        
    ],
    'corretiva': [
        {'id': 'defeito_relatado', 'label': 'Defeito Relatado pelo Cliente'},
        {'id': 'diagnostico', 'label': 'Diagnóstico Técnico'},
        {'id': 'causa_raiz', 'label': 'Causa Raiz do Problema'},
        {'id': 'pecas_trocadas', 'label': 'Peças Substituídas'}
    ],
    'entrega_tecnica': [
        {'id': 'instalacao', 'label': 'Conferência da Instalação'},
        {'id': 'teste_carga', 'label': 'Teste com Carga (100%)'},
        {'id': 'treinamento', 'label': 'Treinamento do Operador'},
        {'id': 'manual', 'label': 'Entrega do Manual'}
    ]
}



@app.route('/clientes/novo', methods=['GET', 'POST'])
def novo_cliente():
    if 'usuario' not in session: return redirect(url_for('login'))

    if request.method == 'POST':
        nome_cliente = request.form.get('nome_cliente')
        endereco = request.form.get('endereco')
        contato = request.form.get('contato')
        
        # Captura todas as listas do formulário
        # (A ordem dos inputs no HTML garante a ordem das listas)
        nomes = request.form.getlist('equip_nome[]')
        kvas = request.form.getlist('equip_kva[]')
        series = request.form.getlist('equip_serie[]')
        regimes = request.form.getlist('equip_regime[]')
        fabricantes = request.form.getlist('equip_fabricante[]')
        mot_modelos = request.form.getlist('equip_mot_mod[]')
        mot_series = request.form.getlist('equip_mot_ser[]')
        ger_modelos = request.form.getlist('equip_ger_mod[]')
        ger_series = request.form.getlist('equip_ger_ser[]')
        vcc = request.form.getlist('equip_vcc[]')
        vca = request.form.getlist('equip_vca[]')
        controles = request.form.getlist('equip_controle[]')
        tags = request.form.getlist('equip_tag[]')

        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    # 1. Cria Cliente
                    cursor.execute("""
                        INSERT INTO clientes (nome_cliente, endereco, contato) 
                        VALUES (%s, %s, %s) RETURNING id
                    """, (nome_cliente, endereco, contato))
                    novo_id = cursor.fetchone()[0]

                    # 2. Cria Equipamentos Completos
                    for i in range(len(nomes)):
                        if nomes[i]: # Só salva se tiver nome
                            cursor.execute("""
                                INSERT INTO equipamentos 
                                (cliente_id, nome_equipamento, potencia_kva, numero_serie_principal,
                                 regime_operacao, fabricante, motor_modelo, motor_numero_serie,
                                 gerador_modelo, gerador_numero_serie, tensao_vcc_comando,
                                 tensao_vca, unidade_controle, tag)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                novo_id, nomes[i], kvas[i], series[i],
                                regimes[i], fabricantes[i], mot_modelos[i], mot_series[i],
                                ger_modelos[i], ger_series[i], vcc[i],
                                vca[i], controles[i], tags[i]
                            ))
                    
                    conn.commit()
            
            flash('Cliente e Equipamentos cadastrados com sucesso!', 'success')
            return redirect(url_for('listar_clientes'))

        except Exception as e:
            print(f"Erro Cadastro: {e}")
            flash(f'Erro ao cadastrar: {e}', 'danger')
            return redirect(url_for('novo_cliente'))

    return render_template('novo_cliente.html')

@app.route('/clientes')
def listar_clientes():
    if 'usuario' not in session: return redirect(url_for('login'))
    
    termo = request.args.get('busca', '').lower()
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                if termo:
                    cursor.execute("""
                        SELECT * FROM clientes 
                        WHERE LOWER(nome_cliente) LIKE %s OR LOWER(endereco) LIKE %s 
                        ORDER BY nome_cliente
                    """, (f'%{termo}%', f'%{termo}%'))
                else:
                    cursor.execute("SELECT * FROM clientes ORDER BY nome_cliente")
                
                clientes = cursor.fetchall()
                
                # Conta quantos equipamentos cada cliente tem (Opcional, mas legal)
                for c in clientes:
                    cursor.execute("SELECT COUNT(*) as qtd FROM equipamentos WHERE cliente_id = %s", (c['id'],))
                    c['qtd_equips'] = cursor.fetchone()['qtd']

    except Exception as e:
        flash(f'Erro ao listar: {e}', 'danger')
        return redirect(url_for('dashboard'))

    return render_template('clientes_lista.html', clientes=clientes)

@app.route('/clientes/visualizar/<int:cliente_id>')
def visualizar_cliente(cliente_id):
    if 'usuario' not in session: return redirect(url_for('login'))

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Dados do Cliente
                cursor.execute("SELECT * FROM clientes WHERE id = %s", (cliente_id,))
                cliente = cursor.fetchone()
                
                if not cliente:
                    flash('Cliente não encontrado.', 'danger')
                    return redirect(url_for('listar_clientes'))

                # Equipamentos do Cliente
                cursor.execute("""
                    SELECT * FROM equipamentos WHERE cliente_id = %s ORDER BY nome_equipamento
                """, (cliente_id,))
                equipamentos = cursor.fetchall()

        return render_template('visualizar_cliente.html', cliente=cliente, equipamentos=equipamentos)
    except Exception as e:
        flash(f'Erro: {e}', 'danger')
        return redirect(url_for('listar_clientes'))
    

@app.route('/clientes/editar/<int:cliente_id>', methods=['GET', 'POST'])
def editar_cliente(cliente_id):
    if 'usuario' not in session: return redirect(url_for('login'))

    if request.method == 'POST':
        # Dados Cliente
        nome = request.form.get('nome_cliente')
        endereco = request.form.get('endereco')
        contato = request.form.get('contato')
        
        # Deletar removidos
        ids_delete = request.form.getlist('deletar_ids[]')
        
        # Novos Equipamentos (Listas)
        # (Use os mesmos nomes de getlist do novo_cliente aqui para os "novos")
        # Vou simplificar: a lógica de novos usa os mesmos nomes de campo do HTML novo_cliente
        
        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    # A. Atualiza Cliente
                    cursor.execute("UPDATE clientes SET nome_cliente=%s, endereco=%s, contato=%s WHERE id=%s", (nome, endereco, contato, cliente_id))
                    
                    # B. Atualiza Equipamentos EXISTENTES (Iterando form keys)
                    # O form envia chaves tipo: 'existente_nome_55', 'existente_tag_55'
                    # Precisamos identificar quais IDs vieram no form para update
                    
                    # Dica: Vamos pegar todos os IDs presentes no form que começam com 'existente_nome_'
                    ids_existentes = [k.split('_')[2] for k in request.form.keys() if k.startswith('existente_nome_')]
                    
                    for eq_id in ids_existentes:
                        cursor.execute("""
                            UPDATE equipamentos SET
                                nome_equipamento=%s, potencia_kva=%s, numero_serie_principal=%s,
                                regime_operacao=%s, fabricante=%s, motor_modelo=%s, motor_numero_serie=%s,
                                gerador_modelo=%s, gerador_numero_serie=%s, tensao_vcc_comando=%s,
                                tensao_vca=%s, unidade_controle=%s, tag=%s
                            WHERE id=%s
                        """, (
                            request.form.get(f'existente_nome_{eq_id}'),
                            request.form.get(f'existente_kva_{eq_id}'),
                            request.form.get(f'existente_serie_{eq_id}'),
                            request.form.get(f'existente_regime_{eq_id}'),
                            request.form.get(f'existente_fabricante_{eq_id}'),
                            request.form.get(f'existente_mot_mod_{eq_id}'),
                            request.form.get(f'existente_mot_ser_{eq_id}'),
                            request.form.get(f'existente_ger_mod_{eq_id}'),
                            request.form.get(f'existente_ger_ser_{eq_id}'),
                            request.form.get(f'existente_vcc_{eq_id}'),
                            request.form.get(f'existente_vca_{eq_id}'),
                            request.form.get(f'existente_controle_{eq_id}'),
                            request.form.get(f'existente_tag_{eq_id}'),
                            eq_id
                        ))

                    # C. Insere NOVOS Equipamentos
                    novos_nomes = request.form.getlist('equip_nome[]')
                    # ... Pegar as outras listas dos novos (mesma lógica do novo_cliente)
                    # Para simplificar o código aqui, assuma que você pegou todas as listas como no novo_cliente
                    # e faça o loop:
                    
                    kvas = request.form.getlist('equip_kva[]')
                    series = request.form.getlist('equip_serie[]')
                    regimes = request.form.getlist('equip_regime[]')
                    fabricantes = request.form.getlist('equip_fabricante[]')
                    mot_modelos = request.form.getlist('equip_mot_mod[]')
                    mot_series = request.form.getlist('equip_mot_ser[]')
                    ger_modelos = request.form.getlist('equip_ger_mod[]')
                    ger_series = request.form.getlist('equip_ger_ser[]')
                    vcc = request.form.getlist('equip_vcc[]')
                    vca = request.form.getlist('equip_vca[]')
                    controles = request.form.getlist('equip_controle[]')
                    tags = request.form.getlist('equip_tag[]')

                    for i in range(len(novos_nomes)):
                        if novos_nomes[i]:
                            cursor.execute("""
                                INSERT INTO equipamentos 
                                (cliente_id, nome_equipamento, potencia_kva, numero_serie_principal, regime_operacao, fabricante, motor_modelo, motor_numero_serie, gerador_modelo, gerador_numero_serie, tensao_vcc_comando, tensao_vca, unidade_controle, tag)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (cliente_id, novos_nomes[i], kvas[i], series[i], regimes[i], fabricantes[i], mot_modelos[i], mot_series[i], ger_modelos[i], ger_series[i], vcc[i], vca[i], controles[i], tags[i]))

                    # D. Deleta
                    for del_id in ids_delete:
                        if del_id:
                            # Checa vínculo antes de deletar
                            cursor.execute("DELETE FROM equipamentos WHERE id = %s", (del_id,))

                    conn.commit()
            
            flash('Alterações salvas!', 'success')
            return redirect(url_for('listar_clientes'))

        except Exception as e:
            flash(f'Erro: {e}', 'danger')
            return redirect(url_for('editar_cliente', cliente_id=cliente_id))

    # GET
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM clientes WHERE id = %s", (cliente_id,))
                cliente = cursor.fetchone()
                cursor.execute("SELECT * FROM equipamentos WHERE cliente_id = %s ORDER BY nome_equipamento", (cliente_id,))
                equipamentos = cursor.fetchall()
        return render_template('editar_cliente.html', cliente=cliente, equipamentos=equipamentos)
    except:
        return redirect(url_for('listar_clientes'))
    


@app.route('/api/equipamentos/<int:id_do_cliente>')
def api_equipamentos(id_do_cliente):
    # --- PRINT ESPIÃO ---
    print("\n\n>>> A API FOI CHAMADA! <<<")
    print(f"Buscando equipamentos para cliente ID: {id_do_cliente}")
    # --------------------

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Confirme visualmente se esta query está no seu arquivo
                cursor.execute("""
                    SELECT 
                        id, 
                        nome_equipamento,        -- TEM QUE SER ESSE NOME
                        potencia_kva,            -- TEM QUE TER ISSO
                        numero_serie_principal, 
                        motor_modelo
                    FROM equipamentos 
                    WHERE cliente_id = %s 
                    ORDER BY nome_equipamento
                """, (id_do_cliente,))
                
                equipamentos = cursor.fetchall()
                
                # --- PRINT ESPIÃO 2 ---
                print("DADOS ENCONTRADOS NO BANCO:")
                print(equipamentos) # Vai mostrar no terminal o que ele achou
                # ----------------------
        
        return jsonify(equipamentos)
    except Exception as e:
        print(f"ERRO NA API: {e}")
        return jsonify({'error': str(e)}), 500

# Configuração de Upload
UPLOAD_FOLDER = 'static/uploads/os_anexos'
# Cria a pasta se não existir (Isso resolve o problema de não salvar)
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

@app.route('/os/painel')
def os_painel():
    # 1. Verificação de Login
    if 'usuario' not in session:
        flash('Faça login para acessar.', 'danger')
        return redirect(url_for('login'))

    usuario_tipo = session.get('tipo')
    usuario_id = session.get('user_id') 
    
    # 2. Inicializa a lista VAZIA antes de tentar buscar
    # Isso garante que a variável exista mesmo se o banco falhar
    ordens_servico = []

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                
                # Queries (Admin ou Técnico)
                if usuario_tipo == 'admin':
                    cursor.execute("""
                        SELECT 
                            os.id_os,
                            os.data_hora_abertura,
                            os.status,
                            os.descricao,
                            c.nome_cliente,
                            STRING_AGG(u.nome, ', ') as nome_tecnico
                        FROM os 
                        JOIN clientes c ON os.id_cliente = c.id 
                        LEFT JOIN os_tecnicos ot ON os.id_os = ot.id_os
                        LEFT JOIN usuarios u ON ot.id_usuario = u.id
                        GROUP BY os.id_os, c.nome_cliente
                        ORDER BY os.data_hora_abertura DESC;
                    """)
                
                elif usuario_tipo == 'tecnico':
                    cursor.execute("""
                        SELECT DISTINCT
                            os.id_os,
                            os.data_hora_abertura,
                            os.status,
                            os.descricao,
                            c.nome_cliente
                        FROM os 
                        JOIN clientes c ON os.id_cliente = c.id 
                        JOIN os_tecnicos ot ON os.id_os = ot.id_os
                        WHERE ot.id_usuario = %s 
                        ORDER BY os.data_hora_abertura DESC;
                    """, (usuario_id,))
                
                ordens_servico = cursor.fetchall()

    except Exception as e:
        # Se der erro (ex: banco offline), apenas printa no terminal
        print(f"ERRO PAINEL (Possivelmente Offline): {e}")
        # NÃO FAZ REDIRECT. Deixa o código seguir para renderizar a página vazia.
        # O Service Worker vai salvar essa versão vazia se for a primeira vez, 
        # ou se já tiver cache, nem vai chegar aqui.

    # 3. RETORNO OBRIGATÓRIO (Fora do try/except)
    return render_template('os_painel.html', ordens_servico=ordens_servico, user_type=usuario_tipo)

# --- ROTA 2: CRIAR OS (ESSA PRECISAVA SER CORRIGIDA) ---
@app.route('/os/criar', methods=['GET', 'POST'])
def criar_os():
    # ... (verificações de segurança e login mantidas) ...
    tipo_usuario = session.get('tipo', '').lower()
    if 'usuario' not in session or tipo_usuario != 'admin':
        return redirect(url_for('os_painel'))

    if request.method == 'POST':
        # ... (captura dos outros campos) ...
        id_cliente = request.form.get('cliente')
        descricao = request.form.get('descricao')
        prioridade = request.form.get('prioridade')
        
        # NOVO: Captura o veículo
        veiculo = request.form.get('veiculo') 

        tecnicos_selecionados = request.form.getlist('tecnicos[]')
        equipamentos_ids = request.form.getlist('equipamento_id[]')
        tipos_servico = request.form.getlist('tipo_servico_item[]')

        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    # ATUALIZADO: Inclui veiculo_utilizado
                    cursor.execute("""
                        INSERT INTO os (id_cliente, descricao, status, data_hora_abertura, prioridade, veiculo_utilizado)
                        VALUES (%s, %s, 'Aberta', NOW(), %s, %s) RETURNING id_os
                    """, (id_cliente, descricao, prioridade, veiculo))
                    
                    novo_id_os = cursor.fetchone()[0]

                    # ... (Lógica de inserir técnicos e equipamentos continua IGUAL) ...
                    for t_id in tecnicos_selecionados:
                        cursor.execute("INSERT INTO os_tecnicos (id_os, id_usuario) VALUES (%s, %s)", (novo_id_os, t_id))
                    
                    for i in range(len(equipamentos_ids)):
                        cursor.execute("INSERT INTO os_equipamentos (id_os, id_equipamento, tipo_servico) VALUES (%s, %s, %s)", (novo_id_os, equipamentos_ids[i], tipos_servico[i]))
                    
                    conn.commit()
            
            flash('OS criada com sucesso!', 'success')
            return redirect(url_for('os_painel'))
        except Exception as e:
            flash(f'Erro: {e}', 'danger')
            return redirect(url_for('criar_os'))

    # GET: Carregar dados
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT id, nome_cliente FROM clientes ORDER BY nome_cliente")
                clientes = cursor.fetchall()
                cursor.execute("SELECT id, nome FROM usuarios WHERE tipo = 'tecnico' ORDER BY nome")
                tecnicos = cursor.fetchall()
                
                # NOVO: Busca lista de veículos únicos da sua tabela controle_veiculos
                cursor.execute("SELECT DISTINCT placa FROM controle_veiculos ORDER BY placa")
                veiculos = [r['placa'] for r in cursor.fetchall()]

    except Exception as e:
        flash(f'Erro ao carregar dados: {e}', 'danger')
        return redirect(url_for('os_painel'))

    return render_template('criar_os.html', clientes=clientes, tecnicos=tecnicos, veiculos=veiculos)



@app.route('/os/editar/<int:os_id>', methods=['GET', 'POST'])
def editar_os(os_id):
    if 'usuario' not in session: return redirect(url_for('login'))

    # Verifica se é admin
    if session.get('tipo') != 'admin':
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('os_painel'))

    # --- SALVAR ALTERAÇÕES (POST) ---
    if request.method == 'POST':
        descricao = request.form.get('descricao')
        prioridade = request.form.get('prioridade')
        veiculo = request.form.get('veiculo') # Captura o veículo
        
        tecnicos_selecionados = request.form.getlist('tecnicos[]')
        ids_para_deletar = request.form.getlist('deletar_ids[]')
        novos_equips = request.form.getlist('equipamento_id[]')
        novos_tipos = request.form.getlist('tipo_servico_item[]')

        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    
                    # 1. Atualiza dados da OS
                    cursor.execute("""
                        UPDATE os SET descricao=%s, prioridade=%s, veiculo_utilizado=%s WHERE id_os=%s
                    """, (descricao, prioridade, veiculo, os_id))
                    
                    # 2. Atualiza Técnicos (Remove e Insere)
                    cursor.execute("DELETE FROM os_tecnicos WHERE id_os = %s", (os_id,))
                    for t_id in tecnicos_selecionados:
                        cursor.execute("INSERT INTO os_tecnicos (id_os, id_usuario) VALUES (%s, %s)", (os_id, t_id))

                    # 3. Equipamentos (Update Tipos)
                    for key, value in request.form.items():
                        if key.startswith('tipo_existente_'):
                            id_vinculo = key.split('_')[2]
                            cursor.execute("UPDATE os_equipamentos SET tipo_servico = %s WHERE id = %s", (value, id_vinculo))

                    # 4. Equipamentos (Delete)
                    for del_id in ids_para_deletar:
                        if del_id: cursor.execute("DELETE FROM os_equipamentos WHERE id = %s", (del_id,))

                    # 5. Equipamentos (Insert Novos)
                    for i in range(len(novos_equips)):
                        cursor.execute("""
                            INSERT INTO os_equipamentos (id_os, id_equipamento, tipo_servico)
                            VALUES (%s, %s, %s)
                        """, (os_id, novos_equips[i], novos_tipos[i]))
                    
                    conn.commit()
            
            flash('OS Atualizada com sucesso!', 'success')
            return redirect(url_for('os_painel'))
            
        except Exception as e:
            print(f"Erro Edição: {e}")
            flash(f'Erro ao salvar: {e}', 'danger')
            return redirect(url_for('editar_os', os_id=os_id))

    # --- CARREGAR TELA (GET) ---
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                
                # [CORREÇÃO AQUI]: Adicionado JOIN para trazer nome_cliente
                cursor.execute("""
                    SELECT os.*, c.nome_cliente 
                    FROM os 
                    JOIN clientes c ON os.id_cliente = c.id 
                    WHERE os.id_os = %s
                """, (os_id,))
                
                os_dados = cursor.fetchone()
                
                # Se a OS não existir, evita erro
                if not os_dados:
                    flash('OS não encontrada.', 'danger')
                    return redirect(url_for('os_painel'))

                # Técnicos Atuais
                cursor.execute("SELECT id_usuario FROM os_tecnicos WHERE id_os = %s", (os_id,))
                tecnicos_atuais = [row['id_usuario'] for row in cursor.fetchall()]
                
                # Equipamentos Vinculados
                cursor.execute("""
                    SELECT oe.id as vinculo_id, e.nome_equipamento, oe.tipo_servico 
                    FROM os_equipamentos oe 
                    JOIN equipamentos e ON oe.id_equipamento = e.id 
                    WHERE oe.id_os = %s
                    ORDER BY e.nome_equipamento
                """, (os_id,))
                equips_vinculados = cursor.fetchall()
                
                # Listas para Selects
                cursor.execute("SELECT id, nome_cliente FROM clientes ORDER BY nome_cliente")
                clientes = cursor.fetchall()
                
                cursor.execute("SELECT id, nome FROM usuarios WHERE tipo='tecnico' ORDER BY nome")
                todos_tecnicos = cursor.fetchall()
                
                # Equipamentos do Cliente (para adicionar novos)
                cursor.execute("""
                    SELECT id, nome_equipamento, potencia_kva 
                    FROM equipamentos 
                    WHERE cliente_id = %s ORDER BY nome_equipamento
                """, (os_dados['id_cliente'],))
                todos_equips_cliente = cursor.fetchall()

                # Lista de Veículos
                cursor.execute("SELECT DISTINCT placa FROM controle_veiculos ORDER BY placa")
                veiculos = [r['placa'] for r in cursor.fetchall()]

        return render_template('editar_os.html', 
                               os=os_dados, 
                               equips_ja_tem=equips_vinculados, 
                               clientes=clientes, 
                               tecnicos=todos_tecnicos, 
                               tecnicos_atuais=tecnicos_atuais,
                               lista_equips=todos_equips_cliente,
                               veiculos=veiculos)
                               
    except Exception as e:
        print(f"ERRO GET EDITAR: {e}")
        flash(f'Erro ao carregar dados: {e}', 'danger')
        return redirect(url_for('os_painel'))
    

@app.route('/os/visualizar/<int:os_id>')
def visualizar_os(os_id):
    if 'usuario' not in session: return redirect(url_for('login'))
    
    # É praticamente igual ao GET do Editar, mas renderiza outro template
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Dados da OS + Cliente (Já traz o nome)
                cursor.execute("""
                    SELECT os.*, c.nome_cliente 
                    FROM os JOIN clientes c ON os.id_cliente = c.id 
                    WHERE os.id_os = %s
                """, (os_id,))
                os_dados = cursor.fetchone()
                
                # Busca técnicos nomes (string)
                cursor.execute("""
                    SELECT STRING_AGG(u.nome, ', ') as nomes 
                    FROM os_tecnicos ot JOIN usuarios u ON ot.id_usuario = u.id 
                    WHERE ot.id_os = %s
                """, (os_id,))
                tecnicos_nomes = cursor.fetchone()['nomes']
                
                # Equipamentos vinculados
                cursor.execute("""
                    SELECT e.nome_equipamento, oe.tipo_servico 
                    FROM os_equipamentos oe 
                    JOIN equipamentos e ON oe.id_equipamento = e.id 
                    WHERE oe.id_os = %s
                """, (os_id,))
                equipamentos = cursor.fetchall()

        return render_template('visualizar_os.html', os=os_dados, tecnicos=tecnicos_nomes, equipamentos=equipamentos)

    except Exception as e:
        flash(f'Erro ao visualizar: {e}', 'danger')
        return redirect(url_for('os_painel'))    



@app.route('/os/iniciar/<int:os_id>')
def iniciar_os(os_id):
    if 'usuario' not in session: return redirect(url_for('login'))
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # Salva a hora atual e muda o status para 'Em Execução'
                cursor.execute("""
                    UPDATE os 
                    SET data_hora_inicio = NOW(), 
                        status = 'Em Execução' 
                    WHERE id_os = %s AND data_hora_inicio IS NULL
                """, (os_id,))
                
                conn.commit()
                
        flash('Check-in realizado! Bom trabalho.', 'success')
        return redirect(url_for('os_executar', os_id=os_id))
        
    except Exception as e:
        flash(f'Erro ao iniciar: {e}', 'danger')
        return redirect(url_for('os_executar', os_id=os_id))
    
    
# --- ROTA 3: EXECUTAR OS (CORRIGIDA COLUNA NOME) ---
@app.route('/os/executar/<int:os_id>', methods=['GET'])
def os_executar(os_id):
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                
                # 1. Busca Dados da OS + Status
                cursor.execute("""
                    SELECT 
                        os.*, 
                        clientes.nome_cliente,
                        clientes.endereco -- ADICIONADO O ENDEREÇO AQUI
                    FROM os 
                    JOIN clientes ON os.id_cliente = clientes.id
                    WHERE os.id_os = %s
                """, (os_id,))
                dados_os = cursor.fetchone()
                
                if not dados_os:
                    flash('OS não encontrada.', 'danger')
                    return redirect(url_for('os_painel'))
                
                # --- TRAVA DE SEGURANÇA: Se já concluiu, vai para detalhes ---
                if dados_os['status'] == 'Concluída':
                    flash('Esta OS já foi finalizada. Exibindo relatório.', 'info')
                    return redirect(url_for('os_detalhes', os_id=os_id))

                # 2. Busca Equipamentos vinculados e seus tipos de serviço
                # Usamos 'oe.id' como 'item_id' para saber qual checklist estamos preenchendo
                cursor.execute("""
                    SELECT 
                        oe.id as item_id, 
                        oe.tipo_servico,
                        e.* -- Traz todas as colunas (motor_modelo, fabricante, kva, etc)
                    FROM os_equipamentos oe
                    JOIN equipamentos e ON oe.id_equipamento = e.id
                    WHERE oe.id_os = %s
                """, (os_id,))
                equipamentos_os = cursor.fetchall()

        # 3. Monta a estrutura para o HTML (Mescla dados do banco com as perguntas do dicionário)
        lista_execucao = []
        for item in equipamentos_os:
            tipo = item['tipo_servico']
            # Pega as perguntas do dicionário global CHECKLIST_CONFIG
            perguntas = CHECKLIST_CONFIG.get(tipo, CHECKLIST_CONFIG['preventiva'])
            
            # Cria um objeto novo com tudo que o HTML precisa
            item_completo = dict(item)
            item_completo['checklist'] = perguntas
            lista_execucao.append(item_completo)

        return render_template('executar_checklist.html', os=dados_os, lista_execucao=lista_execucao)

    except Exception as e:
        print(f"ERRO EXECUTAR: {e}")
        flash(f'Erro ao abrir OS: {e}', 'danger')
        return redirect(url_for('os_painel'))

# --- 1. CONFIGURAÇÃO ABSOLUTA DO CAMINHO ---
# Isso garante que ele ache a pasta independente de onde você rodar o script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'os_anexos')

# Cria a pasta se não existir
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ... (seus outros códigos) ...

@app.route('/os/finalizar/<int:os_id>', methods=['POST'])
def finalizar_os(os_id):
    if 'usuario' not in session: return redirect(url_for('login'))

    form_data = request.form
    
    try:
        urls_assinaturas = {}

        # 1. PROCESSA ASSINATURAS (Envia para Cloudinary)
        # O Cloudinary aceita a string base64 direto, não precisa salvar arquivo temporário
        for tipo in ['assinatura_tecnico', 'assinatura_cliente']:
            b64_str = form_data.get(f'{tipo}_base64')
            if b64_str and "," in b64_str:
                try:
                    # Envia para a nuvem (pasta 'assinaturas')
                    upload_result = cloudinary.uploader.upload(b64_str, folder="geramaster_assinaturas")
                    urls_assinaturas[tipo] = upload_result['secure_url']
                except Exception as e:
                    print(f"Erro upload assinatura {tipo}: {e}")
                    urls_assinaturas[tipo] = None

        # 2. TRANSAÇÃO NO BANCO
        with get_connection() as conn:
            with conn.cursor() as cursor:
                
                # Atualiza dados da OS (Separando Obs de Ações e Salvando Nomes)
                cursor.execute("""
                    UPDATE os 
                    SET status = 'Concluída', 
                        data_hora_fechamento = NOW(), 
                        acoes = %s,              -- Relatório Técnico
                        observacoes_finais = %s, -- Observações Gerais
                        assinatura_tecnico = %s, -- URL Cloudinary
                        assinatura_cliente = %s, -- URL Cloudinary
                        nome_assinatura_tecnico = %s, -- Nome Digitado
                        nome_assinatura_cliente = %s  -- Nome Digitado
                    WHERE id_os = %s
                """, (
                    form_data.get('servicos_executados'),
                    form_data.get('obs_geral'),
                    urls_assinaturas.get('assinatura_tecnico'),
                    urls_assinaturas.get('assinatura_cliente'),
                    form_data.get('nome_assinatura_tecnico'),
                    form_data.get('nome_assinatura_cliente'),
                    os_id
                ))

                # 3. PROCESSA ITENS DO CHECKLIST E FOTOS
                for key, value in form_data.items():
                    if key.startswith('resposta_'):
                        # Desmonta a chave: resposta_55_horimetro
                        partes = key.split('_')
                        id_vinculo_equip = partes[1]
                        slug_pergunta = "_".join(partes[2:])
                        item_nome_db = slug_pergunta.replace('_', ' ').title()

                        # Busca ID real do equipamento para vincular corretamente
                        cursor.execute("SELECT id_equipamento FROM os_equipamentos WHERE id = %s", (id_vinculo_equip,))
                        res_eq = cursor.fetchone()
                        
                        if res_eq:
                            equip_real_id = res_eq[0]
                            
                            # Insere a Resposta de Texto
                            cursor.execute("""
                                INSERT INTO itens_checklist_resposta (id_os, id_equipamento, item_nome, resposta_texto) 
                                VALUES (%s, %s, %s, %s) RETURNING id_resposta
                            """, (os_id, equip_real_id, item_nome_db, value))
                            
                            id_resposta = cursor.fetchone()[0]

                            # --- PROCESSA FOTOS DO ITEM (Cloudinary) ---
                            key_foto = f"foto_{id_vinculo_equip}_{slug_pergunta}"
                            arquivos = request.files.getlist(key_foto)
                            
                            for file in arquivos:
                                if file and file.filename != '':
                                    try:
                                        # Upload direto do objeto arquivo para o Cloudinary
                                        upload_result = cloudinary.uploader.upload(file, folder="geramaster_evidencias")
                                        url_web = upload_result['secure_url']

                                        # Salva link na tabela de fotos
                                        cursor.execute("""
                                            INSERT INTO checklist_fotos (id_resposta, url_foto) 
                                            VALUES (%s, %s)
                                        """, (id_resposta, url_web))
                                    except Exception as e:
                                        print(f"Erro upload foto item {item_nome_db}: {e}")

                            # Marca o item como realizado na tabela de vinculo
                            cursor.execute("UPDATE os_equipamentos SET checklist_realizado = TRUE WHERE id = %s", (id_vinculo_equip,))

                conn.commit()

        flash('OS Finalizada e Sincronizada com Sucesso!', 'success')
        
        # Redireciona direto para o relatório para conferência
        return redirect(url_for('os_detalhes', os_id=os_id))

    except Exception as e:
        print(f"ERRO CRÍTICO FINALIZAR: {e}")
        flash(f'Erro ao finalizar OS: {e}', 'danger')
        return redirect(url_for('os_executar', os_id=os_id))
    


def mock_upload_image_to_host(file_storage: FileStorage, os_id: int, item_key: str) -> str | None:
    """
    SIMULAÇÃO: Salva o arquivo localmente e retorna uma URL estática (interna).
    Esta função substitui a lógica do GCS/Imgur para testes.
    """
    
    if not file_storage or file_storage.filename == '':
        return None
    
    try:
        # Garante um nome de arquivo seguro e único
        filename = secure_filename(file_storage.filename)
        
        # Cria um nome único com timestamp para evitar colisões
        unique_filename = f"os_{os_id}_{item_key}_{int(time.time())}_{filename}"
        
        # Caminho completo onde o arquivo será salvo
        save_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        # Salva o arquivo no disco local
        file_storage.save(save_path)
        
        # Retorna a URL ESTÁTICA que o seu template HTML usará para exibir a foto
        # (Ex: /static/uploads/os_anexos/os_1_foto_horimetro_...jpg)
        return f"/{UPLOAD_FOLDER}/{unique_filename}"
        
    except Exception as e:
        print(f"DEBUG: Erro ao salvar arquivo localmente para teste: {e}")
        return None
    

@app.route('/os/detalhes/<int:os_id>')
def os_detalhes(os_id):
    if 'usuario' not in session: return redirect(url_for('login'))

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # 1. Dados da OS (Formatados)
                cursor.execute("""
                    SELECT 
                        os.*, 
                        c.nome_cliente,
                        -- Pega nomes dos técnicos numa string
                        (SELECT STRING_AGG(u.nome, ', ') FROM os_tecnicos ot JOIN usuarios u ON ot.id_usuario = u.id WHERE ot.id_os = os.id_os) as nomes_tecnicos
                    FROM os 
                    JOIN clientes c ON os.id_cliente = c.id
                    WHERE os.id_os = %s
                """, (os_id,))
                os_dados = cursor.fetchone()

                # 2. Checklist Agrupado com Fotos
                cursor.execute("""
                    SELECT 
                        e.nome_equipamento,
                        r.item_nome,
                        r.resposta_texto,
                        COALESCE(json_agg(f.url_foto) FILTER (WHERE f.url_foto IS NOT NULL), '[]') as fotos
                    FROM itens_checklist_resposta r
                    JOIN equipamentos e ON r.id_equipamento = e.id
                    LEFT JOIN checklist_fotos f ON r.id_resposta = f.id_resposta
                    WHERE r.id_os = %s
                    GROUP BY e.nome_equipamento, r.item_nome, r.resposta_texto, r.id_resposta
                    ORDER BY e.nome_equipamento, r.id_resposta
                """, (os_id,))
                itens = cursor.fetchall()
                
                # Agrupa por equipamento
                relatorio = {}
                for i in itens:
                    eq = i['nome_equipamento']
                    if eq not in relatorio: relatorio[eq] = []
                    relatorio[eq].append(i)

        return render_template('os_detalhes.html', os=os_dados, relatorio=relatorio)
    except Exception as e:
        flash(f'Erro: {e}', 'danger')
        return redirect(url_for('os_painel'))
    
@app.route('/service-worker.js')
def service_worker():
    # O arquivo real continua em static/, mas o navegador vai achar que está na raiz
    return send_from_directory('static', 'service-worker.js')




from flask import jsonify, request

@app.route('/api/registrar_nfc', methods=['POST'])
def registrar_nfc():
    print("\n--- INICIANDO REGISTRO NFC ---")
    
    # 1. Checa Login
    if 'usuario' not in session:
        print("❌ Erro: Usuário não logado na sessão.")
        return jsonify({'erro': 'Usuário não logado'}), 401

    # 2. Recebe dados
    data_json = request.get_json()
    print(f"📡 Dados Recebidos do JS: {data_json}")

    usuario = session['usuario']
    tipo = data_json.get('tipo_registro')
    
    # Validação extra para garantir que o tipo veio
    if not tipo:
        print("❌ Erro: Tipo de registro veio vazio.")
        return jsonify({'erro': 'Tipo de registro não informado'}), 400

    print(f"👤 Usuário: {usuario} | Tipo: {tipo}")

    # 3. Prepara Dados
    agora = datetime.now(fuso_brasilia)
    hora = agora.strftime('%H:%M:%S')
    data_hoje = agora.strftime('%Y-%m-%d')
    latitude = data_json.get('latitude')
    longitude = data_json.get('longitude')

    try:
        print("💾 Tentando conectar ao banco...")
        with get_connection() as conn:
            with conn.cursor() as cursor:
                query = "INSERT INTO registros (usuario, tipo_registro, data, hora, latitude, longitude) VALUES (%s, %s, %s, %s, %s, %s)"
                print(f"📝 Executando Query: {query}")
                print(f"📦 Valores: {usuario}, {tipo}, {data_hoje}, {hora}, {latitude}, {longitude}")
                
                cursor.execute(query, (usuario, tipo, data_hoje, hora, latitude, longitude))
            conn.commit()
            print("✅ COMMIT REALIZADO COM SUCESSO!")
        
        return jsonify({'sucesso': True, 'mensagem': f'Ponto ({tipo}) registrado às {hora}!'})

    except Exception as e:
        print(f"❌ ERRO CRÍTICO NO BANCO: {str(e)}")
        # Importante: rollback em caso de erro para não travar o banco
        if 'conn' in locals():
            conn.rollback()
        return jsonify({'erro': str(e)}), 500


@app.route('/admin/veiculos')
def admin_veiculos():
    # Segurança: Apenas Admin
    if 'usuario' not in session or session.get('tipo') != 'admin':
        flash('Acesso restrito a administradores.', 'danger')
        return redirect(url_for('login'))

    veiculos = []
    try:
        with get_connection() as conn:
            # DictCursor permite chamar no HTML por nome: veiculo['placa']
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                cursor.execute("SELECT * FROM veiculos ORDER BY modelo, placa")
                veiculos = cursor.fetchall()
    except Exception as e:
        flash(f'Erro ao listar veículos: {e}', 'danger')

    return render_template('admin_veiculos_lista.html', veiculos=veiculos)

# --- FORMULÁRIO (NOVO E EDITAR) ---
@app.route('/admin/veiculo/form', methods=['GET', 'POST'])
@app.route('/admin/veiculo/form/<int:id>', methods=['GET', 'POST'])
def admin_veiculo_form(id=None):
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    veiculo = None
    
    # Se for GET e tiver ID, busca os dados para preencher o formulário (Edição)
    if request.method == 'GET' and id:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                cursor.execute("SELECT * FROM veiculos WHERE id = %s", (id,))
                veiculo = cursor.fetchone()

    # Se for POST (Salvar)
    if request.method == 'POST':
        placa = request.form['placa'].upper().strip() # Placa sempre maiúscula
        modelo = request.form['modelo']
        ativo = True if 'ativo' in request.form else False # Checkbox retorna 'on' ou nada

        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    if id:
                        # UPDATE (Editar)
                        cursor.execute("""
                            UPDATE veiculos 
                            SET placa=%s, modelo=%s, ativo=%s 
                            WHERE id=%s
                        """, (placa, modelo, ativo, id))
                        flash('Veículo atualizado com sucesso!', 'success')
                    else:
                        # INSERT (Novo)
                        cursor.execute("""
                            INSERT INTO veiculos (placa, modelo, ativo) 
                            VALUES (%s, %s, %s)
                        """, (placa, modelo, ativo))
                        flash('Veículo cadastrado com sucesso!', 'success')
            
            return redirect(url_for('admin_veiculos'))
            
        except Exception as e:
            flash(f'Erro ao salvar veículo: {e}', 'danger')

    return render_template('admin_veiculo_form.html', veiculo=veiculo)

# --- EXCLUIR VEÍCULO ---
@app.route('/admin/veiculo/excluir/<int:id>')
def admin_veiculo_excluir(id):
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM veiculos WHERE id = %s", (id,))
                conn.commit()
        flash('Veículo excluído.', 'info')
    except Exception as e:
        flash(f'Erro ao excluir (verifique se não há abastecimentos vinculados): {e}', 'danger')

    return redirect(url_for('admin_veiculos'))



@app.route('/admin/usuarios')
def admin_usuarios():
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    usuarios = []
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                cursor.execute("SELECT * FROM usuarios ORDER BY nome")
                usuarios = cursor.fetchall()
    except Exception as e:
        flash(f'Erro ao carregar usuários: {e}', 'danger')

    return render_template('admin_usuarios_lista.html', usuarios=usuarios)

# --- FORMULÁRIO USUÁRIO (NOVO E EDITAR) ---
@app.route('/admin/usuario/form', methods=['GET', 'POST'])
@app.route('/admin/usuario/form/<int:id>', methods=['GET', 'POST'])
def admin_usuario_form(id=None):
    # Verificação de segurança
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    usuario_edit = None

    # GET: Busca dados para preencher o formulário se for edição
    if request.method == 'GET' and id:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                cursor.execute("SELECT * FROM usuarios WHERE id = %s", (id,))
                usuario_edit = cursor.fetchone()

    # POST: Salvar no banco
    if request.method == 'POST':
        nome = request.form['nome']
        usuario_login = request.form['usuario']
        tipo = request.form['tipo'] 
        
        # POST: Salvar no banco
    if request.method == 'POST':
        nome = request.form['nome']
        usuario_login = request.form['usuario']
        tipo = request.form['tipo'] 
        
        # --- CORREÇÃO DO ERRO "None" ---
        bling_id_input = request.form['bling_id'].strip() # Remove espaços em branco
        
        if not bling_id_input: # Se estiver vazio ("")
            bling_id = None    # Python None (vira NULL no banco)
        else:
            try:
                bling_id = int(bling_id_input) # Tenta converter para número
            except ValueError:
                bling_id = None # Se digitar texto (ex: "abc"), vira NULL para não quebrar
            
        senha_raw = request.form['senha'] 

        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    if id:
                        # --- EDITAR ---
                        if senha_raw:
                            cursor.execute("""
                                UPDATE usuarios 
                                SET nome=%s, usuario=%s, tipo=%s, bling_id=%s, senha=%s
                                WHERE id=%s
                            """, (nome, usuario_login, tipo, bling_id, senha_raw, id))
                        else:
                            cursor.execute("""
                                UPDATE usuarios 
                                SET nome=%s, usuario=%s, tipo=%s, bling_id=%s
                                WHERE id=%s
                            """, (nome, usuario_login, tipo, bling_id, id))
                        flash('Usuário atualizado!', 'success')
                    
                    else:
                        # --- NOVO ---
                        if not senha_raw:
                            flash('Senha obrigatória!', 'warning')
                            return render_template('admin_usuario_form.html', usuario=None)
                        
                        cursor.execute("""
                            INSERT INTO usuarios (nome, usuario, senha, tipo, bling_id) 
                            VALUES (%s, %s, %s, %s, %s)
                        """, (nome, usuario_login, senha_raw, tipo, bling_id))
                        flash('Usuário criado!', 'success')
                
                conn.commit()
            return redirect(url_for('admin_usuarios'))

        except Exception as e:
            flash(f'Erro ao salvar usuário: {e}', 'danger')
            print(f"DEBUG ERRO: {e}") # Ajuda a ver o erro no terminal

    return render_template('admin_usuario_form.html', usuario=usuario_edit)

# --- EXCLUIR USUÁRIO ---
@app.route('/admin/usuario/excluir/<int:id>')
def admin_usuario_excluir(id):
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM usuarios WHERE id = %s", (id,))
                conn.commit()
        flash('Usuário excluído.', 'info')
    except Exception as e:
        flash(f'Erro: {e}', 'danger')

    return redirect(url_for('admin_usuarios'))


# --- INICIALIZAÇÃO DA APLICAÇÃO ---
if __name__ == '__main__':
    # MUDANÇA AQUI: port=5001
    app.run(debug=True, port=5000)
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