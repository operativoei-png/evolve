import os
import pandas as pd
from io import BytesIO
from datetime import datetime
import uuid

from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'evolve-pro-2026-final'
# Cambiamo nome al DB per forzare la creazione delle nuove tabelle corrette
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///evolve_v3_final.db'
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
    name = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50))
    van_plate = db.Column(db.String(20)) # Targa furgone
    items = db.relationship('Item', backref='owner', lazy=True)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False) 
    serial = db.Column(db.String(120), unique=True, nullable=True) # Matricola
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'), nullable=True)

class TransferLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bolla_no = db.Column(db.String(50), unique=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    tech_name = db.Column(db.String(120))
    data_json = db.Column(db.Text)

# --- INIZIALIZZAZIONE ---

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password_hash=generate_password_hash('admin123'))
        db.session.add(admin)
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ROTTE ---

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password_hash, request.form.get('password')):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Credenziali non valide', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    stats = {
        'techs': Technician.query.count(),
        'wh_items': Item.query.filter_by(technician_id=None).count(),
        'assigned': Item.query.filter(Item.technician_id != None).count()
    }
    return render_template('dashboard.html', stats=stats)

@app.route('/technicians', methods=['GET', 'POST'])
@login_required
def technicians():
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        plate = request.form.get('plate')
        if name:
            try:
                db.session.add(Technician(name=name, phone=phone, van_plate=plate))
                db.session.commit()
                flash('Tecnico aggiunto!', 'success')
            except Exception as e:
                db.session.rollback()
                flash('Errore: Tecnico già esistente o dati non validi', 'danger')
            return redirect(url_for('technicians'))
    techs = Technician.query.all()
    return render_template('technicians.html', techs=techs)

@app.route('/technician/<int:id>')
@login_required
def technician_detail(id):
    tech = Technician.query.get_or_404(id)
    available = Item.query.filter_by(technician_id=None).all()
    return render_template('technician_detail.html', tech=tech, available=available)

@app.route('/warehouse', methods=['GET', 'POST'])
@login_required
def warehouse():
    if request.method == 'POST':
        code = request.form.get('code')
        serial = request.form.get('serial')
        desc = request.form.get('description')
        qty_str = request.form.get('quantity', '1')
        qty = int(qty_str) if qty_str.isdigit() else 1
        
        if serial:
            # Controllo se la matricola esiste già ovunque
            check = Item.query.filter_by(serial=serial).first()
            if check:
                flash(f'Errore: Matricola {serial} già presente a sistema!', 'danger')
            else:
                db.session.add(Item(code=code, serial=serial, description=desc, quantity=1))
                db.session.commit()
                flash('Matricola caricata', 'success')
        else:
            item = Item.query.filter_by(code=code, serial=None, technician_id=None).first()
            if item: 
                item.quantity += qty
            else: 
                db.session.add(Item(code=code, description=desc, quantity=qty))
            db.session.commit()
            flash('Magazzino aggiornato', 'success')
            
    items = Item.query.filter_by(technician_id=None).all()
    return render_template('warehouse.html', items=items)

@app.route('/assign/<int:tech_id>', methods=['POST'])
@login_required
def assign_item(tech_id):
    tech = Technician.query.get_or_404(tech_id)
    item_id = request.form.get('item_id')
    qty_req = int(request.form.get('qty', 1))
    item = Item.query.get(item_id)
    
    if item and item.quantity >= qty_req:
        bolla_no = f"BOL-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        if item.serial:
            item.technician_id = tech.id
            log_desc = f"Matricola {item.serial} - {item.description}"
        else:
            item.quantity -= qty_req
            db.session.add(Item(code=item.code, description=item.description, quantity=qty_req, technician_id=tech.id))
            log_desc = f"{qty_req} pz di {item.description}"
        
        log = TransferLog(bolla_no=bolla_no, tech_name=tech.name, data_json=log_desc)
        db.session.add(log)
        db.session.commit()
        return redirect(url_for('view_bolla', id=log.id))
    
    flash('Giacenza insufficiente', 'danger')
    return redirect(url_for('technician_detail', id=tech.id))

@app.route('/bolla/<int:id>')
@login_required
def view_bolla(id):
    log = TransferLog.query.get_or_404(id)
    return render_template('bolla.html', log=log)

@app.route('/excel/export')
@login_required
def export_excel():
    items = Item.query.all()
    df = pd.DataFrame([{'Codice': i.code, 'Matricola': i.serial, 'Descrizione': i.description, 'Qta': i.quantity, 'Posizione': i.owner.name if i.owner else 'Magazzino Centrale'} for i in items])
    out = BytesIO()
    df.to_excel(out, index=False)
    out.seek(0)
    return send_file(out, download_name="Inventario_Completo.xlsx", as_attachment=True)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
