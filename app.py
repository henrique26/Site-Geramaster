from flask import Flask, render_template, request, redirect, url_for, session
import pytz
from datetime import datetime
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
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')



            usuarios = [
                ('admin', '1234', 'admin', 'Administrador'),
                ('henrique', 'henrique1234', 'tecnico', 'Henrique Antunes Fonseca'),
                ('Euler', 'euler1234', 'tecnico', 'Euler Mendes Pena Silva'),
                ('Alexon', 'alexon1234', 'tecnico','Alexon Braz de Deus'),
                ('Carlos', 'carlos1234', 'tecnico', 'Carlos Alexandre de Oliveira'),
                ('ivan', 'ivan1234', 'admin', 'Ivan Chagas Miranda'),
                ('Wallace', 'wallace1234', 'admin', 'Afonso Wallace da Silva')
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
                cursor.execute("SELECT * FROM usuarios WHERE usuario = %s AND senha = %s", (usuario, senha))
                user = cursor.fetchone()
                if user:
                    session['usuario'] = user[1]
                    session['tipo'] = user[3]
                    session['nome'] = user[4]  # pega direto do banco, aqui tá certo
                    return redirect(url_for('menu'))
    return render_template('login.html')


@app.route('/menu')
def menu():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    return render_template('menu.html', usuario=session['usuario'], tipo=session['tipo'])

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
            with get_connection() as conn:
                with conn.cursor() as cursor:
                 cursor.execute("""
                UPDATE pendencias_os 
                SET status = 'Resolvida', concluido_por = %s 
                WHERE id = %s
            """, (usuario, id_pendencia))
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
                SELECT p.id, p.numero_os, p.data, p.cliente, p.pendencia, p.status, u.usuario, p.concluido_por
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




@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)

    app.run(debug=True, host='0.0.0.0')
