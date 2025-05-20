from flask import Flask, render_template, request, redirect, url_for, session
import pytz
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor  # opcional para fetch como dict

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
                    tipo TEXT NOT NULL
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

            usuarios = [
                ('admin', '1234', 'admin'),
                ('henrique', 'henrique1234', 'tecnico'),
                ('euler', 'euler1234', 'tecnico'),
                ('alexon', 'alexon1234', 'tecnico'),
                ('carlos', 'carlos1234', 'tecnico'),
                ('ivan', 'ivan1234', 'admin'),
                ('wallace', 'wallace1234', 'admin')
            ]

            for usuario, senha, tipo in usuarios:
                cursor.execute("SELECT * FROM usuarios WHERE usuario = %s", (usuario,))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO usuarios (usuario, senha, tipo) VALUES (%s, %s, %s)",
                                   (usuario, senha, tipo))

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

    return render_template('rdv.html', rdvs=rdvs, tipo=tipo_usuario, usuario=usuario,
                           tecnicos=tecnicos, filtro_usuario=filtro_usuario,
                           filtro_data=filtro_data, filtro_data_inicio=filtro_data_inicio,
                           filtro_data_fim=filtro_data_fim, is_admin=(tipo_usuario == 'admin'),
                           saldo=saldo if tipo_usuario != 'admin' else None,
                           saldos=saldos if tipo_usuario == 'admin' else {})

@app.route('/deposito', methods=['GET', 'POST'])
def deposito():
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        usuario = request.form['usuario']
        valor = float(request.form['valor'])
        data = request.form['data']

        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO depositos (usuario, valor, data) VALUES (%s, %s, %s)",
                               (usuario, valor, data))
                conn.commit()
        return redirect(url_for('deposito'))

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico'")
            tecnicos = [row[0] for row in cursor.fetchall()]

            cursor.execute("SELECT id, valor, data, usuario FROM depositos ORDER BY data DESC")
            depositos = cursor.fetchall()

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
        elif acao == 'registrar_manual':
            tipo = request.form['tipo_registro_manual']
            data = request.form['data']
            hora = request.form['hora']
        else:
            return redirect(url_for('dashboard'))

        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO registros (usuario, tipo_registro, data, hora) VALUES (%s, %s, %s, %s)",
                               (usuario, tipo, data, hora))
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

                query += " ORDER BY data DESC, hora DESC"
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

                query += " ORDER BY data DESC, hora DESC"
                cursor.execute(query, params)
                registros = cursor.fetchall()
                tecnicos = []

    return render_template('dashboard.html', registros=registros, usuario=usuario, tipo=tipo_usuario,
                           tecnicos=tecnicos, filtro_usuario=filtro_usuario, filtro_data=filtro_data,
                           filtro_data_inicio=filtro_data_inicio, filtro_data_fim=filtro_data_fim,
                           is_admin=(tipo_usuario == 'admin'))

@app.route('/excluir/<int:id>', methods=['POST'])
def excluir_registro(id):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM registros WHERE id = %s", (id,))
            conn.commit()

    return redirect(url_for('dashboard'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)

    app.run(debug=True, host='0.0.0.0')
