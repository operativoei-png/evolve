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
app.config['SECRET_KEY'] = 'evolve-industrial-super-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///evolve_full_system.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ----------------------------------------------------------------
# MODELLI DATABASE (STRUTTURA COMPLESSA)
# ----------------------------------------------------------------

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    notes = db.Column(db.Text)
    items = db.relationship('Item', backref='technician', lazy=True)
    documents = db.relationship('Document', backref='technician', lazy=True)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False) # Barcode modello
    serial = db.Column(db.String(120), unique=True, nullable=True) # Matricola/Serial
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50)) # Materiale, Attrezzatura, Ricambio
    quantity = db.Column(db.Integer, default=1)
    unit = db.Column(db.String(20), default="pz")
    location = db.Column(db.String(100), default="Magazzino Centrale")
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'), nullable=True)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

class TransferLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bolla_no = db.Column(db.String(50), unique=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    technician_name = db.Column(db.String(120))
    type = db.Column(db.String(20)) # CARICO, SCARICO, TRASFERIMENTO
    content_json = db.Column(db.Text) # Dettagli materiali trasferiti

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150))
    filename = db.Column(db.String(255))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'))

# ----------------------------------------------------------------
# INIZIALIZZAZIONE & LOGIN MANAGER
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
# ROTTE PRINCIPALI
# ----------------------------------------------------------------

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password_hash, request.form.get('password')):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Accesso negato. Riprova.')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    stats = {
        'total_items': Item.query.filter_by(technician_id=None).count(),
        'total_techs': Technician.query.count(),
        'assigned_items': Item.query.filter(Item.technician_id != None).count(),
        'recent_transfers': TransferLog.query.order_by(TransferLog.timestamp.desc()).limit(5).all()
    }
    return render_template('dashboard.html', stats=stats)

# ----------------------------------------------------------------
# GESTIONE MAGAZZINO (BARCODE & EXCEL)
# ----------------------------------------------------------------

@app.route('/warehouse', methods=['GET', 'POST'])
@login_required
def warehouse():
    if request.method == 'POST':
        # Logica per carico manuale o barcode
        code = request.form.get('code')
        serial = request.form.get('serial') # Pistola Barcode su Matricola
        qty = int(request.form.get('quantity', 1))
        
        if serial:
            # Controllo se la matricola esiste già
            exists = Item.query.filter_by(serial=serial).first()
            if exists:
                flash(f"Errore: Matricola {serial} già presente!")
            else:
                db.session.add(Item(code=code, serial=serial, description=request.form.get('description'), quantity=1))
        else:
            # Materiale generico (senza matricola)
            item = Item.query.filter_by(code=code, serial=None, technician_id=None).first()
            if item:
                item.quantity += qty
            else:
                db.session.add(Item(code=code, description=request.form.get('description'), quantity=qty))
        
        db.session.commit()
        flash("Magazzino aggiornato correttamente.")

    items = Item.query.filter_by(technician_id=None).all()
    return render_template('warehouse.html', items=items)

@app.route('/warehouse/export')
@login_required
def export_warehouse():
    items = Item.query.all()
    data = [{
        'Codice': i.code, 'Matricola': i.serial, 'Descrizione': i.description,
        'Quantità': i.quantity, 'Posizione': i.location, 
        'Assegnato a': i.technician.name if i.technician else 'Magazzino Centrale'
    } for i in items]
    
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, download_name="Evolve_Inventario_Totale.xlsx", as_attachment=True)

@app.route('/warehouse/import', methods=['POST'])
@login_required
def import_warehouse():
    file = request.files.get('file')
    if file:
        df = pd.read_excel(file)
        for _, row in df.iterrows():
            # Logica di importazione intelligente
            db.session.add(Item(
                code=str(row['Codice']), 
                serial=str(row['Matricola']) if pd.notnull(row['Matricola']) else None,
                description=row['Descrizione'],
                quantity=int(row['Quantità'])
            ))
        db.session.commit()
        flash("Dati importati con successo da Excel.")
    return redirect(url_for('warehouse'))

# ----------------------------------------------------------------
# SCHEDA TECNICI & ASSEGNAZIONE BOLLE
# ----------------------------------------------------------------

@app.route('/technicians', methods=['GET', 'POST'])
@login_required
def technicians():
    if request.method == 'POST':
        db.session.add(Technician(name=request.form.get('name'), phone=request.form.get('phone')))
        db.session.commit()
    all_techs = Technician.query.all()
    return render_template('technicians.html', techs=all_techs)

@app.route('/technician/<int:id>')
@login_required
def technician_detail(id):
    tech = Technician.query.get_or_404(id)
    # Lista materiali disponibili per essere assegnati
    available = Item.query.filter_by(technician_id=None).all()
    return render_template('technician_detail.html', tech=tech, available=available)

@app.route('/assign_item/<int:tech_id>', methods=['POST'])
@login_required
def assign_item(tech_id):
    tech = Technician.query.get_or_404(tech_id)
    item_id = request.form.get('item_id')
    qty_req = int(request.form.get('quantity', 1))
    
    item = Item.query.get(item_id)
    if item and item.quantity >= qty_req:
        # Generazione Numero Bolla
        bolla_no = f"BOL-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:5].upper()}"
        
        if item.serial:
            # Se ha matricola, sposto l'intero pezzo
            item.technician_id = tech.id
            desc_log = f"Trasferita Matricola: {item.serial} ({item.description})"
        else:
            # Se è materiale sfuso, scalo la quantità e creo un record per il tecnico
            item.quantity -= qty_req
            new_move = Item(code=item.code, description=item.description, quantity=qty_req, technician_id=tech.id)
            db.session.add(new_move)
            desc_log = f"Trasferiti {qty_req} pz di {item.description}"

        log = TransferLog(bolla_no=bolla_no, technician_name=tech.name, type="CARICO TECNICO", content_json=desc_log)
        db.session.add(log)
        db.session.commit()
        flash(f"Materiale assegnato. Bolla creata: {bolla_no}")
        return redirect(url_for('view_bolla', id=log.id))
    
    flash("Errore: Quantità insufficiente in magazzino!")
    return redirect(url_for('technician_detail', id=tech.id))

@app.route('/bolla/<int:id>')
@login_required
def view_bolla(id):
    log = TransferLog.query.get_or_404(id)
    return render_template('bolla.html', log=log)

# ----------------------------------------------------------------
# AVVIO
# ----------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
