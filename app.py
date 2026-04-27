import os
import hashlib
import sqlite3
import smtplib
import difflib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps

import requests
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DATABASE = os.environ.get("DATABASE_URL", "priceradar.db")

# --------------- DB helpers ---------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS competitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            pricing_url TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            content_hash TEXT NOT NULL,
            text_content TEXT,
            html_snippet TEXT,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            diff_summary TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            seen BOOLEAN DEFAULT 0
        );
    """)
    db.close()


# --------------- Auth helpers ---------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Faca login para continuar.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if "user_id" in session:
        return get_db().execute(
            "SELECT * FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
    return None


# --------------- Scraper logic ---------------

def scrape_pricing_page(url):
    headers = {"User-Agent": "PriceRadar/1.0 (pricing monitor bot)"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[SCRAPER] Error fetching {url}: {e}")
        return None


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def compute_diff(old_text, new_text):
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=3))
    return "\n".join(diff[:80])


# --------------- Email ---------------

def send_alert_email(to_email, competitor_name, pricing_url, diff_summary, comp_id):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    from_email = os.environ.get("FROM_EMAIL", smtp_user or "noreply@priceradar.app")

    if not all([smtp_host, smtp_user, smtp_pass]):
        print(f"[EMAIL SKIP] SMTP not configured. Would notify {to_email} about {competitor_name}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[PriceRadar] Mudanca detectada: {competitor_name}"
    msg["From"] = from_email
    msg["To"] = to_email

    text_body = f"Mudanca detectada em {competitor_name} ({pricing_url}).\n\nDiff:\n{diff_summary}"
    html_body = f"""
    <h2>Mudanca de preco detectada!</h2>
    <p><strong>Concorrente:</strong> {competitor_name}</p>
    <p><strong>URL:</strong> {pricing_url}</p>
    <h3>Resumo das mudancas:</h3>
    <pre style="background:#0f0f1a;color:#6ee7b7;padding:16px;border-radius:8px;overflow-x:auto;font-size:13px">{diff_summary}</pre>
    """
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, int(os.environ.get("SMTP_PORT", 587))) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"[EMAIL] Sent alert to {to_email} about {competitor_name}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


# --------------- Scheduled job ---------------

def run_scraper():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")

    rows = db.execute("""
        SELECT c.*, u.email as user_email
        FROM competitors c JOIN users u ON c.user_id = u.id
    """).fetchall()

    for comp in rows:
        html = scrape_pricing_page(comp["pricing_url"])

        if html is None:
            db.execute("""
                INSERT INTO alerts (snapshot_id, competitor_id, diff_summary)
                VALUES (0, ?, ?)
            """, (comp["id"], "Pagina fora do ar. Nao foi possivel acessar a URL."))
            db.commit()
            continue

        text = extract_text(html)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        prev = db.execute("""
            SELECT * FROM snapshots WHERE competitor_id = ?
            ORDER BY captured_at DESC LIMIT 1
        """, (comp["id"],)).fetchone()

        snippet = html[:5000]
        cur = db.execute("""
            INSERT INTO snapshots (competitor_id, content_hash, text_content, html_snippet)
            VALUES (?, ?, ?, ?)
        """, (comp["id"], content_hash, text, snippet))
        snap_id = cur.lastrowid
        db.commit()

        if prev and prev["content_hash"] != content_hash:
            diff_summary = compute_diff(prev["text_content"], text)
            db.execute("""
                INSERT INTO alerts (snapshot_id, competitor_id, diff_summary)
                VALUES (?, ?, ?)
            """, (snap_id, comp["id"], diff_summary))
            db.commit()
            send_alert_email(
                comp["user_email"], comp["name"],
                comp["pricing_url"], diff_summary, comp["id"]
            )

    print(f"[SCRAPER] Scanned {len(rows)} competitors.")
    db.close()


# --------------- Routes ---------------

@app.route("/")
def landing():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        flash("Email e senha sao obrigatorios.", "error")
        return render_template("register.html"), 400

    if len(password) < 6:
        flash("A senha deve ter pelo menos 6 caracteres.", "error")
        return render_template("register.html"), 400

    db = get_db()
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        flash("Email ja cadastrado.", "error")
        return render_template("register.html"), 409

    pw_hash = generate_password_hash(password)
    db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, pw_hash))
    db.commit()

    user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    session["user_id"] = user["id"]
    session["user_email"] = email
    flash("Conta criada com sucesso!", "success")
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        flash("Email ou senha incorretos.", "error")
        return render_template("login.html"), 401

    session["user_id"] = user["id"]
    session["user_email"] = user["email"]
    flash("Login realizado!", "success")
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]
    user = current_user()

    competitors = db.execute("""
        SELECT c.*,
            (SELECT s.content_hash FROM snapshots s
             WHERE s.competitor_id = c.id ORDER BY s.captured_at DESC LIMIT 1) as last_hash,
            (SELECT COUNT(*) FROM alerts a
             WHERE a.competitor_id = c.id AND a.seen = 0) as unseen_alerts,
            (SELECT s.captured_at FROM snapshots s
             WHERE s.competitor_id = c.id ORDER BY s.captured_at DESC LIMIT 1) as last_check
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()

    recent_alerts = db.execute("""
        SELECT a.*, c.name as competitor_name
        FROM alerts a
        JOIN competitors c ON a.competitor_id = c.id
        WHERE c.user_id = ?
        ORDER BY a.sent_at DESC LIMIT 10
    """, (uid,)).fetchall()

    max_comp = 5 if user["plan"] == "free" else 50
    return render_template(
        "dashboard.html",
        competitors=competitors,
        alerts=recent_alerts,
        user=user,
        max_competitors=max_comp,
    )


@app.route("/competitors", methods=["POST"])
@login_required
def add_competitor():
    db = get_db()
    uid = session["user_id"]
    user = current_user()

    count = db.execute(
        "SELECT COUNT(*) as c FROM competitors WHERE user_id = ?", (uid,)
    ).fetchone()["c"]
    max_comp = 5 if user["plan"] == "free" else 50

    if count >= max_comp:
        flash(f"Limite de {max_comp} concorrentes atingido no plano {user['plan']}.", "error")
        return redirect(url_for("dashboard"))

    name = request.form.get("name", "").strip()
    pricing_url = request.form.get("pricing_url", "").strip()

    if not name or not pricing_url:
        flash("Nome e URL sao obrigatorios.", "error")
        return redirect(url_for("dashboard"))

    if not pricing_url.startswith(("http://", "https://")):
        flash("URL deve comecar com http:// ou https://", "error")
        return redirect(url_for("dashboard"))

    db.execute(
        "INSERT INTO competitors (user_id, name, pricing_url) VALUES (?, ?, ?)",
        (uid, name, pricing_url),
    )
    db.commit()
    flash(f"Concorrente '{name}' adicionado!", "success")
    return redirect(url_for("dashboard"))


@app.route("/competitors/<int:comp_id>", methods=["DELETE"])
@login_required
def remove_competitor(comp_id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (comp_id, uid)
    ).fetchone()
    if not comp:
        return jsonify({"error": "Nao encontrado"}), 404

    db.execute("DELETE FROM competitors WHERE id = ? AND user_id = ?", (comp_id, uid))
    db.commit()

    if request.headers.get("HX-Request"):
        return ""
    return "", 200


@app.route("/competitors/<int:comp_id>/history")
@login_required
def snapshot_history(comp_id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (comp_id, uid)
    ).fetchone()
    if not comp:
        flash("Concorrente nao encontrado.", "error")
        return redirect(url_for("dashboard"))

    snapshots = db.execute("""
        SELECT s.*,
            (SELECT s2.text_content FROM snapshots s2
             WHERE s2.competitor_id = s.competitor_id AND s2.captured_at < s.captured_at
             ORDER BY s2.captured_at DESC LIMIT 1) as prev_text
        FROM snapshots s
        WHERE s.competitor_id = ?
        ORDER BY s.captured_at DESC
    """, (comp_id,)).fetchall()

    alerts = db.execute(
        "SELECT * FROM alerts WHERE competitor_id = ? ORDER BY sent_at DESC",
        (comp_id,),
    ).fetchall()

    return render_template("history.html", competitor=comp, snapshots=snapshots, alerts=alerts)


@app.route("/competitors/<int:comp_id>/check", methods=["POST"])
@login_required
def manual_check(comp_id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (comp_id, uid)
    ).fetchone()
    if not comp:
        flash("Concorrente nao encontrado.", "error")
        return redirect(url_for("dashboard"))

    html = scrape_pricing_page(comp["pricing_url"])
    if html is None:
        flash("Erro ao acessar a pagina. Verifique a URL.", "error")
        return redirect(url_for("snapshot_history", comp_id=comp_id))

    text = extract_text(html)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    prev = db.execute("""
        SELECT * FROM snapshots WHERE competitor_id = ?
        ORDER BY captured_at DESC LIMIT 1
    """, (comp_id,)).fetchone()

    snippet = html[:5000]
    cur = db.execute("""
        INSERT INTO snapshots (competitor_id, content_hash, text_content, html_snippet)
        VALUES (?, ?, ?, ?)
    """, (comp_id, content_hash, text, snippet))
    snap_id = cur.lastrowid
    db.commit()

    if prev and prev["content_hash"] != content_hash:
        diff_summary = compute_diff(prev["text_content"], text)
        db.execute("""
            INSERT INTO alerts (snapshot_id, competitor_id, diff_summary)
            VALUES (?, ?, ?)
        """, (snap_id, comp_id, diff_summary))
        db.commit()
        flash("Mudanca detectada! Alerta criado.", "success")
    elif prev:
        flash("Snapshot capturado. Nenhuma mudanca detectada.", "info")
    else:
        flash("Primeiro snapshot capturado com sucesso!", "success")

    return redirect(url_for("snapshot_history", comp_id=comp_id))


@app.route("/alerts")
@login_required
def alerts_list():
    db = get_db()
    uid = session["user_id"]
    filter_type = request.args.get("filter", "all")

    query = """
        SELECT a.*, c.name as competitor_name
        FROM alerts a
        JOIN competitors c ON a.competitor_id = c.id
        WHERE c.user_id = ?
    """
    if filter_type == "unread":
        query += " AND a.seen = 0"
    elif filter_type == "read":
        query += " AND a.seen = 1"
    query += " ORDER BY a.sent_at DESC"

    alerts = db.execute(query, (uid,)).fetchall()
    return render_template("alerts.html", alerts=alerts, current_filter=filter_type)


@app.route("/alerts/<int:alert_id>/read", methods=["POST"])
@login_required
def mark_alert_read(alert_id):
    db = get_db()
    uid = session["user_id"]
    db.execute("""
        UPDATE alerts SET seen = 1
        WHERE id = ? AND competitor_id IN (
            SELECT id FROM competitors WHERE user_id = ?
        )
    """, (alert_id, uid))
    db.commit()
    if request.headers.get("HX-Request"):
        return '<span class="text-xs text-gray-500">Lido</span>'
    return "", 200


# --------------- Startup ---------------

init_db()

scheduler = BackgroundScheduler()
scheduler.add_job(run_scraper, "interval", hours=24, id="price_check", next_run_time=None)
scheduler.start()

if __name__ == "__main__":
    try:
        app.run(debug=True, use_reloader=False, port=5000)
    finally:
        scheduler.shutdown()
