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
    def __init__(self, id, username, role='admin', project_id=None):
        self.id = id
        self.username = username
        self.role = role
        self.project_id = project_id

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if u: 
        role = u['role'] if 'role' in u.keys() else 'admin'
        p_id = u['project_id'] if 'project_id' in u.keys() else None
        return User(id=u['id'], username=u['username'], role=role, project_id=p_id)
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
    c.execute('''CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT, location TEXT, target REAL DEFAULT 0, unit TEXT DEFAULT 'Unit', rate REAL DEFAULT 0)''')
    
    # Migrations
    try: c.execute("ALTER TABLE projects ADD COLUMN target REAL DEFAULT 0")
    except: pass
    try: c.execute("ALTER TABLE projects ADD COLUMN unit TEXT DEFAULT 'Unit'")
    except: pass
    try: c.execute("ALTER TABLE projects ADD COLUMN rate REAL DEFAULT 0")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS workers 
                 (id INTEGER PRIMARY KEY, name TEXT, phone TEXT, role TEXT, 
                  id_number TEXT, daily_wage REAL, photo_path TEXT, 
                  project_id INTEGER, address TEXT, experience TEXT, rating INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS expenses 
                 (id INTEGER PRIMARY KEY, item TEXT, amount REAL, date_time TEXT, project_id INTEGER)''')
    
    # Updated Attendance Table (Added project_id)
    c.execute('''CREATE TABLE IF NOT EXISTS attendance 
                 (id INTEGER PRIMARY KEY, worker_id INTEGER, date TEXT, status TEXT, project_id INTEGER)''')
    try: c.execute("ALTER TABLE attendance ADD COLUMN project_id INTEGER")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS payments 
                 (id INTEGER PRIMARY KEY, worker_id INTEGER, amount REAL, date TEXT, notes TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS work_logs 
                 (id INTEGER PRIMARY KEY, project_id INTEGER, date TEXT, progress REAL, material_loads REAL, notes TEXT)''')
    
    # --- NEW MIGRATIONS ---
    try: c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'admin'")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN project_id INTEGER")
    except: pass
    try: c.execute("ALTER TABLE expenses ADD COLUMN bill_path TEXT")
    except: pass
    try: c.execute("ALTER TABLE expenses ADD COLUMN entered_by TEXT")
    except: pass
    
    # Admin creation logic same...
    admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
    if not admin:
        hashed_pw = generate_password_hash('admin123', method='pbkdf2:sha256')
        c.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", ('admin', hashed_pw, 'admin'))
    conn.commit()
    conn.close()

init_db()

# --- ROUTES ---

@app.route('/sw.js')
def service_worker():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    return response

@app.route('/')
def public_home():
    conn = get_db_connection()
    projects = conn.execute("SELECT * FROM projects").fetchall()
    
    expenses_map = {row['project_id']: (row[1] or 0) for row in conn.execute("SELECT project_id, SUM(amount) FROM expenses GROUP BY project_id").fetchall()}
    payments_map = {row['project_id']: (row[1] or 0) for row in conn.execute("SELECT w.project_id, SUM(p.amount) FROM payments p JOIN workers w ON p.worker_id = w.id GROUP BY w.project_id").fetchall()}
    prog_rows = conn.execute("SELECT project_id, SUM(progress), COUNT(DISTINCT date) FROM work_logs GROUP BY project_id").fetchall()
    progress_map = {row['project_id']: {'done': (row[1] or 0), 'days': (row[2] or 0)} for row in prog_rows}

    # FIX: Group by Attendance Project ID (COALESCE handles old records)
    cost_mandays_rows = conn.execute('''
        SELECT 
            COALESCE(a.project_id, w.project_id) as pid, 
            SUM(CASE WHEN a.status='Present' THEN w.daily_wage WHEN a.status='Half Day' THEN w.daily_wage/2.0 ELSE 0 END),
            SUM(CASE WHEN a.status='Present' THEN 1 WHEN a.status='Half Day' THEN 0.5 ELSE 0 END)
        FROM workers w 
        JOIN attendance a ON w.id = a.worker_id 
        GROUP BY pid
    ''').fetchall()
    worker_stats_map = {row['pid']: {'cost': (row[1] or 0), 'mandays': (row[2] or 0)} for row in cost_mandays_rows}

    public_stats = []
    for p in projects:
        exp = expenses_map.get(p['id'], 0)
        pay = payments_map.get(p['id'], 0)
        
        workers = conn.execute("SELECT role FROM workers WHERE project_id=?", (p['id'],)).fetchall()
        mistri_count = sum(1 for w in workers if w['role'] == 'Mistri')
        labour_count = sum(1 for w in workers if w['role'] == 'Labour')
        
        ws = worker_stats_map.get(p['id'], {'cost': 0, 'mandays': 0})
        prog_data = progress_map.get(p['id'], {'done': 0, 'days': 0})
        
        target = p['target'] or 0
        est_days = "N/A"
        percent = 0
        if target > 0:
            percent = int((prog_data['done'] / target) * 100)
            if prog_data['done'] > 0 and prog_data['days'] > 0:
                avg_daily = prog_data['done'] / prog_data['days']
                remaining = target - prog_data['done']
                est_days = int(remaining / avg_daily) if remaining > 0 else "Done"
        
        public_stats.append({
            'name': p['name'], 'location': p['location'], 
            'expense': exp, 'payment': pay, 'grand_total': exp + pay,
            'mistri': mistri_count, 'labour': labour_count,
            'mandays': ws['mandays'],          
            'total_worker_cost': ws['cost'],
            'progress_done': prog_data['done'], 'progress_target': target, 
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
            # Load extra fields
            role = user['role'] if 'role' in user.keys() else 'admin'
            p_id = user['project_id'] if 'project_id' in user.keys() else None
            
            login_user(User(id=user['id'], username=user['username'], role=role, project_id=p_id))
            
            # REDIRECT LOGIC
            if role == 'foreman':
                return redirect(url_for('foreman_dashboard'))
            return redirect(url_for('dashboard'))
        else:
            flash('Login Failed.')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('public_home'))

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_pw = request.form['current_password']
        new_pw = request.form['new_password']
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE id=?", (current_user.id,)).fetchone()
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
    
    expenses_map = {row['project_id']: (row[1] or 0) for row in conn.execute("SELECT project_id, SUM(amount) FROM expenses GROUP BY project_id").fetchall()}
    payments_map = {row['project_id']: (row[1] or 0) for row in conn.execute("SELECT w.project_id, SUM(p.amount) FROM payments p JOIN workers w ON p.worker_id = w.id GROUP BY w.project_id").fetchall()}
    
    prog_rows = conn.execute("SELECT project_id, SUM(progress), SUM(material_loads), COUNT(DISTINCT date) FROM work_logs GROUP BY project_id").fetchall()
    progress_map = {row['project_id']: {'done': (row[1] or 0), 'mat': (row[2] or 0), 'days': (row[3] or 0)} for row in prog_rows}

    # FIX: Group by Attendance Project ID (Handles shifting)
    cost_mandays_rows = conn.execute('''
        SELECT 
            COALESCE(a.project_id, w.project_id) as pid, 
            SUM(CASE WHEN a.status='Present' THEN w.daily_wage WHEN a.status='Half Day' THEN w.daily_wage/2.0 ELSE 0 END),
            SUM(CASE WHEN a.status='Present' THEN 1 WHEN a.status='Half Day' THEN 0.5 ELSE 0 END)
        FROM workers w 
        JOIN attendance a ON w.id = a.worker_id 
        GROUP BY pid
    ''').fetchall()
    worker_stats_map = {row['pid']: {'cost': (row[1] or 0), 'mandays': (row[2] or 0)} for row in cost_mandays_rows}

    project_stats = []
    for p in projects:
        exp = expenses_map.get(p['id'], 0)
        pay = payments_map.get(p['id'], 0)
        
        workers = conn.execute("SELECT role FROM workers WHERE project_id=?", (p['id'],)).fetchall()
        m_count = sum(1 for w in workers if w['role'] == 'Mistri')
        l_count = sum(1 for w in workers if w['role'] == 'Labour')
        
        ws = worker_stats_map.get(p['id'], {'cost': 0, 'mandays': 0})
        prog_data = progress_map.get(p['id'], {'done': 0, 'mat': 0, 'days': 0})
        
        target = p['target'] or 0
        percent = int((prog_data['done'] / target) * 100) if target > 0 else 0
        rate = p['rate'] or 0
        estimated_income = prog_data['done'] * rate
        
        est_days = "N/A"
        if target > 0 and prog_data['done'] > 0 and prog_data['days'] > 0:
            avg = prog_data['done'] / prog_data['days']
            rem = target - prog_data['done']
            est_days = int(rem / avg) if rem > 0 else "Done"

        project_stats.append({
            'id': p['id'], 'name': p['name'], 'location': p['location'], 
            'total_expense': exp, 'total_payment': pay, 'grand_total': exp + pay,
            'm_count': m_count, 'l_count': l_count,
            'mandays': ws['mandays'],
            'total_worker_cost': ws['cost'],
            'progress_done': prog_data['done'], 'progress_target': target, 'progress_unit': p['unit'],
            'progress_percent': percent, 'material_used': prog_data['mat'], 'est_days': est_days, 
            'rate': rate, 'income': estimated_income
        })
    
    conn.close()
    return render_template('dashboard.html', t_mistri=t_mistri, t_labour=t_labour, p_stats=project_stats, idle_workers=idle_workers)

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

@app.route('/edit_expense/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    conn = get_db_connection()
    if request.method == 'POST':
        item = request.form['item']
        amount = request.form['amount']
        date_time = request.form['date_time'].replace('T', ' ')
        project_id = request.form['project_id']
        conn.execute('''UPDATE expenses SET item=?, amount=?, date_time=?, project_id=? WHERE id=?''', 
                     (item, amount, date_time, project_id, expense_id))
        conn.commit()
        conn.close()
        flash('Expense updated successfully!')
        return redirect(url_for('expense_log'))
    expense = conn.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
    projects = conn.execute("SELECT * FROM projects").fetchall()
    conn.close()
    return render_template('edit_expense.html', expense=expense, projects=projects)

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

# --- NEW: Project Payments List ---
@app.route('/project_payments/<int:project_id>')
@login_required
def project_payments(project_id):
    conn = get_db_connection()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    # Note: Payments are currently filtered by workers currently assigned to this project
    payments = conn.execute('''SELECT p.*, w.name, w.role 
                               FROM payments p 
                               JOIN workers w ON p.worker_id = w.id 
                               WHERE w.project_id=? 
                               ORDER BY p.date DESC''', (project_id,)).fetchall()
    conn.close()
    return render_template('project_payments.html', payments=payments, project=project)

@app.route('/print_expenses/<int:project_id>')
@login_required
def print_expenses(project_id):
    conn = get_db_connection()
    if project_id == 0:
        project_name = "All Projects"
        expenses = conn.execute('''SELECT e.*, p.name as project_name FROM expenses e 
                                   LEFT JOIN projects p ON e.project_id = p.id ORDER BY e.date_time DESC''').fetchall()
        payments = conn.execute('''SELECT p.*, w.name, w.role, proj.name as project_name 
                                   FROM payments p JOIN workers w ON p.worker_id = w.id 
                                   LEFT JOIN projects proj ON w.project_id = proj.id ORDER BY p.date DESC''').fetchall()
    else:
        project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        project_name = project['name']
        expenses = conn.execute("SELECT * FROM expenses WHERE project_id=? ORDER BY date_time DESC", (project_id,)).fetchall()
        payments = conn.execute('''SELECT p.*, w.name, w.role FROM payments p 
                                   JOIN workers w ON p.worker_id = w.id WHERE w.project_id=? ORDER BY p.date DESC''', (project_id,)).fetchall()
    
    total_exp = sum(e['amount'] for e in expenses)
    total_pay = sum(p['amount'] for p in payments)
    conn.close()
    return render_template('print_expenses.html', expenses=expenses, payments=payments, total_exp=total_exp, total_pay=total_pay, project_name=project_name)

@app.route('/project_workers/<int:project_id>')
@login_required
def project_workers(project_id):
    conn = get_db_connection()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    workers = conn.execute('''SELECT w.*, p.name as project_name FROM workers w 
                              LEFT JOIN projects p ON w.project_id = p.id 
                              WHERE w.project_id=?''', (project_id,)).fetchall()
    conn.close()
    return render_template('worker_list.html', workers=workers, title=f"Staff at {project['name']}")

@app.route('/add_project', methods=['POST'])
@login_required
def add_project():
    conn = get_db_connection()
    conn.execute("INSERT INTO projects (name, location, target, unit, rate) VALUES (?, ?, ?, ?, ?)", 
                 (request.form['name'], request.form['location'], 
                  request.form.get('target', 0), request.form.get('unit', 'Unit'), request.form.get('rate', 0)))
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/edit_project/<int:project_id>', methods=['POST'])
@login_required
def edit_project(project_id):
    conn = get_db_connection()
    conn.execute('UPDATE projects SET name=?, location=?, target=?, unit=?, rate=? WHERE id=?',
                 (request.form['name'], request.form['location'], request.form.get('target', 0), 
                  request.form.get('unit', 'Unit'), request.form.get('rate', 0), project_id))
    conn.commit()
    conn.close()
    flash('✅ Project Updated Successfully!')
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
        return redirect(url_for('add_worker')) 
    workers = conn.execute('''SELECT w.*, p.name as project_name FROM workers w 
                              LEFT JOIN projects p ON w.project_id = p.id ORDER BY w.id DESC''').fetchall()
    projects = conn.execute("SELECT * FROM projects").fetchall()
    conn.close()
    return render_template('add_worker.html', workers=workers, projects=projects)

@app.route('/add_expense', methods=['POST'])
@login_required
def add_expense():
    user_date = request.form.get('date_time')
    final_date = user_date.replace('T', ' ') if user_date else datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # --- Image Upload Logic (Camera + File Support) ---
    # Pehle normal upload check karega, agar khali hai to camera input check karega
    file = request.files.get('bill_photo')
    if not file or file.filename == '':
        file = request.files.get('bill_camera') # Camera input

    filename = None
    if file and file.filename != '':
        # Filename secure karo
        ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'jpg'
        filename = "bill_" + datetime.now().strftime('%Y%m%d%H%M%S') + "." + ext
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

    # Identify who entered
    entered_by = f"{current_user.username} ({current_user.role})"

    conn = get_db_connection()
    conn.execute("INSERT INTO expenses (item, amount, date_time, project_id, bill_path, entered_by) VALUES (?, ?, ?, ?, ?, ?)", 
                 (request.form['item'], request.form['amount'], final_date, request.form['project_id'], filename, entered_by))
    conn.commit()
    conn.close()
    
    return redirect(request.referrer or url_for('expense_log'))

@app.route('/expense_log')
@login_required
def expense_log():
    conn = get_db_connection()
    
    # Default back link (Admin ke liye)
    back_url = url_for('dashboard') 
    
    if current_user.role == 'foreman':
        pid = current_user.project_id
        project = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        project_name = project['name'] if project else "My Site"
        current_pid = pid
        
        # --- FIX: Filter by Entered By ---
        # Sirf wahi rows uthao jisme entered_by me current username match ho
        user_signature = f"{current_user.username}%"
        
        expenses = conn.execute('''SELECT e.*, p.name as project_name FROM expenses e 
                                   LEFT JOIN projects p ON e.project_id = p.id 
                                   WHERE e.project_id=? AND e.entered_by LIKE ?
                                   ORDER BY e.date_time DESC''', (pid, user_signature)).fetchall()
        
        # Foreman ke liye back link
        back_url = url_for('foreman_dashboard')
        projects = [] 
        
    else:
        # Admin Logic (Sab dikhega)
        projects = conn.execute("SELECT * FROM projects").fetchall()
        expenses = conn.execute('''SELECT e.*, p.name as project_name FROM expenses e 
                                   LEFT JOIN projects p ON e.project_id = p.id ORDER BY e.date_time DESC''').fetchall()
        project_name = "All Projects"
        current_pid = 0

    conn.close()
    return render_template('expense_log.html', expenses=expenses, project_name=project_name, 
                           projects=projects, current_pid=current_pid, back_url=back_url)

@app.route('/attendance', methods=['GET', 'POST'])
@login_required
def attendance():
    conn = get_db_connection()
    
    # --- GET: Display Page ---
    if request.method == 'GET':
        selected_date = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
        today_date = datetime.now().strftime('%Y-%m-%d')

        # Restriction: Foreman can only see/edit Today
        if current_user.role == 'foreman' and selected_date != today_date:
            flash("⚠️ Foreman sirf aaj ki attendance laga sakta hai!")
            return redirect(url_for('attendance', date=today_date))

        # Workers fetch logic...
        workers_query = "SELECT w.*, p.name as project_name FROM workers w LEFT JOIN projects p ON w.project_id = p.id WHERE w.project_id IS NOT NULL AND w.project_id > 0"
        
        # Agar foreman hai to sirf uske project ke workers dikhao
        if current_user.role == 'foreman':
            workers = conn.execute(workers_query + " AND w.project_id=?", (current_user.project_id,)).fetchall()
        else:
            workers = conn.execute(workers_query + " ORDER BY w.role, w.name").fetchall()
        
        existing_att = conn.execute("SELECT worker_id, status FROM attendance WHERE date=?", (selected_date,)).fetchall()
        status_map = {row['worker_id']: row['status'] for row in existing_att}

        conn.close()
        return render_template('attendance.html', workers=workers, today_date=selected_date, status_map=status_map)

    # --- POST: Save Attendance ---
    if request.method == 'POST':
        date = request.form['attendance_date']
        today_str = datetime.now().strftime('%Y-%m-%d')

        # SECURITY CHECK: Foreman previous date edit nahi kar sakta
        if current_user.role == 'foreman' and date != today_str:
            flash("🚫 Error: Aap purani date ki attendance change nahi kar sakte.")
            return redirect(url_for('foreman_dashboard'))

        workers = conn.execute("SELECT id, project_id FROM workers").fetchall()
        for w in workers:
            status = request.form.get(f'status_{w["id"]}')
            if status:
                exists = conn.execute("SELECT id FROM attendance WHERE worker_id=? AND date=?", (w['id'], date)).fetchone()
                if exists:
                    conn.execute("UPDATE attendance SET status=?, project_id=? WHERE id=?", (status, w['project_id'], exists['id']))
                else:
                    conn.execute("INSERT INTO attendance (worker_id, date, status, project_id) VALUES (?, ?, ?, ?)", (w['id'], date, status, w['project_id']))
        conn.commit()
        
        if current_user.role == 'foreman':
            return redirect(url_for('foreman_dashboard'))
        return redirect(url_for('attendance_report'))

@app.route('/attendance_report')
@login_required
def attendance_report():
    conn = get_db_connection()
    dates = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
    
    workers = conn.execute("SELECT * FROM workers WHERE project_id IS NOT NULL AND project_id > 0").fetchall()
    worker_report = []
    for w in workers:
        w_row = {'name': w['name'], 'role': w['role'], 'days': []}
        for d in dates:
            stat = conn.execute("SELECT status FROM attendance WHERE worker_id=? AND date=?", (w['id'], d)).fetchone()
            w_row['days'].append(stat['status'] if stat else '-')
        worker_report.append(w_row)
        
    projects = conn.execute("SELECT * FROM projects").fetchall()
    site_report = []
    for p in projects:
        p_row = {'name': p['name'], 'unit': p['unit'], 'days': []}
        for d in dates:
            log = conn.execute("SELECT SUM(progress) as progress, SUM(material_loads) as material_loads FROM work_logs WHERE project_id=? AND date=?", (p['id'], d)).fetchone()
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
    # --- SECURITY CHECK: Sirf Admin hi access kar sakta hai ---
    if current_user.role != 'admin':
        flash("🚫 Access Denied! Payments page is for Admins only.")
        return redirect(url_for('foreman_dashboard'))
    
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute("INSERT INTO payments (worker_id, amount, date) VALUES (?, ?, ?)", 
                     (request.form['worker_id'], request.form['amount'], request.form['date']))
        conn.commit()
        return redirect(url_for('payments'))
    
    workers = conn.execute('''SELECT w.*, p.name as project_name FROM workers w LEFT JOIN projects p ON w.project_id = p.id''').fetchall()
    
    att_map = {} 
    att_rows = conn.execute("SELECT worker_id, status, COUNT(*) FROM attendance GROUP BY worker_id, status").fetchall()
    for row in att_rows:
        wid = row['worker_id']
        status = row['status']
        if wid not in att_map: att_map[wid] = {'Present': 0, 'Half Day': 0}
        if status in att_map[wid]: att_map[wid][status] = row[2]
        
    pay_map = {}
    pay_rows = conn.execute("SELECT worker_id, SUM(amount) FROM payments GROUP BY worker_id").fetchall()
    for row in pay_rows:
        pay_map[row['worker_id']] = row[1] or 0

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
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    # --- Date Range Logic ---
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
    
    total_mandays = 0
    total_work_done = {} 
    
    # --- 1. Site Work Report (Same as before) ---
    projects = conn.execute("SELECT * FROM projects").fetchall()
    site_report = []
    for p in projects:
        p_row = {'name': p['name'], 'unit': p['unit'], 'days': [], 'total_site_work': 0}
        for d in dates:
            log = conn.execute("SELECT SUM(progress) as progress, SUM(material_loads) as material_loads FROM work_logs WHERE project_id=? AND date=?", (p['id'], d)).fetchone()
            if log and log['progress'] is not None:
                progress = log['progress'] or 0
                mat_loads = log['material_loads'] or 0
                p_row['days'].append({'work': progress, 'mat': mat_loads})
                p_row['total_site_work'] += progress
                u = p['unit'] or 'Unit'
                if u not in total_work_done: total_work_done[u] = 0
                total_work_done[u] += progress
            else:
                p_row['days'].append(None)
        site_report.append(p_row)

    # --- 2. Worker Attendance Report (FIXED LOGIC) ---
    # Query updated: Removed "WHERE project_id > 0". Now fetches ALL workers.
    workers = conn.execute('''SELECT w.*, p.name as project_name FROM workers w 
                              LEFT JOIN projects p ON w.project_id = p.id 
                              ORDER BY p.name, w.role''').fetchall()
    
    worker_report = []
    for w in workers:
        # Default Project Name if Idle
        p_name = w['project_name'] if w['project_name'] else 'Idle/Left'
        w_row = {'name': w['name'], 'role': w['role'], 'project_name': p_name, 'days': [], 'p_count': 0}
        
        has_attendance_in_range = False
        
        for d in dates:
            stat = conn.execute("SELECT status FROM attendance WHERE worker_id=? AND date=?", (w['id'], d)).fetchone()
            status = stat['status'] if stat else '-'
            w_row['days'].append(status)
            
            # Check if worker has ANY data in this range
            if status != '-':
                has_attendance_in_range = True
            
            if status == 'Present':
                w_row['p_count'] += 1
            elif status == 'Half Day':
                w_row['p_count'] += 0.5
        
        # LOGIC: Report me tabhi dikhao agar (Abhi Active Hai) YA (Is range me Attendance hai)
        if has_attendance_in_range or (w['project_id'] and w['project_id'] > 0):
            worker_report.append(w_row)
            total_mandays += w_row['p_count']

    conn.close()
    return render_template('print_attendance.html', dates=dates, site_report=site_report, worker_report=worker_report,
                           start_date=start_date.strftime('%Y-%m-%d'), end_date=end_date.strftime('%Y-%m-%d'),
                           total_mandays=total_mandays, total_work_done=total_work_done)

@app.route('/foreman_dashboard')
@login_required
def foreman_dashboard():
    if current_user.role != 'foreman':
        return redirect(url_for('dashboard'))
    
    pid = current_user.project_id
    if not pid:
        flash("No project assigned.")
        return redirect(url_for('logout'))

    conn = get_db_connection()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    
    # Workers count
    workers = conn.execute("SELECT role FROM workers WHERE project_id=?", (pid,)).fetchall()
    m_count = sum(1 for w in workers if w['role'] == 'Mistri')
    l_count = sum(1 for w in workers if w['role'] == 'Labour')

    # Progress
    total_prog = conn.execute("SELECT SUM(progress) FROM work_logs WHERE project_id=?", (pid,)).fetchone()[0] or 0
    
    # Check if attendance marked today
    today = datetime.now().strftime('%Y-%m-%d')
    att_done = conn.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND project_id=?", (today, pid)).fetchone()[0]
    
    target = project['target'] or 0
    percent = int((total_prog / target) * 100) if target > 0 else 0

    conn.close()
    return render_template('foreman_dashboard.html', p=project, m_count=m_count, l_count=l_count, 
                           percent=percent, total_prog=total_prog, att_done=att_done)

@app.route('/make_foreman/<int:worker_id>')
@login_required
def make_foreman(worker_id):
    if current_user.role != 'admin':
        flash('Only Admin can create Foremen!')
        return redirect(url_for('dashboard'))
        
    conn = get_db_connection()
    worker = conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
    
    if worker:
        # Check if worker is assigned to a project
        if not worker['project_id'] or worker['project_id'] == 0:
            flash('Error: Foreman must be assigned to a project first (Cannot be Idle).')
            conn.close()
            return redirect(url_for('worker_profile', worker_id=worker_id))

        username = worker['name'].split()[0].lower() + str(worker['id'])
        exists = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        
        if not exists:
            hashed_pw = generate_password_hash('123456', method='pbkdf2:sha256')
            # Save Project ID to User Table
            conn.execute("INSERT INTO users (username, password, role, project_id) VALUES (?, ?, ?, ?)", 
                         (username, hashed_pw, 'foreman', worker['project_id']))
            flash(f"Foreman Created! Username: {username} | Password: 123456")
        else:
            flash(f"User already exists: {username}")
            
    conn.commit()
    conn.close()
    return redirect(url_for('worker_profile', worker_id=worker_id))

@app.route('/salary_slip/<int:worker_id>')
@login_required
def salary_slip(worker_id):
    conn = get_db_connection()
    
    # --- Date Filter Logic (Default: Current Month) ---
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if not start_date or not end_date:
        today = datetime.now()
        # Default: 1st day of current month to Today
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')

    # Worker Details
    worker = conn.execute('''SELECT w.*, p.name as project_name, p.location 
                             FROM workers w 
                             LEFT JOIN projects p ON w.project_id = p.id 
                             WHERE w.id=?''', (worker_id,)).fetchone()

    # --- 1. Attendance Calculation (Between Dates) ---
    att_rows = conn.execute('''SELECT status, COUNT(*) as cnt 
                               FROM attendance 
                               WHERE worker_id=? AND date BETWEEN ? AND ? 
                               GROUP BY status''', (worker_id, start_date, end_date)).fetchall()
    
    p_days = 0
    h_days = 0
    for row in att_rows:
        if row['status'] == 'Present': p_days = row['cnt']
        elif row['status'] == 'Half Day': h_days = row['cnt']
    
    total_mandays = p_days + (h_days * 0.5)
    total_earned = total_mandays * worker['daily_wage']

    # --- 2. Payments/Advances Taken (Between Dates) ---
    advances = conn.execute('''SELECT * FROM payments 
                               WHERE worker_id=? AND date BETWEEN ? AND ? 
                               ORDER BY date''', (worker_id, start_date, end_date)).fetchall()
    
    total_taken = sum(a['amount'] for a in advances)
    net_payable = total_earned - total_taken

    conn.close()
    return render_template('salary_slip.html', w=worker, start_date=start_date, end_date=end_date,
                           p_days=p_days, h_days=h_days, total_mandays=total_mandays,
                           total_earned=total_earned, advances=advances, total_taken=total_taken,
                           net_payable=net_payable, generated_on=datetime.now())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)