import os
import pandas as pd
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'evolve-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///evolve.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- MODELLI DATABASE ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    # Relazione con materiali e attrezzature assegnate
    items = db.relationship('Item', backref='assigned_tech', lazy=True)
    tools = db.relationship('Tool', backref='assigned_tech', lazy=True)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False) # Barcode
    description = db.Column(db.String(255))
    quantity = db.Column(db.Integer, default=0)
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'), nullable=True)

class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    serial = db.Column(db.String(80), unique=True)
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'), nullable=True)

# Inizializzazione
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password_hash=generate_password_hash('admin123'))
        db.session.add(admin)
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ROTTE GENERALI ---

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Credenziali errate!')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    t_count = Technician.query.count()
    # Conta solo gli articoli nel magazzino generale (non assegnati)
    i_count = Item.query.filter_by(technician_id=None).count()
    return render_template('dashboard.html', t_count=t_count, i_count=i_count)

# --- GESTIONE MAGAZZINO ---

@app.route('/warehouse', methods=['GET', 'POST'])
@login_required
def warehouse():
    if request.method == 'POST':
        code = request.form.get('code')
        desc = request.form.get('description')
        qty = int(request.form.get('quantity', 0))
        
        # Se il codice esiste già, aumenta la quantità (utile per barcode)
        existing_item = Item.query.filter_by(code=code, technician_id=None).first()
        if existing_item:
            existing_item.quantity += qty
        else:
            db.session.add(Item(code=code, description=desc, quantity=qty))
        
        db.session.commit()
        flash('Magazzino aggiornato!')
        return redirect(url_for('warehouse'))
    
    items = Item.query.filter_by(technician_id=None).all()
    return render_template('warehouse.html', items=items)

# --- EXCEL IMPORT/EXPORT ---

@app.route('/export/excel')
@login_required
def export_excel():
    items = Item.query.all()
    data = []
    for i in items:
        data.append({
            "Codice": i.code,
            "Descrizione": i.description,
            "Quantità": i.quantity,
            "Assegnato a": i.assigned_tech.name if i.assigned_tech else "Magazzino Centrale"
        })
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventario')
    output.seek(0)
    return send_file(output, download_name="inventario_evolve.xlsx", as_attachment=True)

@app.route('/import/excel', methods=['POST'])
@login_required
def import_excel():
    file = request.files.get('file')
    if file:
        df = pd.read_excel(file)
        for _, row in df.iterrows():
            # Cerca se esiste già per aggiornare qty, altrimenti crea
            item = Item.query.filter_by(code=str(row['Codice']), technician_id=None).first()
            if item:
                item.quantity = int(row['Quantità'])
            else:
                db.session.add(Item(code=str(row['Codice']), description=row['Descrizione'], quantity=int(row['Quantità'])))
        db.session.commit()
        flash('Importazione Excel completata!')
    return redirect(url_for('warehouse'))

# --- ASSEGNAZIONE TECNICI ---

@app.route('/technician/<int:id>/assign', methods=['POST'])
@login_required
def assign_material(id):
    tech = Technician.query.get_or_404(id)
    item_code = request.form.get('item_code')
    qty_to_assign = int(request.form.get('quantity', 0))
    
    # Trova l'articolo nel magazzino centrale
    central_item = Item.query.filter_by(code=item_code, technician_id=None).first()
    
    if central_item and central_item.quantity >= qty_to_assign:
        # Scarica dal magazzino centrale
        central_item.quantity -= qty_to_assign
        
        # Carica sul tecnico (magazzino viaggiante)
        tech_item = Item.query.filter_by(code=item_code, technician_id=tech.id).first()
        if tech_item:
            tech_item.quantity += qty_to_assign
        else:
            db.session.add(Item(code=item_code, description=central_item.description, quantity=qty_to_assign, technician_id=tech.id))
            
        db.session.commit()
        flash(f'Materiale assegnato a {tech.name}')
    else:
        flash('Quantità insufficiente in magazzino!')
        
    return redirect(url_for('technician_detail', id=tech.id))

@app.route('/technicians', methods=['GET', 'POST'])
@login_required
def technicians():
    if request.method == 'POST':
        db.session.add(Technician(name=request.form['name'], phone=request.form['phone']))
        db.session.commit()
    return render_template('technicians.html', techs=Technician.query.all())

@app.route('/technician/<int:id>')
@login_required
def technician_detail(id):
    tech = Technician.query.get_or_404(id)
    # Lista materiali disponibili a magazzino per il menu a tendina
    available_items = Item.query.filter(Item.technician_id == None, Item.quantity > 0).all()
    return render_template('technician_detail.html', tech=tech, available_items=available_items)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
