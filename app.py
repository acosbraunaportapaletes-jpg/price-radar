import os
import hashlib
import sqlite3
import smtplib
import difflib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from urllib.parse import urlparse

import requests
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

DATABASE = os.getenv("DATABASE_URL", "priceradar.db")


# ── DB helpers ──────────────────────────────────────────────────────────────

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
            check_interval_hours INTEGER DEFAULT 24,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            content_text TEXT,
            content_hash TEXT,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            diff_summary TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMP
        );
    """)
    db.close()


# ── Auth helpers ────────────────────────────────────────────────────────────

def current_user():
    if "user_id" not in session:
        return None
    return get_db().execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Faca login para continuar.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Scraper helpers ─────────────────────────────────────────────────────────

def scrape_pricing_page(url):
    headers = {"User-Agent": "PriceRadar/1.0 (pricing monitor)"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def compute_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_diff(old_text, new_text):
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines, lineterm="",
        fromfile="antes", tofile="depois", n=3
    ))
    return "\n".join(diff)


def compute_diff_html(old_text, new_text):
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    differ = difflib.HtmlDiff(wrapcolumn=80)
    return differ.make_table(
        old_lines, new_lines,
        fromdesc="Anterior", todesc="Atual",
        context=True, numlines=3
    )


# ── Email helper ────────────────────────────────────────────────────────────

def send_alert_email(to_email, competitor_name, diff_summary):
    smtp_host = os.getenv("SMTP_HOST")
    if not smtp_host:
        print(f"[ALERT] SMTP nao configurado. Mudanca detectada: {competitor_name}")
        return False

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    from_email = os.getenv("FROM_EMAIL", smtp_user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"PriceRadar: {competitor_name} mudou o pricing!"
    msg["From"] = from_email
    msg["To"] = to_email

    body_html = (
        f"<h2>Mudanca de preco detectada: {competitor_name}</h2>"
        f"<pre style='background:#111;color:#eee;padding:16px;border-radius:8px;'>"
        f"{diff_summary[:3000]}</pre>"
        f"<p>Acesse seu dashboard para ver o diff completo.</p>"
    )
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False


# ── Snapshot logic ──────────────────────────────────────────────────────────

def take_snapshot(comp_id, db):
    comp = db.execute(
        "SELECT c.*, u.email as user_email FROM competitors c "
        "JOIN users u ON c.user_id = u.id WHERE c.id = ?",
        (comp_id,)
    ).fetchone()
    if not comp:
        return None

    content = scrape_pricing_page(comp["pricing_url"])
    content_hash = compute_hash(content)

    last_snap = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 1",
        (comp_id,)
    ).fetchone()

    cur = db.execute(
        "INSERT INTO snapshots (competitor_id, content_text, content_hash) VALUES (?, ?, ?)",
        (comp_id, content, content_hash)
    )
    snap_id = cur.lastrowid
    changed = False

    if last_snap and last_snap["content_hash"] != content_hash:
        diff_summary = compute_diff(last_snap["content_text"], content)
        if not diff_summary:
            diff_summary = "Conteudo alterado (hash diferente)."

        db.execute(
            "INSERT INTO alerts (snapshot_id, user_id, diff_summary) VALUES (?, ?, ?)",
            (snap_id, comp["user_id"], diff_summary)
        )
        send_alert_email(comp["user_email"], comp["name"], diff_summary)
        changed = True

    db.commit()
    return {"changed": changed, "snapshot_id": snap_id}


# ── Scheduled check (all competitors) ──────────────────────────────────────

def check_all_prices():
    with app.app_context():
        db = get_db()
        competitors = db.execute(
            "SELECT c.id FROM competitors c"
        ).fetchall()
        for comp in competitors:
            try:
                take_snapshot(comp["id"], db)
            except Exception as e:
                print(f"[SCHEDULER ERROR] competitor {comp['id']}: {e}")
        print(f"[SCHEDULER] Checked {len(competitors)} competitors.")


# ── Routes: Landing & Auth ──────────────────────────────────────────────────

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


# ── Routes: Dashboard ──────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]

    alerts = db.execute("""
        SELECT a.*, c.name as competitor_name, c.id as competitor_id
        FROM alerts a
        JOIN snapshots s ON a.snapshot_id = s.id
        JOIN competitors c ON s.competitor_id = c.id
        WHERE a.user_id = ?
        ORDER BY a.sent_at DESC LIMIT 30
    """, (uid,)).fetchall()

    competitor_count = db.execute(
        "SELECT COUNT(*) as cnt FROM competitors WHERE user_id = ?", (uid,)
    ).fetchone()["cnt"]

    unread_count = db.execute(
        "SELECT COUNT(*) as cnt FROM alerts WHERE user_id = ? AND read_at IS NULL", (uid,)
    ).fetchone()["cnt"]

    total_alerts = db.execute(
        "SELECT COUNT(*) as cnt FROM alerts WHERE user_id = ?", (uid,)
    ).fetchone()["cnt"]

    return render_template("dashboard.html",
                           alerts=alerts,
                           competitor_count=competitor_count,
                           unread_count=unread_count,
                           total_alerts=total_alerts)


# ── Routes: Competitors ────────────────────────────────────────────────────

@app.route("/competitors", methods=["GET"])
@login_required
def list_competitors():
    db = get_db()
    uid = session["user_id"]

    comps = db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM snapshots WHERE competitor_id = c.id) as snap_count,
            (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_check,
            (SELECT COUNT(*) FROM alerts a
             JOIN snapshots sn ON a.snapshot_id = sn.id
             WHERE sn.competitor_id = c.id) as alert_count
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()

    return render_template("competitors.html", competitors=comps)


@app.route("/competitors", methods=["POST"])
@login_required
def add_competitor():
    name = request.form.get("name", "").strip()
    pricing_url = request.form.get("pricing_url", "").strip()

    if not name or not pricing_url:
        flash("Nome e URL sao obrigatorios.", "error")
        return redirect(url_for("list_competitors"))

    parsed = urlparse(pricing_url)
    if not parsed.scheme or not parsed.netloc:
        flash("URL invalida. Inclua http:// ou https://", "error")
        return redirect(url_for("list_competitors"))

    db = get_db()
    db.execute(
        "INSERT INTO competitors (user_id, name, pricing_url) VALUES (?, ?, ?)",
        (session["user_id"], name, pricing_url)
    )
    db.commit()
    flash(f"Concorrente '{name}' adicionado!", "success")
    return redirect(url_for("list_competitors"))


@app.route("/competitors/<int:id>", methods=["DELETE", "POST"])
@login_required
def remove_competitor(id):
    db = get_db()
    uid = session["user_id"]

    # Support _method override for HTML forms
    method = request.form.get("_method", request.method).upper()
    if method != "DELETE":
        abort(405)

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        abort(404)

    db.execute("DELETE FROM competitors WHERE id = ?", (id,))
    db.commit()

    if request.headers.get("HX-Request"):
        return ""

    flash("Concorrente removido.", "success")
    return redirect(url_for("list_competitors"))


@app.route("/competitors/<int:id>/history")
@login_required
def snapshot_history(id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        abort(404)

    snaps = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 50",
        (id,)
    ).fetchall()

    diffs = []
    snap_list = list(snaps)
    for i in range(len(snap_list)):
        snap = snap_list[i]
        if i < len(snap_list) - 1:
            older = snap_list[i + 1]
            if snap["content_hash"] != older["content_hash"]:
                diff_text = compute_diff(older["content_text"], snap["content_text"])
                diff_html = compute_diff_html(older["content_text"], snap["content_text"])
                diffs.append({"snapshot": snap, "diff_text": diff_text,
                              "diff_html": diff_html, "changed": True})
            else:
                diffs.append({"snapshot": snap, "diff_text": None,
                              "diff_html": None, "changed": False})
        else:
            diffs.append({"snapshot": snap, "diff_text": None,
                          "diff_html": None, "changed": False})

    return render_template("history.html", competitor=comp, diffs=diffs)


@app.route("/competitors/<int:id>/check", methods=["POST"])
@login_required
def manual_check(id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        abort(404)

    try:
        result = take_snapshot(id, db)
        if result and result["changed"]:
            flash(f"Mudanca detectada em {comp['name']}!", "warning")
        else:
            flash(f"Nenhuma mudanca em {comp['name']}.", "info")
    except Exception as e:
        flash(f"Erro ao verificar {comp['name']}: {str(e)}", "error")

    return redirect(url_for("snapshot_history", id=id))


# ── Routes: Alerts ──────────────────────────────────────────────────────────

@app.route("/alerts")
@login_required
def list_alerts():
    db = get_db()
    uid = session["user_id"]

    alerts = db.execute("""
        SELECT a.*, c.name as competitor_name, c.id as competitor_id
        FROM alerts a
        JOIN snapshots s ON a.snapshot_id = s.id
        JOIN competitors c ON s.competitor_id = c.id
        WHERE a.user_id = ?
        ORDER BY a.sent_at DESC LIMIT 100
    """, (uid,)).fetchall()

    return render_template("alerts.html", alerts=alerts)


@app.route("/alerts/<int:alert_id>/read", methods=["POST"])
@login_required
def mark_alert_read(alert_id):
    db = get_db()
    uid = session["user_id"]
    db.execute(
        "UPDATE alerts SET read_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (alert_id, uid)
    )
    db.commit()
    if request.headers.get("HX-Request"):
        return '<span class="inline-block px-2 py-1 text-xs rounded bg-green-900 text-green-300">Lido</span>'
    flash("Alerta marcado como lido.", "success")
    return redirect(url_for("list_alerts"))


# ── Init & Run ──────────────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
