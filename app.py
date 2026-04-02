import os
import pandas as pd
from io import BytesIO
from datetime import datetime
import uuid

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'evolve-pro-erp-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///evolve_enterprise.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static/uploads/attestati')

# Assicura che la cartella upload esista
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ----------------------------------------------------------------
# MODELLI DATABASE (ARCHITETTURA AZIENDALE)
# ----------------------------------------------------------------

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='admin')

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    badge_number = db.Column(db.String(20), unique=True)
    phone = db.Column(db.String(50))
    van_plate = db.Column(db.String(20)) # Targa furgone assegnato
    # Relazioni
    items = db.relationship('Item', backref='owner', lazy=True)
    certs = db.relationship('Certificate', backref='technician', lazy=True)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False) # Barcode Modello
    serial = db.Column(db.String(120), unique=True, nullable=True) # Matricola Unica
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50)) # Materiale o Attrezzatura
    quantity = db.Column(db.Integer, default=1)
    unit = db.Column(db.String(10), default='pz')
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'), nullable=True)
    last_move = db.Column(db.DateTime, default=datetime.utcnow)

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(150))
    filename = db.Column(db.String(255))
    expiry_date = db.Column(db.Date, nullable=True)
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'))

class TransferLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bolla_no = db.Column(db.String(50), unique=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    tech_name = db.Column(db.String(120))
    data_json = db.Column(db.Text) # Riassunto contenuto bolla

# ----------------------------------------------------------------
# INIZIALIZZAZIONE & AUTH
# ----------------------------------------------------------------

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password_hash=generate_password_hash('admin123'))
        db.session.add(admin)
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ----------------------------------------------------------------
# ROTTE GESTIONALI
# ----------------------------------------------------------------

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

# MAGAZZINO GENERALE (CON SUPPORTO BARCODE)
@app.route('/warehouse', methods=['GET', 'POST'])
@login_required
def warehouse():
    if request.method == 'POST':
        code = request.form.get('code')
        serial = request.form.get('serial') # Carico con matricola
        qty = int(request.form.get('quantity', 1))
        
        if serial: # Pezzo unico tracciato
            new_item = Item(code=code, serial=serial, description=request.form.get('description'), quantity=1)
            db.session.add(new_item)
        else: # Materiale di consumo
            item = Item.query.filter_by(code=code, serial=None, technician_id=None).first()
            if item: item.quantity += qty
            else: db.session.add(Item(code=code, description=request.form.get('description'), quantity=qty))
        
        db.session.commit()
        flash('Carico registrato', 'success')

    items = Item.query.filter_by(technician_id=None).all()
    return render_template('warehouse.html', items=items)

# EXCEL IMPORT/EXPORT
@app.route('/excel/export')
@login_required
def export_excel():
    items = Item.query.all()
    df = pd.DataFrame([{
        'Codice': i.code, 'Matricola': i.serial, 'Descrizione': i.description,
        'Qta': i.quantity, 'Stato': 'Assegnato a ' + i.owner.name if i.owner else 'Disponibile'
    } for i in items])
    out = BytesIO()
    df.to_excel(out, index=False)
    out.seek(0)
    return send_file(out, download_name="Evolve_Full_Report.xlsx", as_attachment=True)

# SCHEDA TECNICO & ASSEGNAZIONE BOLLA
@app.route('/technician/<int:id>', methods=['GET', 'POST'])
@login_required
def technician_detail(id):
    tech = Technician.query.get_or_404(id)
    if request.method == 'POST' and 'cert_file' in request.files:
        # Caricamento Attestato
        file = request.files['cert_file']
        if file:
            fname = f"cert_{tech.id}_{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
            db.session.add(Certificate(description=request.form.get('desc'), filename=fname, technician_id=tech.id))
            db.session.commit()
            flash('Attestato caricato', 'success')

    available = Item.query.filter_by(technician_id=None).all()
    return render_template('technician_detail.html', tech=tech, available=available)

@app.route('/assign/<int:tech_id>', methods=['POST'])
@login_required
def assign_item(tech_id):
    tech = Technician.query.get_or_404(tech_id)
    item_id = request.form.get('item_id')
    qty_req = int(request.form.get('qty', 1))
    
    item = Item.query.get(item_id)
    if item and item.quantity >= qty_req:
        bolla_no = f"BOL-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        
        if item.serial: # Sposta matricola
            item.technician_id = tech.id
            log_desc = f"Consegnata Matricola {item.serial} - {item.description}"
        else: # Sposta quantità
            item.quantity -= qty_req
            db.session.add(Item(code=item.code, description=item.description, quantity=qty_req, technician_id=tech.id))
            log_desc = f"Consegnati {qty_req} pz di {item.description}"

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

@app.route('/technicians', methods=['GET', 'POST'])
@login_required
def technicians():
    if request.method == 'POST':
        db.session.add(Technician(name=request.form['name'], phone=request.form['phone'], van_plate=request.form['plate']))
        db.session.commit()
    return render_template('technicians.html', techs=Technician.query.all())

@app.route('/logout')
def logout(): logout_user(); return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
