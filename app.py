







from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
import sqlite3
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import csv
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
import os
from io import BytesIO
import base64

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# Database path
DATABASE = 'expense_tracker.db'

# Password hasher
ph = PasswordHasher()

# ===== HELPER FUNCTIONS =====

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables."""
    with get_db() as conn:
        c = conn.cursor()

        # Users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Expenses table
        c.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                date TEXT NOT NULL,
                description TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        # Budgets table
        c.execute('''
            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                monthly_limit REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, category)
            )
        ''')

        # Recurring expenses table
        c.execute('''
            CREATE TABLE IF NOT EXISTS recurring_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                day_of_month INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                last_processed TEXT,
                active BOOLEAN DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        conn.commit()

# ===== USER MODEL =====

class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, username, email FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
        if user:
            return User(user['id'], user['username'], user['email'])
    return None

# ===== ROUTES =====

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        if not username or not email or not password:
            flash('All fields are required!', 'error')
            return render_template('register.html')

        try:
            password_hash = ph.hash(password)

            with get_db() as conn:
                c = conn.cursor()
                c.execute('''
                    INSERT INTO users (username, email, password_hash)
                    VALUES (?, ?, ?)
                ''', (username, email, password_hash))
                conn.commit()

            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))

        except sqlite3.IntegrityError:
            flash('Username or email already exists!', 'error')
        except Exception as e:
            flash(f'Registration error: {e}', 'error')

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT id, username, email, password_hash FROM users WHERE username = ? OR email = ?',
                     (username, username))
            user = c.fetchone()

            if user:
                try:
                    ph.verify(user['password_hash'], password)
                    user_obj = User(user['id'], user['username'], user['email'])
                    login_user(user_obj)
                    flash(f'Welcome back, {user["username"]}!', 'success')
                    return redirect(url_for('dashboard'))
                except VerifyMismatchError:
                    flash('Invalid password.', 'error')
            else:
                flash('User not found.', 'error')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    with get_db() as conn:
        c = conn.cursor()

        # Get total expenses
        c.execute('SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id = ?', (current_user.id,))
        total = c.fetchone()[0]

        # Get category breakdown
        c.execute('''
            SELECT category, COALESCE(SUM(amount), 0) as total
            FROM expenses
            WHERE user_id = ?
            GROUP BY category
            ORDER BY total DESC
        ''', (current_user.id,))
        categories = c.fetchall()

        # Get monthly spending for chart
        months = 6
        labels = []
        data = []
        for i in range(months - 1, -1, -1):
            date = datetime.now() - timedelta(days=30 * i)
            label = date.strftime("%b %Y")
            start = date.replace(day=1).strftime("%Y-%m-%d")
            if date.month == 12:
                end = date.replace(year=date.year + 1, month=1, day=1).strftime("%Y-%m-%d")
            else:
                end = date.replace(month=date.month + 1, day=1).strftime("%Y-%m-%d")

            c.execute('''
                SELECT COALESCE(SUM(amount), 0)
                FROM expenses
                WHERE user_id = ? AND date >= ? AND date < ?
            ''', (current_user.id, start, end))
            data.append(c.fetchone()[0])
            labels.append(label)

        return render_template('dashboard.html',
                             total=total,
                             categories=categories,
                             labels=labels,
                             data=data)

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount'))
            category = request.form.get('category')
            date = request.form.get('date') or datetime.now().strftime("%Y-%m-%d")
            description = request.form.get('description', '')

            with get_db() as conn:
                c = conn.cursor()
                c.execute('''
                    INSERT INTO expenses (user_id, amount, category, date, description)
                    VALUES (?, ?, ?, ?, ?)
                ''', (current_user.id, amount, category, date, description))
                conn.commit()

            flash(f'Expense added: €{amount:.2f} for {category}', 'success')
            return redirect(url_for('dashboard'))

        except ValueError:
            flash('Invalid amount. Please enter a number.', 'error')
        except Exception as e:
            flash(f'Error: {e}', 'error')

    return render_template('add_expense.html')

@app.route('/edit/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM expenses WHERE id = ? AND user_id = ?', (expense_id, current_user.id))
        expense = c.fetchone()

        if not expense:
            flash('Expense not found.', 'error')
            return redirect(url_for('dashboard'))

    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount'))
            category = request.form.get('category')
            date = request.form.get('date')
            description = request.form.get('description', '')

            with get_db() as conn:
                c = conn.cursor()
                c.execute('''
                    UPDATE expenses
                    SET amount = ?, category = ?, date = ?, description = ?
                    WHERE id = ? AND user_id = ?
                ''', (amount, category, date, description, expense_id, current_user.id))
                conn.commit()

            flash('Expense updated successfully!', 'success')
            return redirect(url_for('dashboard'))

        except ValueError:
            flash('Invalid amount. Please enter a number.', 'error')
        except Exception as e:
            flash(f'Error: {e}', 'error')

    return render_template('edit_expense.html', expense=expense)

@app.route('/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM expenses WHERE id = ? AND user_id = ?', (expense_id, current_user.id))
        conn.commit()

    flash('Expense deleted.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/budgets', methods=['GET', 'POST'])
@login_required
def budgets():
    if request.method == 'POST':
        category = request.form.get('category')
        limit = request.form.get('limit')

        if not category or not limit:
            flash('Category and limit are required.', 'error')
            return redirect(url_for('budgets'))

        try:
            limit = float(limit)
            with get_db() as conn:
                c = conn.cursor()
                c.execute('''
                    INSERT INTO budgets (user_id, category, monthly_limit)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, category) DO UPDATE SET monthly_limit = excluded.monthly_limit
                ''', (current_user.id, category, limit))
                conn.commit()
            flash(f'Budget set for {category}: €{limit:.2f}', 'success')
        except ValueError:
            flash('Invalid limit. Please enter a number.', 'error')
        except Exception as e:
            flash(f'Error: {e}', 'error')

        return redirect(url_for('budgets'))

    # Get budget status
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT category, monthly_limit FROM budgets WHERE user_id = ?', (current_user.id,))
        budgets_list = c.fetchall()

        month = datetime.now().month
        year = datetime.now().year
        start_date = f"{year:04d}-{month:02d}-01"
        if month == 12:
            end_date = f"{year + 1:04d}-01-01"
        else:
            end_date = f"{year:04d}-{month + 1:02d}-01"

        results = []
        for budget in budgets_list:
            c.execute('''
                SELECT COALESCE(SUM(amount), 0)
                FROM expenses
                WHERE user_id = ? AND category = ? AND date >= ? AND date < ?
            ''', (current_user.id, budget['category'], start_date, end_date))
            spent = c.fetchone()[0]

            limit = budget['monthly_limit']
            remaining = limit - spent
            percentage = (spent / limit * 100) if limit > 0 else 0

            if percentage >= 100:
                status = 'exceeded'
            elif percentage >= 80:
                status = 'warning'
            else:
                status = 'ok'

            results.append({
                'category': budget['category'],
                'limit': limit,
                'spent': spent,
                'remaining': remaining,
                'percentage': percentage,
                'status': status
            })

    return render_template('budgets.html', budgets=results)

@app.route('/recurring', methods=['GET', 'POST'])
@login_required
def recurring():
    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount'))
            category = request.form.get('category')
            day_of_month = int(request.form.get('day_of_month'))
            description = request.form.get('description', '')
            start_date = request.form.get('start_date') or datetime.now().strftime("%Y-%m-%d")
            end_date = request.form.get('end_date') or None

            with get_db() as conn:
                c = conn.cursor()
                c.execute('''
                    INSERT INTO recurring_expenses
                    (user_id, amount, category, description, day_of_month, start_date, end_date, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ''', (current_user.id, amount, category, description, day_of_month, start_date, end_date))
                conn.commit()

            flash(f'Recurring expense added: €{amount:.2f} for {category} on day {day_of_month}', 'success')
            return redirect(url_for('recurring'))

        except ValueError:
            flash('Invalid input. Please check your values.', 'error')
        except Exception as e:
            flash(f'Error: {e}', 'error')

    # Get recurring expenses
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, amount, category, description, day_of_month, start_date, end_date, last_processed, active
            FROM recurring_expenses
            WHERE user_id = ?
            ORDER BY day_of_month
        ''', (current_user.id,))
        recurring_list = c.fetchall()

    return render_template('recurring.html', recurring=recurring_list)

@app.route('/process_recurring', methods=['POST'])
@login_required
def process_recurring():
    today = datetime.now().strftime("%Y-%m-%d")

    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, amount, category, description, day_of_month, start_date, end_date, last_processed
            FROM recurring_expenses
            WHERE user_id = ? AND active = 1
        ''', (current_user.id,))
        recurring = c.fetchall()

        processed = 0
        for rec in recurring:
            if rec['start_date'] and today < rec['start_date']:
                continue
            if rec['end_date'] and today > rec['end_date']:
                continue

            if rec['last_processed']:
                last = datetime.strptime(rec['last_processed'], "%Y-%m-%d")
                now = datetime.strptime(today, "%Y-%m-%d")
                if last.month == now.month and last.year == now.year:
                    continue

            day = rec['day_of_month']
            now = datetime.strptime(today, "%Y-%m-%d")

            import calendar
            last_day = calendar.monthrange(now.year, now.month)[1]
            if day > last_day:
                day = last_day

            if now.day != day:
                continue

            desc = f"{rec['description']} (recurring)" if rec['description'] else f"Recurring {rec['category']}"
            c.execute('''
                INSERT INTO expenses (user_id, amount, category, date, description)
                VALUES (?, ?, ?, ?, ?)
            ''', (current_user.id, rec['amount'], rec['category'], today, desc))

            c.execute('''
                UPDATE recurring_expenses SET last_processed = ? WHERE id = ?
            ''', (today, rec['id']))

            processed += 1

        conn.commit()

    flash(f'Processed {processed} recurring expense(s)', 'success')
    return redirect(url_for('recurring'))

@app.route('/toggle_recurring/<int:rec_id>', methods=['POST'])
@login_required
def toggle_recurring(rec_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT active FROM recurring_expenses WHERE id = ? AND user_id = ?', (rec_id, current_user.id))
        result = c.fetchone()

        if result:
            new_status = 0 if result['active'] else 1
            c.execute('UPDATE recurring_expenses SET active = ? WHERE id = ?', (new_status, rec_id))
            conn.commit()
            flash('Recurring expense toggled.', 'success')
        else:
            flash('Recurring expense not found.', 'error')

    return redirect(url_for('recurring'))

@app.route('/delete_recurring/<int:rec_id>', methods=['POST'])
@login_required
def delete_recurring(rec_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM recurring_expenses WHERE id = ? AND user_id = ?', (rec_id, current_user.id))
        conn.commit()

    flash('Recurring expense deleted.', 'success')
    return redirect(url_for('recurring'))

@app.route('/export_csv')
@login_required
def export_csv():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, amount, category, date, description
            FROM expenses
            WHERE user_id = ?
            ORDER BY date DESC
        ''', (current_user.id,))
        rows = c.fetchall()

    if not rows:
        flash('No expenses to export.', 'warning')
        return redirect(url_for('dashboard'))

    from flask import Response
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Amount', 'Category', 'Date', 'Description'])

    for row in rows:
        writer.writerow([row['id'], f"{row['amount']:.2f}", row['category'], row['date'], row['description'] or ''])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=expenses_{datetime.now().strftime("%Y%m%d")}.csv'}
    )

@app.route('/chart/<chart_type>')
@login_required
def chart(chart_type):
    with get_db() as conn:
        c = conn.cursor()

        if chart_type == 'pie':
            c.execute('''
                SELECT category, COALESCE(SUM(amount), 0) as total
                FROM expenses
                WHERE user_id = ?
                GROUP BY category
                HAVING total > 0
            ''', (current_user.id,))
            data = c.fetchall()

            if not data:
                flash('No data to chart.', 'warning')
                return redirect(url_for('dashboard'))

            categories = [row['category'] for row in data]
            amounts = [row['total'] for row in data]

            fig, ax = plt.subplots(figsize=(8, 6))
            colors = plt.cm.Set3(range(len(categories)))
            ax.pie(amounts, labels=categories, autopct='%1.1f%%', colors=colors, startangle=90)
            ax.set_title('Spending by Category')

        elif chart_type == 'bar':
            c.execute('''
                SELECT category, COALESCE(SUM(amount), 0) as total
                FROM expenses
                WHERE user_id = ?
                GROUP BY category
                HAVING total > 0
                ORDER BY total DESC
                LIMIT 10
            ''', (current_user.id,))
            data = c.fetchall()

            if not data:
                flash('No data to chart.', 'warning')
                return redirect(url_for('dashboard'))

            categories = [row['category'] for row in data]
            amounts = [row['total'] for row in data]

            fig, ax = plt.subplots(figsize=(10, 6))
            colors = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6']
            ax.bar(categories, amounts, color=colors[:len(categories)], edgecolor='black', linewidth=0.5)
            ax.set_title('Spending by Category')
            ax.set_xlabel('Category')
            ax.set_ylabel('Amount (€)')
            plt.xticks(rotation=45, ha='right')

        elif chart_type == 'trend':
            months = 6
            labels = []
            amounts = []
            for i in range(months - 1, -1, -1):
                date = datetime.now() - timedelta(days=30 * i)
                label = date.strftime("%b %Y")
                start = date.replace(day=1).strftime("%Y-%m-%d")
                if date.month == 12:
                    end = date.replace(year=date.year + 1, month=1, day=1).strftime("%Y-%m-%d")
                else:
                    end = date.replace(month=date.month + 1, day=1).strftime("%Y-%m-%d")

                c.execute('''
                    SELECT COALESCE(SUM(amount), 0)
                    FROM expenses
                    WHERE user_id = ? AND date >= ? AND date < ?
                ''', (current_user.id, start, end))
                amounts.append(c.fetchone()[0])
                labels.append(label)

            if sum(amounts) == 0:
                flash('No data to chart.', 'warning')
                return redirect(url_for('dashboard'))

            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(labels, amounts, marker='o', linewidth=2, markersize=8, color='#3b82f6')
            ax.fill_between(labels, 0, amounts, alpha=0.3, color='#3b82f6')
            ax.set_title('Monthly Spending Trend')
            ax.set_xlabel('Month')
            ax.set_ylabel('Amount (€)')
            ax.grid(True, alpha=0.3)

            for i, (label, value) in enumerate(zip(labels, amounts)):
                ax.annotate(f'€{value:.2f}', (i, value), textcoords="offset points", xytext=(0, 10), ha='center', fontsize=8)

        else:
            return redirect(url_for('dashboard'))

    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    plt.close()

    img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    return render_template('chart.html', chart_type=chart_type, chart_data=img_base64)

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)