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
    # Projects table updated with target info
    c.execute('''CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT, location TEXT, target REAL DEFAULT 0, unit TEXT DEFAULT 'Unit')''')
    
    # Migration hack for existing projects table (purane data me column add karne ke liye)
    try:
        c.execute("ALTER TABLE projects ADD COLUMN target REAL DEFAULT 0")
        c.execute("ALTER TABLE projects ADD COLUMN unit TEXT DEFAULT 'Unit'")
    except: pass

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
    
    # NEW: Work Progress Log Table
    c.execute('''CREATE TABLE IF NOT EXISTS work_logs 
                 (id INTEGER PRIMARY KEY, project_id INTEGER, date TEXT, progress REAL, material_loads REAL, notes TEXT)''')
    
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
    
    # 1. Expenses Sum
    expenses_map = {row['project_id']: (row[1] or 0) for row in conn.execute("SELECT project_id, SUM(amount) FROM expenses GROUP BY project_id").fetchall()}
        
    # 2. Payments Sum
    payments_map = {row['project_id']: (row[1] or 0) for row in conn.execute("SELECT w.project_id, SUM(p.amount) FROM payments p JOIN workers w ON p.worker_id = w.id GROUP BY w.project_id").fetchall()}

    # 3. Work Progress Data
    prog_rows = conn.execute("SELECT project_id, SUM(progress), COUNT(DISTINCT date) FROM work_logs GROUP BY project_id").fetchall()
    progress_map = {row['project_id']: {'done': (row[1] or 0), 'days': (row[2] or 0)} for row in prog_rows}

    # 4. Worker Cost & Mandays Calculation (New Logic)
    # Yeh query check karegi ki kis worker ki kitni haziri hai aur uska rate kya hai
    cost_mandays_rows = conn.execute('''
        SELECT 
            w.project_id, 
            SUM(CASE WHEN a.status='Present' THEN w.daily_wage WHEN a.status='Half Day' THEN w.daily_wage/2.0 ELSE 0 END),
            SUM(CASE WHEN a.status='Present' THEN 1 WHEN a.status='Half Day' THEN 0.5 ELSE 0 END)
        FROM workers w 
        JOIN attendance a ON w.id = a.worker_id 
        GROUP BY w.project_id
    ''').fetchall()
    worker_stats_map = {row['project_id']: {'cost': (row[1] or 0), 'mandays': (row[2] or 0)} for row in cost_mandays_rows}

    public_stats = []
    for p in projects:
        exp = expenses_map.get(p['id'], 0)
        pay = payments_map.get(p['id'], 0)
        
        # Worker Counts
        workers = conn.execute("SELECT role FROM workers WHERE project_id=?", (p['id'],)).fetchall()
        mistri_count = sum(1 for w in workers if w['role'] == 'Mistri')
        labour_count = sum(1 for w in workers if w['role'] == 'Labour')
        
        # New Stats
        ws = worker_stats_map.get(p['id'], {'cost': 0, 'mandays': 0})
        
        # Estimation Logic
        prog_data = progress_map.get(p['id'], {'done': 0, 'days': 0})
        done = prog_data['done']
        days_worked = prog_data['days']
        target = p['target'] or 0
        
        est_days = "N/A"
        percent = 0
        if target > 0:
            percent = int((done / target) * 100)
            if done > 0 and days_worked > 0:
                avg_daily = done / days_worked
                remaining = target - done
                if remaining > 0:
                    est_days = int(remaining / avg_daily)
                else:
                    est_days = "Done"
        
        public_stats.append({
            'name': p['name'], 'location': p['location'], 
            'expense': exp, 'payment': pay, 'grand_total': exp + pay,
            'mistri': mistri_count, 'labour': labour_count,
            'mandays': ws['mandays'],          
            'total_worker_cost': ws['cost'],   # New: Total Wage Generated
            'progress_done': done, 'progress_target': target, 
            'progress_unit': p['unit'], 'progress_percent': percent, 'est_days': est_days
        })
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
    
    # 1. Data Calculations (Same as public_home)
    expenses_map = {row['project_id']: (row[1] or 0) for row in conn.execute("SELECT project_id, SUM(amount) FROM expenses GROUP BY project_id").fetchall()}
    payments_map = {row['project_id']: (row[1] or 0) for row in conn.execute("SELECT w.project_id, SUM(p.amount) FROM payments p JOIN workers w ON p.worker_id = w.id GROUP BY w.project_id").fetchall()}
    
    # 2. Work Progress & Material
    prog_rows = conn.execute("SELECT project_id, SUM(progress), SUM(material_loads), COUNT(DISTINCT date) FROM work_logs GROUP BY project_id").fetchall()
    progress_map = {row['project_id']: {'done': (row[1] or 0), 'mat': (row[2] or 0), 'days': (row[3] or 0)} for row in prog_rows}

    # 3. Worker Cost & Mandays
    cost_mandays_rows = conn.execute('''
        SELECT 
            w.project_id, 
            SUM(CASE WHEN a.status='Present' THEN w.daily_wage WHEN a.status='Half Day' THEN w.daily_wage/2.0 ELSE 0 END),
            SUM(CASE WHEN a.status='Present' THEN 1 WHEN a.status='Half Day' THEN 0.5 ELSE 0 END)
        FROM workers w 
        JOIN attendance a ON w.id = a.worker_id 
        GROUP BY w.project_id
    ''').fetchall()
    worker_stats_map = {row['project_id']: {'cost': (row[1] or 0), 'mandays': (row[2] or 0)} for row in cost_mandays_rows}

    project_stats = []
    for p in projects:
        exp = expenses_map.get(p['id'], 0)
        pay = payments_map.get(p['id'], 0)
        
        # Counts
        workers = conn.execute("SELECT role FROM workers WHERE project_id=?", (p['id'],)).fetchall()
        m_count = sum(1 for w in workers if w['role'] == 'Mistri')
        l_count = sum(1 for w in workers if w['role'] == 'Labour')
        
        # New Stats
        ws = worker_stats_map.get(p['id'], {'cost': 0, 'mandays': 0})
        prog_data = progress_map.get(p['id'], {'done': 0, 'mat': 0, 'days': 0})
        
        # Estimation
        done = prog_data['done']
        target = p['target'] or 0
        percent = int((done / target) * 100) if target > 0 else 0
        
        est_days = "N/A"
        if target > 0 and done > 0 and prog_data['days'] > 0:
            avg = done / prog_data['days']
            rem = target - done
            est_days = int(rem / avg) if rem > 0 else "Done"

        project_stats.append({
            'id': p['id'], 'name': p['name'], 'location': p['location'], 
            'total_expense': exp, 'total_payment': pay, 'grand_total': exp + pay,
            'm_count': m_count, 'l_count': l_count,
            'mandays': ws['mandays'],
            'total_worker_cost': ws['cost'],
            'progress_done': done, 'progress_target': target, 'progress_unit': p['unit'],
            'progress_percent': percent, 'material_used': prog_data['mat'], 'est_days': est_days
        })
    
    conn.close()
    return render_template('dashboard.html', t_mistri=t_mistri, t_labour=t_labour, p_stats=project_stats, idle_workers=idle_workers)

# --- NEW ROUTE: Add Work Progress ---
@app.route('/add_work_log', methods=['POST'])
@login_required
def add_work_log():
    conn = get_db_connection()
    date_val = request.form.get('date') or datetime.now().strftime('%Y-%m-%d')
    conn.execute("INSERT INTO work_logs (project_id, date, progress, material_loads, notes) VALUES (?, ?, ?, ?, ?)",
                 (request.form['project_id'], date_val, request.form['progress'], request.form['material_loads'], request.form['notes']))
    conn.commit()
    conn.close()
    flash('✅ Work progress updated!')
    return redirect(url_for('dashboard'))

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
# --- NEW: Edit Expense Route ---
@app.route('/edit_expense/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    conn = get_db_connection()
    
    # Agar Form Submit hua hai (Save Changes)
    if request.method == 'POST':
        item = request.form['item']
        amount = request.form['amount']
        date_time = request.form['date_time'].replace('T', ' ')
        project_id = request.form['project_id']
        
        conn.execute('''UPDATE expenses 
                        SET item=?, amount=?, date_time=?, project_id=? 
                        WHERE id=?''', 
                     (item, amount, date_time, project_id, expense_id))
        conn.commit()
        conn.close()
        flash('Expense updated successfully!')
        return redirect(url_for('expense_log'))

    # Agar Edit Page kholna hai (Get Data)
    expense = conn.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
    projects = conn.execute("SELECT * FROM projects").fetchall()
    conn.close()
    return render_template('edit_expense.html', expense=expense, projects=projects)

# --- NEW: Delete Expense Route ---
@app.route('/delete_expense/<int:expense_id>')
@login_required
def delete_expense(expense_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM expenses WHERE id=?", (expense_id,)).fetchone()
    conn.commit()
    conn.close()
    flash('Expense deleted successfully!')
    return redirect(url_for('expense_log'))

@app.route('/project_expenses/<int:project_id>')
@login_required
def project_expenses(project_id):
    conn = get_db_connection()
    projects = conn.execute("SELECT * FROM projects").fetchall()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    expenses = conn.execute("SELECT * FROM expenses WHERE project_id=? ORDER BY date_time DESC", (project_id,)).fetchall()
    conn.close()
    return render_template('expense_log.html', expenses=expenses, project_name=project['name'], projects=projects, current_pid=project_id)

@app.route('/print_expenses/<int:project_id>')
@login_required
def print_expenses(project_id):
    conn = get_db_connection()
    
    # Queries Logic
    if project_id == 0:
        project_name = "All Projects"
        # Expenses
        expenses = conn.execute('''SELECT e.*, p.name as project_name FROM expenses e 
                                   LEFT JOIN projects p ON e.project_id = p.id 
                                   ORDER BY e.date_time DESC''').fetchall()
        # Payments (Join with workers & projects)
        payments = conn.execute('''SELECT p.*, w.name, w.role, proj.name as project_name 
                                   FROM payments p 
                                   JOIN workers w ON p.worker_id = w.id 
                                   LEFT JOIN projects proj ON w.project_id = proj.id 
                                   ORDER BY p.date DESC''').fetchall()
    else:
        project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        project_name = project['name']
        # Expenses
        expenses = conn.execute("SELECT * FROM expenses WHERE project_id=? ORDER BY date_time DESC", (project_id,)).fetchall()
        # Payments (Workers of this project)
        payments = conn.execute('''SELECT p.*, w.name, w.role 
                                   FROM payments p 
                                   JOIN workers w ON p.worker_id = w.id 
                                   WHERE w.project_id=? 
                                   ORDER BY p.date DESC''', (project_id,)).fetchall()
    
    total_exp = sum(e['amount'] for e in expenses)
    total_pay = sum(p['amount'] for p in payments)
    
    conn.close()
    return render_template('print_expenses.html', 
                           expenses=expenses, payments=payments, 
                           total_exp=total_exp, total_pay=total_pay, 
                           project_name=project_name)

@app.route('/project_workers/<int:project_id>')
@login_required
def project_workers(project_id):
    conn = get_db_connection()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    # Fix: Join lagaya taki project_name mile aur "Baitha Hua" na dikhe
    workers = conn.execute('''SELECT w.*, p.name as project_name FROM workers w 
                              LEFT JOIN projects p ON w.project_id = p.id 
                              WHERE w.project_id=?''', (project_id,)).fetchall()
    conn.close()
    return render_template('worker_list.html', workers=workers, title=f"Staff at {project['name']}")

@app.route('/add_project', methods=['POST'])
@login_required
def add_project():
    conn = get_db_connection()
    # Updated to save Target and Unit
    conn.execute("INSERT INTO projects (name, location, target, unit) VALUES (?, ?, ?, ?)", 
                 (request.form['name'], request.form['location'], request.form.get('target', 0), request.form.get('unit', 'Unit')))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/edit_project/<int:project_id>', methods=['POST'])
@login_required
def edit_project(project_id):
    conn = get_db_connection()
    
    # Values get karte waqt safety (agar user khali chhod de)
    name = request.form['name']
    location = request.form['location']
    target = request.form.get('target') # .get use kiya taki crash na ho
    unit = request.form.get('unit')
    
    # Agar target khali hai to 0 mano
    if not target or target.strip() == '':
        target = 0
    
    if not unit:
        unit = 'Unit'

    conn.execute('UPDATE projects SET name=?, location=?, target=?, unit=? WHERE id=?',
                 (name, location, target, unit, project_id))
    conn.commit()
    conn.close()
    flash('✅ Project Updated Successfully!')
    return redirect(url_for('dashboard'))

@app.route('/add_worker', methods=['GET', 'POST'])
@login_required
def add_worker():
    conn = get_db_connection()
    # POST logic same rahega (Save karne wala)
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
        return redirect(url_for('add_worker')) # Redirect back to same list page

    # GET: Ab hum workers ki list bhi bhejenge
    workers = conn.execute('''SELECT w.*, p.name as project_name FROM workers w 
                              LEFT JOIN projects p ON w.project_id = p.id 
                              ORDER BY w.id DESC''').fetchall()
    projects = conn.execute("SELECT * FROM projects").fetchall()
    conn.close()
    return render_template('add_worker.html', workers=workers, projects=projects)

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
    # Projects bhi bhejein taki modal me dropdown dikh sake
    projects = conn.execute("SELECT * FROM projects").fetchall()
    expenses = conn.execute('''SELECT e.*, p.name as project_name FROM expenses e 
                               LEFT JOIN projects p ON e.project_id = p.id 
                               ORDER BY e.date_time DESC''').fetchall()
    conn.close()
    return render_template('expense_log.html', expenses=expenses, project_name="All Projects", projects=projects, current_pid=0)

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
    workers = conn.execute('''SELECT w.*, p.name as project_name 
                              FROM workers w 
                              LEFT JOIN projects p ON w.project_id = p.id 
                              ORDER BY w.role, w.name''').fetchall()
    conn.close()
    return render_template('attendance.html', workers=workers, today_date=today_date)

@app.route('/attendance_report')
@login_required
def attendance_report():
    conn = get_db_connection()
    # Pichle 7 din ki dates
    dates = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
    
    # --- 1. Worker Attendance Data ---
    workers = conn.execute("SELECT * FROM workers WHERE project_id IS NOT NULL AND project_id > 0").fetchall()
    worker_report = []
    for w in workers:
        w_row = {'name': w['name'], 'role': w['role'], 'days': []}
        for d in dates:
            stat = conn.execute("SELECT status FROM attendance WHERE worker_id=? AND date=?", (w['id'], d)).fetchone()
            w_row['days'].append(stat['status'] if stat else '-')
        worker_report.append(w_row)
        
    # --- 2. Site Progress Data (Updated Fix) ---
    projects = conn.execute("SELECT * FROM projects").fetchall()
    site_report = []
    for p in projects:
        p_row = {'name': p['name'], 'unit': p['unit'], 'days': []}
        for d in dates:
            # FIX: Yahan SUM() lagaya hai taki ek din ki sari entry jud jayein
            log = conn.execute("SELECT SUM(progress) as progress, SUM(material_loads) as material_loads FROM work_logs WHERE project_id=? AND date=?", (p['id'], d)).fetchone()
            
            # Check karte hain ki data hai ya nahi (SUM None return kar sakta hai)
            if log and log['progress'] is not None:
                p_row['days'].append({'work': log['progress'], 'mat': log['material_loads']})
            else:
                p_row['days'].append(None)
        site_report.append(p_row)

    conn.close()
    return render_template('attendance_report.html', dates=dates, report=worker_report, site_report=site_report)

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
    
    # --- SPEED OPTIMIZATION START ---
    # Attendance Count Pre-fetch
    att_map = {} 
    att_rows = conn.execute("SELECT worker_id, status, COUNT(*) FROM attendance GROUP BY worker_id, status").fetchall()
    for row in att_rows:
        wid = row['worker_id']
        status = row['status']
        if wid not in att_map: att_map[wid] = {'Present': 0, 'Half Day': 0}
        if status in att_map[wid]: att_map[wid][status] = row[2]
        
    # Payments Sum Pre-fetch
    pay_map = {}
    pay_rows = conn.execute("SELECT worker_id, SUM(amount) FROM payments GROUP BY worker_id").fetchall()
    for row in pay_rows:
        pay_map[row['worker_id']] = row[1] or 0
    # --- SPEED OPTIMIZATION END ---

    payment_summary = []
    t_paid = 0
    t_due = 0
    for w in workers:
        stats = att_map.get(w['id'], {'Present': 0, 'Half Day': 0})
        p_days = stats['Present']
        h_days = stats['Half Day']
        
        total_earned = (p_days * w['daily_wage']) + (h_days * (w['daily_wage']/2))
        total_paid = pay_map.get(w['id'], 0)
        balance = total_earned - total_paid
        t_paid += total_paid
        t_due += balance
        payment_summary.append({'id': w['id'], 'name': w['name'], 'role': w['role'], 'wage': w['daily_wage'], 
                                'earned': total_earned, 'paid': total_paid, 'balance': balance, 'project_name': w['project_name'], 'rating': w['rating']})
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
    # Name, Phone, Wage, Role sab update hoga ab
    conn.execute('''UPDATE workers SET 
                    name=?, phone=?, role=?, daily_wage=?, 
                    address=?, experience=?, rating=?, project_id=? 
                    WHERE id=?''', 
                 (request.form['name'], request.form['phone'], request.form['role'], request.form['daily_wage'],
                  request.form['address'], request.form['experience'], request.form['rating'], request.form['project_id'], 
                  worker_id))
    conn.commit()
    conn.close()
    return redirect(url_for('worker_profile', worker_id=worker_id))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/print_attendance')
@login_required
def print_attendance():
    conn = get_db_connection()
    
    # --- 1. Date Filter Logic ---
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    if start_date_str and end_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    else:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=6) 
        
    dates = []
    curr = start_date
    while curr <= end_date:
        dates.append(curr.strftime('%Y-%m-%d'))
        curr += timedelta(days=1)
        
    # --- 2. Statistics Variables ---
    total_mandays = 0
    total_work_done = {} 
    
    # --- 3. Site Progress Data (Updated Fix) ---
    projects = conn.execute("SELECT * FROM projects").fetchall()
    site_report = []
    for p in projects:
        p_row = {'name': p['name'], 'unit': p['unit'], 'days': [], 'total_site_work': 0}
        for d in dates:
            # FIX: Yahan bhi SUM() lagaya hai
            log = conn.execute("SELECT SUM(progress) as progress, SUM(material_loads) as material_loads FROM work_logs WHERE project_id=? AND date=?", (p['id'], d)).fetchone()
            
            if log and log['progress'] is not None:
                progress = log['progress'] or 0
                mat_loads = log['material_loads'] or 0
                
                p_row['days'].append({'work': progress, 'mat': mat_loads})
                
                # Stats Add karo
                p_row['total_site_work'] += progress
                
                # Grand Total calculation
                u = p['unit'] or 'Unit'
                if u not in total_work_done: total_work_done[u] = 0
                total_work_done[u] += progress
            else:
                p_row['days'].append(None)
        site_report.append(p_row)

    # --- 4. Worker Attendance Data ---
    workers = conn.execute('''SELECT w.*, p.name as project_name FROM workers w 
                              LEFT JOIN projects p ON w.project_id = p.id 
                              WHERE w.project_id IS NOT NULL AND w.project_id > 0 
                              ORDER BY w.project_id, w.role''').fetchall()
    
    worker_report = []
    for w in workers:
        w_row = {'name': w['name'], 'role': w['role'], 'project_name': w['project_name'], 'days': [], 'p_count': 0}
        for d in dates:
            stat = conn.execute("SELECT status FROM attendance WHERE worker_id=? AND date=?", (w['id'], d)).fetchone()
            status = stat['status'] if stat else '-'
            w_row['days'].append(status)
            
            # Manday Calculation
            if status == 'Present':
                total_mandays += 1
                w_row['p_count'] += 1
            elif status == 'Half Day':
                total_mandays += 0.5
                w_row['p_count'] += 0.5
                
        worker_report.append(w_row)

    conn.close()
    
    return render_template('print_attendance.html', 
                           dates=dates, 
                           site_report=site_report, 
                           worker_report=worker_report,
                           start_date=start_date.strftime('%Y-%m-%d'),
                           end_date=end_date.strftime('%Y-%m-%d'),
                           total_mandays=total_mandays,
                           total_work_done=total_work_done)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)