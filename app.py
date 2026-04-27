import os
import hashlib
import sqlite3
import smtplib
import difflib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
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
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@priceradar.app")
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
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            content_hash TEXT NOT NULL,
            content_text TEXT NOT NULL,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            diff_summary TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        resp = requests.get(url, headers=headers, timeout=20)
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
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="",
                                     fromfile="antes", tofile="depois", n=3))
    return "\n".join(diff)


# --------------- Email ---------------

def send_alert_email(user_email, competitor_name, pricing_url, diff_summary):
    if not SMTP_HOST:
        print(f"[EMAIL SKIP] SMTP not configured. Change detected for {competitor_name}")
        return
    subject = f"[PriceRadar] Mudanca detectada: {competitor_name}"
    body = (
        f"<h2>Mudanca de preco detectada: {competitor_name}</h2>"
        f"<p><strong>URL:</strong> {pricing_url}</p>"
        f"<pre style='background:#111;color:#eee;padding:16px;border-radius:8px;'>"
        f"{diff_summary[:3000]}</pre>"
        f"<p><a href='/dashboard'>Ver no dashboard</a></p>"
    )
    msg = MIMEText(body, "html")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = user_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print(f"[EMAIL] Sent alert to {user_email} about {competitor_name}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


# --------------- Scheduled job ---------------

def check_prices():
    with app.app_context():
        db = get_db()
        competitors = db.execute("""
            SELECT c.*, u.email as user_email
            FROM competitors c JOIN users u ON c.user_id = u.id
        """).fetchall()

        for comp in competitors:
            content = scrape_pricing_page(comp["pricing_url"])
            if content.startswith("[ERROR]"):
                continue

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

            last_snap = db.execute(
                "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 1",
                (comp["id"],)
            ).fetchone()

            if last_snap and last_snap["content_hash"] == content_hash:
                continue

            cur = db.execute(
                "INSERT INTO snapshots (competitor_id, content_hash, content_text) VALUES (?, ?, ?)",
                (comp["id"], content_hash, content)
            )
            snap_id = cur.lastrowid

            if last_snap:
                diff_summary = compute_diff(last_snap["content_text"], content)
                if not diff_summary:
                    diff_summary = "Content changed (hash differs)."

                db.execute(
                    "INSERT INTO alerts (snapshot_id, user_id, diff_summary) VALUES (?, ?, ?)",
                    (snap_id, comp["user_id"], diff_summary)
                )
                send_alert_email(comp["user_email"], comp["name"],
                                 comp["pricing_url"], diff_summary)

            db.commit()

        print(f"[SCHEDULER] Checked {len(competitors)} competitors at {datetime.utcnow()}")


def take_snapshot_manual(comp_id, db):
    comp = db.execute("SELECT c.*, u.email as user_email FROM competitors c JOIN users u ON c.user_id = u.id WHERE c.id = ?",
                      (comp_id,)).fetchone()
    if not comp:
        return False

    content = scrape_pricing_page(comp["pricing_url"])
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    last_snap = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 1",
        (comp_id,)
    ).fetchone()

    cur = db.execute(
        "INSERT INTO snapshots (competitor_id, content_hash, content_text) VALUES (?, ?, ?)",
        (comp_id, content_hash, content)
    )
    snap_id = cur.lastrowid
    changed = False

    if last_snap and last_snap["content_hash"] != content_hash:
        diff_summary = compute_diff(last_snap["content_text"], content)
        if not diff_summary:
            diff_summary = "Content changed (hash differs)."
        db.execute(
            "INSERT INTO alerts (snapshot_id, user_id, diff_summary) VALUES (?, ?, ?)",
            (snap_id, comp["user_id"], diff_summary)
        )
        send_alert_email(comp["user_email"], comp["name"],
                         comp["pricing_url"], diff_summary)
        changed = True

    db.commit()
    return changed


# --------------- Routes: Landing & Auth ---------------

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


# --------------- Routes: Dashboard ---------------

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]

    filter_status = request.args.get("status", "all")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    query = """
        SELECT a.*, c.name as competitor_name, c.id as competitor_id, c.pricing_url
        FROM alerts a
        JOIN snapshots s ON a.snapshot_id = s.id
        JOIN competitors c ON s.competitor_id = c.id
        WHERE a.user_id = ?
    """
    params = [uid]
    if filter_status == "unread":
        query += " AND a.is_read = 0"
    elif filter_status == "read":
        query += " AND a.is_read = 1"
    if date_from:
        query += " AND date(a.created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        query += " AND date(a.created_at) <= date(?)"
        params.append(date_to)
    query += " ORDER BY a.created_at DESC LIMIT 50"

    alerts = db.execute(query, params).fetchall()

    total_competitors = db.execute(
        "SELECT COUNT(*) as cnt FROM competitors WHERE user_id = ?", (uid,)
    ).fetchone()["cnt"]
    unread_count = db.execute(
        "SELECT COUNT(*) as cnt FROM alerts WHERE user_id = ? AND is_read = 0", (uid,)
    ).fetchone()["cnt"]
    total_alerts = db.execute(
        "SELECT COUNT(*) as cnt FROM alerts WHERE user_id = ?", (uid,)
    ).fetchone()["cnt"]

    return render_template("dashboard.html",
                           alerts=alerts,
                           total_competitors=total_competitors,
                           unread_count=unread_count,
                           total_alerts=total_alerts,
                           filter_status=filter_status,
                           date_from=date_from,
                           date_to=date_to)


# --------------- Routes: Competitors CRUD ---------------

@app.route("/competitors", methods=["GET", "POST"])
@login_required
def competitors():
    db = get_db()
    uid = session["user_id"]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pricing_url = request.form.get("pricing_url", "").strip()
        notes = request.form.get("notes", "").strip()
        if not name or not pricing_url:
            flash("Nome e URL sao obrigatorios.", "error")
            return redirect(url_for("competitors"))
        if not pricing_url.startswith(("http://", "https://")):
            flash("URL deve comecar com http:// ou https://", "error")
            return redirect(url_for("competitors"))
        db.execute(
            "INSERT INTO competitors (user_id, name, pricing_url, notes) VALUES (?, ?, ?, ?)",
            (uid, name, pricing_url, notes)
        )
        db.commit()
        flash(f"Concorrente '{name}' adicionado!", "success")
        return redirect(url_for("competitors"))

    comps = db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM snapshots s WHERE s.competitor_id = c.id) as snap_count,
            (SELECT s.captured_at FROM snapshots s
             WHERE s.competitor_id = c.id ORDER BY s.captured_at DESC LIMIT 1) as last_check,
            (SELECT COUNT(*) FROM alerts a
             JOIN snapshots sn ON a.snapshot_id = sn.id
             WHERE sn.competitor_id = c.id) as alert_count
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()

    return render_template("competitors.html", competitors=comps)


@app.route("/competitors/<int:comp_id>", methods=["GET", "POST"])
@login_required
def competitor_detail(comp_id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (comp_id, uid)
    ).fetchone()
    if not comp:
        flash("Concorrente nao encontrado.", "error")
        return redirect(url_for("competitors"))

    method = request.form.get("_method", request.method).upper()

    if method == "DELETE":
        db.execute("DELETE FROM alerts WHERE user_id = ? AND snapshot_id IN (SELECT id FROM snapshots WHERE competitor_id = ?)", (uid, comp_id))
        db.execute("DELETE FROM snapshots WHERE competitor_id = ?", (comp_id,))
        db.execute("DELETE FROM competitors WHERE id = ? AND user_id = ?", (comp_id, uid))
        db.commit()
        flash("Concorrente removido.", "success")
        if request.headers.get("HX-Request"):
            return ""
        return redirect(url_for("competitors"))

    if method == "PUT":
        name = request.form.get("name", "").strip()
        pricing_url = request.form.get("pricing_url", "").strip()
        notes = request.form.get("notes", "").strip()
        if not name or not pricing_url:
            flash("Nome e URL sao obrigatorios.", "error")
            return redirect(url_for("competitor_detail", comp_id=comp_id))
        db.execute(
            "UPDATE competitors SET name = ?, pricing_url = ?, notes = ? WHERE id = ? AND user_id = ?",
            (name, pricing_url, notes, comp_id, uid)
        )
        db.commit()
        flash("Concorrente atualizado!", "success")
        return redirect(url_for("competitor_detail", comp_id=comp_id))

    snap_count = db.execute(
        "SELECT COUNT(*) as cnt FROM snapshots WHERE competitor_id = ?", (comp_id,)
    ).fetchone()["cnt"]
    alert_count = db.execute(
        "SELECT COUNT(*) as cnt FROM alerts WHERE snapshot_id IN (SELECT id FROM snapshots WHERE competitor_id = ?)",
        (comp_id,)
    ).fetchone()["cnt"]

    return render_template("competitor_detail.html", competitor=comp,
                           snap_count=snap_count, alert_count=alert_count)


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
        return redirect(url_for("competitors"))

    changed = take_snapshot_manual(comp_id, db)
    if changed:
        flash("Snapshot capturado — mudanca detectada!", "success")
    else:
        flash("Snapshot capturado — nenhuma mudanca.", "info")
    return redirect(url_for("competitor_detail", comp_id=comp_id))


# --------------- Routes: Snapshots ---------------

@app.route("/competitors/<int:comp_id>/snapshots")
@login_required
def snapshots(comp_id):
    db = get_db()
    uid = session["user_id"]
    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (comp_id, uid)
    ).fetchone()
    if not comp:
        flash("Concorrente nao encontrado.", "error")
        return redirect(url_for("competitors"))

    snaps = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 50",
        (comp_id,)
    ).fetchall()

    diffs = []
    snap_list = list(snaps)
    for i in range(len(snap_list) - 1):
        newer = snap_list[i]
        older = snap_list[i + 1]
        if newer["content_hash"] != older["content_hash"]:
            diff = compute_diff(older["content_text"], newer["content_text"])
            diffs.append({"newer_id": newer["id"], "diff": diff})

    diff_map = {d["newer_id"]: d["diff"] for d in diffs}
    return render_template("snapshots.html", competitor=comp, snapshots=snaps, diff_map=diff_map)


# --------------- Routes: Alerts ---------------

@app.route("/alerts")
@login_required
def alerts():
    db = get_db()
    uid = session["user_id"]
    filter_status = request.args.get("status", "all")

    query = """
        SELECT a.*, c.name as competitor_name, c.id as competitor_id
        FROM alerts a
        JOIN snapshots s ON a.snapshot_id = s.id
        JOIN competitors c ON s.competitor_id = c.id
        WHERE a.user_id = ?
    """
    params = [uid]
    if filter_status == "unread":
        query += " AND a.is_read = 0"
    elif filter_status == "read":
        query += " AND a.is_read = 1"
    query += " ORDER BY a.created_at DESC LIMIT 100"

    alert_list = db.execute(query, params).fetchall()
    return render_template("alerts.html", alerts=alert_list, filter_status=filter_status)


@app.route("/alerts/<int:alert_id>/read", methods=["POST"])
@login_required
def mark_read(alert_id):
    db = get_db()
    uid = session["user_id"]
    db.execute("UPDATE alerts SET is_read = 1 WHERE id = ? AND user_id = ?", (alert_id, uid))
    db.commit()
    if request.headers.get("HX-Request"):
        return '<span class="inline-block px-2 py-1 text-xs rounded bg-green-900 text-green-300">Lido</span>'
    flash("Alerta marcado como lido.", "success")
    return redirect(url_for("alerts"))


# --------------- Scheduler ---------------

scheduler = BackgroundScheduler()
scheduler.add_job(check_prices, "interval", hours=CHECK_INTERVAL,
                  id="price_check", next_run_time=None)
scheduler.start()

# --------------- Startup ---------------

init_db()

if __name__ == "__main__":
    scheduler.reschedule_job("price_check", trigger="interval",
                            hours=CHECK_INTERVAL,
                            next_run_time=datetime.now() + timedelta(seconds=60))
    try:
        app.run(debug=True, use_reloader=False, port=5000)
    finally:
        scheduler.shutdown()
