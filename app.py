import os
import hashlib
import sqlite3
import smtplib
import difflib
from email.mime.text import MIMEText
from datetime import datetime
from functools import wraps

import requests
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

DATABASE = os.getenv("DATABASE_URL", "priceradar.db")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_HOURS", "6"))


# --------------- DB helpers ---------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS competitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            pricing_url TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id),
            content_text TEXT,
            content_hash TEXT NOT NULL,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_old_id INTEGER NOT NULL REFERENCES snapshots(id),
            snapshot_new_id INTEGER NOT NULL REFERENCES snapshots(id),
            diff_summary TEXT,
            notified_at TIMESTAMP
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
    headers = {"User-Agent": "PriceRadar/1.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] Could not fetch {url}: {e}"


def compute_diff(old_text, new_text):
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=3))
    return "\n".join(diff)


def take_snapshot(competitor_id, db=None):
    own_conn = db is None
    if own_conn:
        db = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row

    comp = db.execute("SELECT * FROM competitors WHERE id = ?", (competitor_id,)).fetchone()
    if not comp:
        if own_conn:
            db.close()
        return None

    content = scrape_pricing_page(comp["pricing_url"])
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    last = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 1",
        (competitor_id,)
    ).fetchone()

    cur = db.execute(
        "INSERT INTO snapshots (competitor_id, content_text, content_hash) VALUES (?, ?, ?)",
        (competitor_id, content, content_hash)
    )
    new_snap_id = cur.lastrowid
    db.commit()

    change_id = None
    if last and last["content_hash"] != content_hash:
        diff_summary = compute_diff(last["content_text"], content)
        cur2 = db.execute(
            "INSERT INTO changes (snapshot_old_id, snapshot_new_id, diff_summary) VALUES (?, ?, ?)",
            (last["id"], new_snap_id, diff_summary)
        )
        change_id = cur2.lastrowid
        db.commit()

        send_alert_email(comp, diff_summary, db)

    if own_conn:
        db.close()
    return change_id


# --------------- Email ---------------

def send_alert_email(competitor, diff_summary, db=None):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    if not all([smtp_host, smtp_user, smtp_pass]):
        print(f"[EMAIL SKIP] SMTP not configured. Change detected for {competitor['name']}")
        return

    own_conn = db is None
    if own_conn:
        db = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row

    user = db.execute("SELECT * FROM users WHERE id = ?", (competitor["user_id"],)).fetchone()
    if not user:
        if own_conn:
            db.close()
        return

    subject = f"[PriceRadar] Mudanca detectada: {competitor['name']}"
    body = (
        f"Mudanca de preco detectada em {competitor['name']}.\n"
        f"URL: {competitor['pricing_url']}\n\n"
        f"--- Diff ---\n{diff_summary[:3000]}"
    )
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = user["email"]

    try:
        with smtplib.SMTP(smtp_host, 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"[EMAIL] Sent alert to {user['email']} about {competitor['name']}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")

    if own_conn:
        db.close()


# --------------- Scheduled job ---------------

def scheduled_check():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    competitors = db.execute("SELECT * FROM competitors").fetchall()
    db.close()
    for comp in competitors:
        take_snapshot(comp["id"])
    print(f"[SCHEDULER] Checked {len(competitors)} competitors at {datetime.utcnow()}")


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

    competitors = db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM snapshots s WHERE s.competitor_id = c.id) as snap_count,
            (SELECT s.captured_at FROM snapshots s
             WHERE s.competitor_id = c.id ORDER BY s.captured_at DESC LIMIT 1) as last_check,
            (SELECT COUNT(*) FROM changes ch
             JOIN snapshots sn ON ch.snapshot_new_id = sn.id
             WHERE sn.competitor_id = c.id) as change_count
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()

    recent_changes = db.execute("""
        SELECT ch.*, comp.name as competitor_name, comp.id as competitor_id,
               s_new.captured_at as detected_at
        FROM changes ch
        JOIN snapshots s_new ON ch.snapshot_new_id = s_new.id
        JOIN competitors comp ON s_new.competitor_id = comp.id
        WHERE comp.user_id = ?
        ORDER BY s_new.captured_at DESC LIMIT 20
    """, (uid,)).fetchall()

    return render_template("dashboard.html",
                           competitors=competitors,
                           recent_changes=recent_changes)


@app.route("/competitors", methods=["POST"])
@login_required
def add_competitor():
    name = request.form.get("name", "").strip()
    pricing_url = request.form.get("pricing_url", "").strip()
    notes = request.form.get("notes", "").strip()

    if not name or not pricing_url:
        flash("Nome e URL sao obrigatorios.", "error")
        return redirect(url_for("dashboard"))

    if not pricing_url.startswith(("http://", "https://")):
        flash("URL deve comecar com http:// ou https://", "error")
        return redirect(url_for("dashboard"))

    db = get_db()
    db.execute(
        "INSERT INTO competitors (user_id, name, pricing_url, notes) VALUES (?, ?, ?, ?)",
        (session["user_id"], name, pricing_url, notes)
    )
    db.commit()
    flash(f"Concorrente '{name}' adicionado!", "success")
    return redirect(url_for("dashboard"))


@app.route("/competitors/<int:id>", methods=["GET", "DELETE"])
@login_required
def competitor_detail(id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        flash("Concorrente nao encontrado.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "DELETE":
        db.execute("DELETE FROM changes WHERE snapshot_old_id IN (SELECT id FROM snapshots WHERE competitor_id = ?)", (id,))
        db.execute("DELETE FROM changes WHERE snapshot_new_id IN (SELECT id FROM snapshots WHERE competitor_id = ?)", (id,))
        db.execute("DELETE FROM snapshots WHERE competitor_id = ?", (id,))
        db.execute("DELETE FROM competitors WHERE id = ?", (id,))
        db.commit()
        if request.headers.get("HX-Request"):
            return ""
        return "", 200

    snapshots = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC",
        (id,)
    ).fetchall()

    changes = db.execute("""
        SELECT ch.*, s_old.captured_at as old_date, s_new.captured_at as new_date
        FROM changes ch
        JOIN snapshots s_old ON ch.snapshot_old_id = s_old.id
        JOIN snapshots s_new ON ch.snapshot_new_id = s_new.id
        WHERE s_new.competitor_id = ?
        ORDER BY s_new.captured_at DESC
    """, (id,)).fetchall()

    return render_template("competitor_detail.html",
                           competitor=comp, snapshots=snapshots, changes=changes)


@app.route("/competitors/<int:id>/check", methods=["POST"])
@login_required
def manual_check(id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        flash("Concorrente nao encontrado.", "error")
        return redirect(url_for("dashboard"))

    change_id = take_snapshot(id, db)
    if change_id:
        flash("Snapshot capturado — mudanca detectada!", "success")
    else:
        flash("Snapshot capturado — nenhuma mudanca.", "info")
    return redirect(url_for("competitor_detail", id=id))


@app.route("/changes/<int:id>")
@login_required
def view_diff(id):
    db = get_db()
    uid = session["user_id"]

    change = db.execute("SELECT * FROM changes WHERE id = ?", (id,)).fetchone()
    if not change:
        flash("Mudanca nao encontrada.", "error")
        return redirect(url_for("dashboard"))

    old_snap = db.execute("SELECT * FROM snapshots WHERE id = ?", (change["snapshot_old_id"],)).fetchone()
    new_snap = db.execute("SELECT * FROM snapshots WHERE id = ?", (change["snapshot_new_id"],)).fetchone()

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?",
        (old_snap["competitor_id"], uid)
    ).fetchone()
    if not comp:
        flash("Acesso negado.", "error")
        return redirect(url_for("dashboard"))

    old_lines = (old_snap["content_text"] or "").splitlines()
    new_lines = (new_snap["content_text"] or "").splitlines()
    diff_table = difflib.HtmlDiff(wrapcolumn=80).make_table(
        old_lines, new_lines,
        fromdesc=f"Antes ({old_snap['captured_at']})",
        todesc=f"Depois ({new_snap['captured_at']})"
    )

    return render_template("view_diff.html",
                           change=change, competitor=comp,
                           old_snap=old_snap, new_snap=new_snap,
                           diff_table=diff_table)


# --------------- Startup ---------------

init_db()

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_check, "interval", hours=CHECK_INTERVAL,
                  id="price_check", next_run_time=None)
scheduler.start()

if __name__ == "__main__":
    try:
        app.run(debug=True, use_reloader=False, port=5000)
    finally:
        scheduler.shutdown()
