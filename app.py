import os
import pandas as pd
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'evolve-2026-key'
# Usiamo un nome database standard
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///evolve_prod.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- MODELLI ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    items = db.relationship('Item', backref='assigned_tech', lazy=True)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False) 
    serial = db.Column(db.String(100), unique=True, nullable=True) 
    description = db.Column(db.String(255))
    quantity = db.Column(db.Integer, default=1)
    technician_id = db.Column(db.Integer, db.ForeignKey('technician.id'), nullable=True)

class TransferLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bolla_no = db.Column(db.String(50), unique=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    tech_name = db.Column(db.String(120))
    details = db.Column(db.Text)

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
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Credenziali errate')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    t_count = Technician.query.count()
    i_count = Item.query.filter_by(technician_id=None).count()
    return render_template('dashboard.html', t_count=t_count, i_count=i_count)

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
    available_items = Item.query.filter_by(technician_id=None).all()
    return render_template('technician_detail.html', tech=tech, available_items=available_items)

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
            existing = Item.query.filter_by(code=code, serial=None, technician_id=None).first()
            if existing: existing.quantity += qty
            else: db.session.add(Item(code=code, description=desc, quantity=qty))
        db.session.commit()
    items = Item.query.filter_by(technician_id=None).all()
    return render_template('warehouse.html', items=items)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
