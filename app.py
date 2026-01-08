import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.secret_key = 'ppt_builders_secret_key_secure'

# --- LOGIN SETUP ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if u: return User(id=u['id'], username=u['username'])
    return None

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def get_db_connection():
    conn = sqlite3.connect('site_data.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = sqlite3.connect('site_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT, location TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS workers 
                 (id INTEGER PRIMARY KEY, name TEXT, phone TEXT, role TEXT, 
                  id_number TEXT, daily_wage REAL, photo_path TEXT, 
                  project_id INTEGER, address TEXT, experience TEXT, rating INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS expenses 
                 (id INTEGER PRIMARY KEY, item TEXT, amount REAL, date_time TEXT, project_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS attendance 
                 (id INTEGER PRIMARY KEY, worker_id INTEGER, date TEXT, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS payments 
                 (id INTEGER PRIMARY KEY, worker_id INTEGER, amount REAL, date TEXT, notes TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)''')
    
    admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
    if not admin:
        hashed_pw = generate_password_hash('admin123', method='pbkdf2:sha256')
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", ('admin', hashed_pw))
    conn.commit()
    conn.close()

init_db()

# --- ROUTES ---

# PWA Service Worker Route
@app.route('/sw.js')
def service_worker():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    return response

@app.route('/')
def public_home():
    conn = get_db_connection()
    projects = conn.execute("SELECT * FROM projects").fetchall()
    public_stats = []
    for p in projects:
        exp = conn.execute("SELECT SUM(amount) FROM expenses WHERE project_id=?", (p['id'],)).fetchone()[0] or 0
        mistri = conn.execute("SELECT COUNT(*) FROM workers WHERE project_id=? AND role='Mistri'", (p['id'],)).fetchone()[0]
        labour = conn.execute("SELECT COUNT(*) FROM workers WHERE project_id=? AND role='Labour'", (p['id'],)).fetchone()[0]
        public_stats.append({'name': p['name'], 'location': p['location'], 'expense': exp, 'mistri': mistri, 'labour': labour})
    conn.close()
    return render_template('public_home.html', stats=public_stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            login_user(User(id=user['id'], username=user['username']))
            return redirect(url_for('dashboard'))
        else:
            flash('Login Failed.')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('public_home'))

# --- NEW: Change Password Route ---
@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_pw = request.form['current_password']
        new_pw = request.form['new_password']
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE id=?", (current_user.id,)).fetchone()
        
        # Check if old password is correct
        if user and check_password_hash(user['password'], current_pw):
            hashed_pw = generate_password_hash(new_pw, method='pbkdf2:sha256')
            conn.execute("UPDATE users SET password=? WHERE id=?", (hashed_pw, current_user.id))
            conn.commit()
            flash('Password changed successfully! Please login again.')
            conn.close()
            logout_user()
            return redirect(url_for('login'))
        else:
            flash('Error: Old password is wrong!')
            conn.close()
            
    return render_template('change_password.html')

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    t_mistri = conn.execute("SELECT COUNT(*) FROM workers WHERE role='Mistri'").fetchone()[0]
    t_labour = conn.execute("SELECT COUNT(*) FROM workers WHERE role='Labour'").fetchone()[0]
    idle_workers = conn.execute("SELECT COUNT(*) FROM workers WHERE project_id IS NULL OR project_id = 0").fetchone()[0]
    
    projects = conn.execute("SELECT * FROM projects").fetchall()
    project_stats = []
    for p in projects:
        exp = conn.execute("SELECT SUM(amount) FROM expenses WHERE project_id=?", (p['id'],)).fetchone()[0]
        m_count = conn.execute("SELECT COUNT(*) FROM workers WHERE project_id=? AND role='Mistri'", (p['id'],)).fetchone()[0]
        l_count = conn.execute("SELECT COUNT(*) FROM workers WHERE project_id=? AND role='Labour'", (p['id'],)).fetchone()[0]
        project_stats.append({'id': p['id'], 'name': p['name'], 'total_expense': exp if exp else 0, 'm_count': m_count, 'l_count': l_count})
    conn.close()
    return render_template('dashboard.html', t_mistri=t_mistri, t_labour=t_labour, p_stats=project_stats, idle_workers=idle_workers)

# --- NEW ROUTE: Clickable Stats Lists ---
@app.route('/list/<category>')
@login_required
def worker_list(category):
    conn = get_db_connection()
    if category == 'Mistri':
        workers = conn.execute("SELECT w.*, p.name as project_name FROM workers w LEFT JOIN projects p ON w.project_id = p.id WHERE role='Mistri'").fetchall()
        title = "All Mistri List"
    elif category == 'Labour':
        workers = conn.execute("SELECT w.*, p.name as project_name FROM workers w LEFT JOIN projects p ON w.project_id = p.id WHERE role='Labour'").fetchall()
        title = "All Labour List"
    elif category == 'Idle':
        workers = conn.execute("SELECT w.*, p.name as project_name FROM workers w LEFT JOIN projects p ON w.project_id = p.id WHERE project_id IS NULL OR project_id = 0").fetchall()
        title = "Baitha Hua (Idle) Workers"
    else:
        workers = []
        title = "Unknown List"
    conn.close()
    return render_template('worker_list.html', workers=workers, title=title)

# --- NEW ROUTE: Filtered Expenses ---
@app.route('/project_expenses/<int:project_id>')
@login_required
def project_expenses(project_id):
    conn = get_db_connection()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    expenses = conn.execute("SELECT * FROM expenses WHERE project_id=? ORDER BY date_time DESC", (project_id,)).fetchall()
    conn.close()
    return render_template('expense_log.html', expenses=expenses, project_name=project['name'])

@app.route('/add_project', methods=['POST'])
@login_required
def add_project():
    conn = get_db_connection()
    conn.execute("INSERT INTO projects (name, location) VALUES (?, ?)", (request.form['name'], request.form['location']))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/add_worker', methods=['GET', 'POST'])
@login_required
def add_worker():
    conn = get_db_connection()
    if request.method == 'POST':
        file = request.files['photo']
        filename = file.filename if file else ''
        if filename: file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        p_id = request.form['project_id']
        if p_id == '0': p_id = None 
        conn.execute('''INSERT INTO workers (name, phone, role, id_number, daily_wage, photo_path, project_id, rating) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0)''',
                     (request.form['name'], request.form['phone'], request.form['role'], 
                      request.form['id_number'], request.form['daily_wage'], filename, p_id))
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard'))
    projects = conn.execute("SELECT * FROM projects").fetchall()
    conn.close()
    return render_template('add_worker.html', projects=projects)

@app.route('/add_expense', methods=['POST'])
@login_required
def add_expense():
    user_date = request.form.get('date_time')
    final_date = user_date.replace('T', ' ') if user_date else datetime.now().strftime('%Y-%m-%d %H:%M')
    conn = get_db_connection()
    conn.execute("INSERT INTO expenses (item, amount, date_time, project_id) VALUES (?, ?, ?, ?)", 
                 (request.form['item'], request.form['amount'], final_date, request.form['project_id']))
    conn.commit()
    conn.close()
    return redirect(url_for('expense_log')) # Redirects to full log, user can go back

@app.route('/expense_log')
@login_required
def expense_log():
    conn = get_db_connection()
    expenses = conn.execute('''SELECT e.*, p.name as project_name FROM expenses e 
                               LEFT JOIN projects p ON e.project_id = p.id 
                               ORDER BY e.date_time DESC''').fetchall()
    conn.close()
    return render_template('expense_log.html', expenses=expenses, project_name="All Projects")

@app.route('/attendance', methods=['GET', 'POST'])
@login_required
def attendance():
    conn = get_db_connection()
    if request.method == 'POST':
        date = request.form['attendance_date']
        workers = conn.execute("SELECT id FROM workers").fetchall()
        for w in workers:
            status = request.form.get(f'status_{w["id"]}')
            if status:
                exists = conn.execute("SELECT id FROM attendance WHERE worker_id=? AND date=?", (w['id'], date)).fetchone()
                if exists:
                    conn.execute("UPDATE attendance SET status=? WHERE id=?", (status, exists['id']))
                else:
                    conn.execute("INSERT INTO attendance (worker_id, date, status) VALUES (?, ?, ?)", (w['id'], date, status))
        conn.commit()
        return redirect(url_for('attendance_report'))
    today_date = datetime.now().strftime('%Y-%m-%d')
    workers = conn.execute("SELECT * FROM workers").fetchall()
    conn.close()
    return render_template('attendance.html', workers=workers, today_date=today_date)

@app.route('/attendance_report')
@login_required
def attendance_report():
    conn = get_db_connection()
    dates = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
    workers = conn.execute("SELECT * FROM workers WHERE project_id IS NOT NULL AND project_id > 0").fetchall()
    report_data = []
    for w in workers:
        w_row = {'name': w['name'], 'role': w['role'], 'days': []}
        for d in dates:
            stat = conn.execute("SELECT status FROM attendance WHERE worker_id=? AND date=?", (w['id'], d)).fetchone()
            w_row['days'].append(stat['status'] if stat else '-')
        report_data.append(w_row)
    conn.close()
    return render_template('attendance_report.html', dates=dates, report=report_data)

@app.route('/payments', methods=['GET', 'POST'])
@login_required
def payments():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute("INSERT INTO payments (worker_id, amount, date) VALUES (?, ?, ?)", 
                     (request.form['worker_id'], request.form['amount'], request.form['date']))
        conn.commit()
        return redirect(url_for('payments'))
    workers = conn.execute('''SELECT w.*, p.name as project_name FROM workers w LEFT JOIN projects p ON w.project_id = p.id''').fetchall()
    payment_summary = []
    t_paid = 0
    t_due = 0
    for w in workers:
        p_days = conn.execute("SELECT COUNT(*) FROM attendance WHERE worker_id=? AND status='Present'", (w['id'],)).fetchone()[0]
        h_days = conn.execute("SELECT COUNT(*) FROM attendance WHERE worker_id=? AND status='Half Day'", (w['id'],)).fetchone()[0]
        total_earned = (p_days * w['daily_wage']) + (h_days * (w['daily_wage']/2))
        total_paid = conn.execute("SELECT SUM(amount) FROM payments WHERE worker_id=?", (w['id'],)).fetchone()[0] or 0
        balance = total_earned - total_paid
        t_paid += total_paid
        t_due += balance
        payment_summary.append({'id': w['id'], 'name': w['name'], 'role': w['role'], 'wage': w['daily_wage'], 
                                'earned': total_earned, 'paid': total_paid, 'balance': balance, 'project_name': w['project_name']})
    conn.close()
    return render_template('payments.html', summary=payment_summary, today=datetime.now().strftime('%Y-%m-%d'), t_paid=t_paid, t_due=t_due)

@app.route('/profile/<int:worker_id>')
@login_required
def worker_profile(worker_id):
    conn = get_db_connection()
    worker = conn.execute('''SELECT w.*, p.name as project_name FROM workers w LEFT JOIN projects p ON w.project_id = p.id WHERE w.id=?''', (worker_id,)).fetchone()
    total_days = conn.execute("SELECT COUNT(*) FROM attendance WHERE worker_id=? AND (status='Present' OR status='Half Day')", (worker_id,)).fetchone()[0]
    projects = conn.execute("SELECT * FROM projects").fetchall()
    conn.close()
    return render_template('worker_profile.html', w=worker, total_days=total_days, projects=projects)

@app.route('/update_profile/<int:worker_id>', methods=['POST'])
@login_required
def update_profile(worker_id):
    conn = get_db_connection()
    conn.execute('''UPDATE workers SET address=?, experience=?, rating=?, project_id=? WHERE id=?''', 
                 (request.form['address'], request.form['experience'], request.form['rating'], request.form['project_id'], worker_id))
    conn.commit()
    conn.close()
    return redirect(url_for('worker_profile', worker_id=worker_id))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)