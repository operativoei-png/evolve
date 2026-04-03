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
app.config['SECRET_KEY'] = 'evolve-industrial-erp-2026'

# --- CONFIGURAZIONE DATABASE (POSTGRESQL PER RENDERE I DATI PERMANENTI) ---
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///evolve_prod.db'
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
    badge_id = db.Column(db.String(50), unique=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50))
    van_plate = db.Column(db.String(20))
    items = db.relationship('Item', backref='owner', lazy=True)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False) 
    serial = db.Column(db.String(120), unique=True, nullable=True) 
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

# --- ROTTE DI NAVIGAZIONE ---
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
        flash('Credenziali non valide')
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

# --- GESTIONE TECNICI ---
@app.route('/technicians', methods=['GET', 'POST'])
@login_required
def technicians():
    if request.method == 'POST':
        try:
            new_t = Technician(
                badge_id=request.form.get('badge'),
                name=request.form.get('name'),
                phone=request.form.get('phone'),
                van_plate=request.form.get('plate')
            )
            db.session.add(new_t)
            db.session.commit()
            flash('Tecnico registrato correttamente', 'success')
        except:
            db.session.rollback()
            flash('Errore: Nome o Badge già esistente', 'danger')
    return render_template('technicians.html', techs=Technician.query.all())

@app.route('/search_tech', methods=['POST'])
@login_required
def search_tech():
    badge = request.form.get('badge_scan')
    tech = Technician.query.filter_by(badge_id=badge).first()
    if tech:
        return redirect(url_for('technician_detail', id=tech.id))
    flash('Badge non trovato!')
    return redirect(url_for('technicians'))

@app.route('/technician/<int:id>')
@login_required
def technician_detail(id):
    tech = Technician.query.get_or_404(id)
    available = Item.query.filter_by(technician_id=None).all()
    return render_template('technician_detail.html', tech=tech, available=available)

# --- GESTIONE MAGAZZINO E ASSEGNAZIONE ---
@app.route('/warehouse', methods=['GET', 'POST'])
@login_required
def warehouse():
    if request.method == 'POST':
        code = request.form.get('code')
        serial = request.form.get('serial')
        desc = request.form.get('description')
        qty = int(request.form.get('quantity', 1))
        if serial:
            db.session.add(Item(code=code, serial=serial, description=desc, quantity=1))
        else:
            item = Item.query.filter_by(code=code, serial=None, technician_id=None).first()
            if item: item.quantity += qty
            else: db.session.add(Item(code=code, description=desc, quantity=qty))
        db.session.commit()
    items = Item.query.filter_by(technician_id=None).all()
    return render_template('warehouse.html', items=items)

@app.route('/assign/<int:tech_id>', methods=['POST'])
@login_required
def assign_item(tech_id):
    tech = Technician.query.get_or_404(tech_id)
    # Raccogliamo il seriale sparato dalla pistola
    barcode_serial = request.form.get('barcode_serial').strip() if request.form.get('barcode_serial') else None
    
    # Cerchiamo l'articolo nel magazzino centrale tramite Seriale
    item = Item.query.filter_by(serial=barcode_serial, technician_id=None).first()

    if item:
        try:
            bolla_no = f"BOL-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
            item.technician_id = tech.id
            log_desc = f"CONSEGNATO: {item.description} (S/N: {item.serial})"
            log = TransferLog(bolla_no=bolla_no, tech_name=tech.name, data_json=log_desc)
            db.session.add(log)
            db.session.commit()
            return redirect(url_for('visualizza_bolla', id=log.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Errore database: {str(e)}")
    else:
        flash('ERRORE: Articolo con questo seriale non trovato a magazzino o già assegnato!')
    
    return redirect(url_for('technician_detail', id=tech.id))

@app.route('/bolla/<int:id>')
@login_required
def visualizza_bolla(id):
    log = TransferLog.query.get_or_404(id)
    return render_template('bolla.html', log=log)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
