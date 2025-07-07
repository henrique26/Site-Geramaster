from flask import Flask, render_template, request, redirect, url_for, session
import pytz
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor  # opcional para fetch como dict
from flask import send_file, flash
from fpdf import FPDF
import io
from collections import Counter

def get_connection():
    return psycopg2.connect(
        dbname="geramaster_db",
        user="geramaster_db_user",
        password="TJ30dPQcC78FISPHI0QLRotCOeskemab",
        host="dpg-d0lrj9umcj7s73891d70-a.oregon-postgres.render.com",  # ou o host do render
        port="5432"
    )

app = Flask(__name__)
app.secret_key = 'segredo'

fuso_brasilia = pytz.timezone('America/Sao_Paulo')

# Inicializa o banco - ajustado para PostgreSQL
def init_db():
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

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS registros (
                    id SERIAL PRIMARY KEY,
                    usuario TEXT NOT NULL,
                    tipo_registro TEXT NOT NULL,
                    data DATE NOT NULL,
                    hora TEXT NOT NULL
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
                    data_conclusao TIMESTAMP 
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



            usuarios = [
                ('admin', '1234', 'admin', 'Administrador'),
                ('henrique', 'henrique1234', 'tecnico', 'Henrique Antunes Fonseca'),
                ('Euler', 'euler1234', 'tecnico', 'Euler Mendes Pena Silva'),
                ('Alexon', 'alexon1234', 'tecnico','Alexon Braz de Deus'),
                ('Carlos', 'carlos1234', 'tecnico', 'Carlos Alexandre de Oliveira'),
                ('ivan', 'ivan1234', 'admin', 'Ivan Chagas Miranda'),
                ('Wallace', 'wallace1234', 'admin', 'Afonso Wallace da Silva'),
                ('Gestor', 'gera1234', 'admin', 'Geramaster Gestor')
            ]

            for usuario, senha, tipo, nome in usuarios:
                 cursor.execute("SELECT * FROM usuarios WHERE usuario = %s", (usuario,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO usuarios (usuario, senha, tipo, nome) VALUES (%s, %s, %s, %s)", 
                    (usuario, senha, tipo, nome))


            conn.commit()




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




@app.route('/menu')
def menu():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    alertas = session.pop('alertas', []) if session.get('tipo') == 'admin' else []
    avisos_troca_oleo = []

    with get_connection() as conn:
        with conn.cursor() as cursor:
            # 1. Obter o último KM geral para cada placa, independentemente do tipo de registro
            cursor.execute("""
                WITH UltimosKMsGerais AS (
                    SELECT
                        placa,
                        MAX(km) AS ultimo_km_geral,
                        MAX(data) AS ultima_data_geral
                    FROM controle_veiculos
                    GROUP BY placa
                ),
                -- 2. Obter o KM da próxima troca de óleo do registro de troca de óleo mais recente para cada placa
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
                WHERE uto.rn = 1; -- Seleciona apenas a última troca de óleo para cada placa
            """)
            resultados = cursor.fetchall()

            for placa, ultimo_km_geral, proxima_troca_km in resultados:
                if proxima_troca_km is not None:
                    km_restante = proxima_troca_km - ultimo_km_geral
                    if km_restante <= 1000:
                        avisos_troca_oleo.append({
                            'placa': placa,
                            'km_restante': km_restante
                        })

    return render_template('menu.html',
                           usuario=session['usuario'],
                           tipo=session['tipo'],
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

    if request.method == 'POST' and tipo_usuario in ['tecnico', 'admin']:
        valor = float(request.form['valor'].replace(',', '.'))
        descricao = request.form['descricao']
        data = request.form['data']

        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO rdvs (usuario, valor, descricao, data) VALUES (%s, %s, %s, %s)",
                               (usuario, valor, descricao, data))
                conn.commit()
        return redirect(url_for('rdv'))

    filtro_usuario = request.args.get('filtro_usuario', '')
    filtro_data = request.args.get('filtro_data', '')
    filtro_data_inicio = request.args.get('filtro_data_inicio', '')
    filtro_data_fim = request.args.get('filtro_data_fim', '')

    with get_connection() as conn:
        with conn.cursor() as cursor:
            if tipo_usuario == 'admin':
                query = "SELECT * FROM rdvs WHERE TRUE"
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

                query += " ORDER BY data DESC"
                cursor.execute(query, params)
                rdvs = cursor.fetchall()

                cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico'")
                tecnicos = [row[0] for row in cursor.fetchall()]

                saldos = {}
                for tecnico in tecnicos:
                    cursor.execute("SELECT COALESCE(SUM(valor),0) FROM depositos WHERE usuario = %s", (tecnico,))
                    total_depositos = cursor.fetchone()[0]
                    cursor.execute("SELECT COALESCE(SUM(CAST(valor AS REAL)),0) FROM rdvs WHERE usuario = %s", (tecnico,))
                    total_rdvs = cursor.fetchone()[0]

                    saldo = float(total_depositos) - float(total_rdvs)
                    saldos[tecnico] = saldo

                ultimo_deposito = None  # Admin não vê último depósito

            else:
                query = "SELECT * FROM rdvs WHERE usuario = %s"
                params = [usuario]

                if filtro_data_inicio and filtro_data_fim:
                    query += " AND data BETWEEN %s AND %s"
                    params.append(filtro_data_inicio)
                    params.append(filtro_data_fim)
                elif filtro_data:
                    query += " AND data = %s"
                    params.append(filtro_data)

                query += " ORDER BY data DESC"
                cursor.execute(query, params)
                rdvs = cursor.fetchall()
                tecnicos = []

                cursor.execute("SELECT COALESCE(SUM(valor),0) FROM depositos WHERE usuario = %s", (usuario,))
                total_depositos = cursor.fetchone()[0]
                cursor.execute("SELECT COALESCE(SUM(CAST(valor AS REAL)),0) FROM rdvs WHERE usuario = %s", (usuario,))
                total_rdvs = cursor.fetchone()[0]

                saldo = float(total_depositos) - float(total_rdvs)
                saldos = {}

                # CONSULTA DO ÚLTIMO DEPÓSITO AQUI
                cursor.execute(
                    "SELECT valor, data FROM depositos WHERE usuario = %s ORDER BY data DESC LIMIT 1", (usuario,)
                )
                result = cursor.fetchone()
                if result:
                    ultimo_deposito = {'valor': float(result[0]), 'data': result[1]}
                else:
                    ultimo_deposito = None

    return render_template('rdv.html', rdvs=rdvs, tipo=tipo_usuario, usuario=usuario,
                           tecnicos=tecnicos, filtro_usuario=filtro_usuario,
                           filtro_data=filtro_data, filtro_data_inicio=filtro_data_inicio,
                           filtro_data_fim=filtro_data_fim, is_admin=(tipo_usuario == 'admin'),
                           saldo=saldo if tipo_usuario != 'admin' else None,
                           saldos=saldos if tipo_usuario == 'admin' else {},
                           ultimo_deposito=ultimo_deposito)


from datetime import datetime

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

from flask import request

from flask import send_file
from io import BytesIO

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


# ... (suas importações e código existentes) ...

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




@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)

    app.run(debug=True, host='0.0.0.0')
