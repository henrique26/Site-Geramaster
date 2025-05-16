from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import pytz
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'segredo'

fuso_brasilia = pytz.timezone('America/Sao_Paulo')
agora = datetime.now(fuso_brasilia)

# Inicializa o banco
def init_db():
    with sqlite3.connect('banco.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS usuarios ( 
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            tipo TEXT NOT NULL
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            tipo_registro TEXT NOT NULL,
            data TEXT NOT NULL,
            hora TEXT NOT NULL
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS rdvs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            valor TEXT NOT NULL,
            descricao TEXT NOT NULL,
            data TEXT NOT NULL
        )''')

        usuarios = [
            ('admin', '1234', 'admin'),
            ('henrique', 'henrique1234', 'tecnico'),
            ('euler', 'euler1234', 'tecnico'),
            ('alexon', 'alexon1234', 'tecnico'),
            ('carlos', 'carlos1234', 'tecnico')
        ]

        for usuario, senha, tipo in usuarios:
            cursor.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO usuarios (usuario, senha, tipo) VALUES (?, ?, ?)", (usuario, senha, tipo))

        conn.commit()

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        senha = request.form['senha']
        with sqlite3.connect('banco.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM usuarios WHERE usuario = ? AND senha = ?", (usuario, senha))
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

@app.route('/rdv', methods=['GET', 'POST'])
def rdv():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    tipo_usuario = session.get('tipo')
    usuario = session.get('usuario')

    # Registro de novo RDV (se for técnico)
    if request.method == 'POST' and tipo_usuario == 'tecnico':
        valor = request.form['valor']
        descricao = request.form['descricao']
        data = request.form['data']

        with sqlite3.connect('banco.db') as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO rdvs (usuario, valor, descricao, data) VALUES (?, ?, ?, ?)",
                           (usuario, valor, descricao, data))
            conn.commit()
        return redirect(url_for('rdv'))

    # Filtros
    filtro_usuario = request.args.get('filtro_usuario', '')
    filtro_data = request.args.get('filtro_data', '')

    with sqlite3.connect('banco.db') as conn:
        cursor = conn.cursor()

        if tipo_usuario == 'admin':
            query = "SELECT * FROM rdvs WHERE 1=1"
            params = []

            if filtro_usuario:
                query += " AND usuario = ?"
                params.append(filtro_usuario)
            if filtro_data:
                query += " AND data = ?"
                params.append(filtro_data)

            query += " ORDER BY data DESC"
            cursor.execute(query, params)
            rdvs = cursor.fetchall()

            # Pega lista de técnicos para o filtro
            cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico'")
            tecnicos = [row[0] for row in cursor.fetchall()]
        else:
            query = "SELECT * FROM rdvs WHERE usuario = ?"
            params = [usuario]
            if filtro_data:
                query += " AND data = ?"
                params.append(filtro_data)
            query += " ORDER BY data DESC"
            cursor.execute(query, params)
            rdvs = cursor.fetchall()
            tecnicos = []

    return render_template('rdv.html', rdvs=rdvs, tipo=tipo_usuario,
                           tecnicos=tecnicos, filtro_usuario=filtro_usuario, filtro_data=filtro_data)


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
            fuso_brasilia = pytz.timezone('America/Sao_Paulo')
            agora = datetime.now(fuso_brasilia)
            hora = agora.strftime('%H:%M:%S')
            data = agora.strftime('%Y-%m-%d')
        elif acao == 'registrar_manual':
            tipo = request.form['tipo_registro_manual']
            data = request.form['data']
            hora = request.form['hora']
        else:
            return redirect(url_for('dashboard'))

        with sqlite3.connect('banco.db') as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO registros (usuario, tipo_registro, data, hora) VALUES (?, ?, ?, ?)",
                           (usuario, tipo, data, hora))
            conn.commit()
        return redirect(url_for('dashboard'))

    filtro_data = request.args.get('filtro_data', '')
    filtro_usuario = request.args.get('filtro_usuario', '')

    registros = []
    with sqlite3.connect('banco.db') as conn:
        cursor = conn.cursor()
        if tipo_usuario == 'admin':
            query = "SELECT usuario, tipo_registro, data, hora, id FROM registros WHERE 1=1"
            params = []

            if filtro_data:
                query += " AND data = ?"
                params.append(filtro_data)

            if filtro_usuario:
                query += " AND usuario = ?"
                params.append(filtro_usuario)

            query += " ORDER BY data DESC, hora DESC"
            cursor.execute(query, params)
            registros = cursor.fetchall()
        else:
            query = "SELECT tipo_registro, data, hora, id FROM registros WHERE usuario = ?"
            params = [usuario]
            if filtro_data:
                query += " AND data = ?"
                params.append(filtro_data)
            query += " ORDER BY data DESC, hora DESC"
            cursor.execute(query, params)
            registros = cursor.fetchall()

    return render_template('dashboard.html', usuario=usuario, tipo=tipo_usuario, registros=registros)

@app.route('/admin', methods=['GET'])
def admin_dashboard():
    if 'usuario' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    filtro_data = request.args.get('filtro_data', '')
    filtro_usuario = request.args.get('filtro_usuario', '')

    with sqlite3.connect('banco.db') as conn:
        cursor = conn.cursor()

        query = "SELECT usuario, tipo_registro, data, hora FROM registros WHERE 1=1"
        params = []

        if filtro_data:
            query += " AND data = ?"
            params.append(filtro_data)

        if filtro_usuario:
            query += " AND usuario = ?"
            params.append(filtro_usuario)

        query += " ORDER BY data DESC, hora DESC"
        cursor.execute(query, params)
        registros = cursor.fetchall()

        cursor.execute("SELECT usuario FROM usuarios WHERE tipo = 'tecnico'")
        tecnicos = [row[0] for row in cursor.fetchall()]

    return render_template('admin_dashboard.html', registros=registros, tecnicos=tecnicos)

@app.route('/excluir/<int:id>')
def excluir_registro(id):
    if 'usuario' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect('banco.db') as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM registros WHERE id = ?", (id,))
        conn.commit()

    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0')
