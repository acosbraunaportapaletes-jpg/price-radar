import os
import re
import sqlite3
import hashlib
import smtplib
import threading
import time
import difflib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from urllib.request import urlopen, Request
from urllib.error import URLError

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

DATABASE = os.environ.get("DATABASE_URL", "priceradar.db")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_HOURS", "6"))


# ── DB helpers ────────────────────────────────────────────────────────────────

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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS competitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            pricing_url TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            html_hash TEXT NOT NULL,
            content_text TEXT NOT NULL,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            diff_html TEXT NOT NULL,
            notified_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.close()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Faca login para continuar.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if "user_id" not in session:
        return None
    return get_db().execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()


@app.context_processor
def inject_user():
    return {"user": current_user()}


# ── Scraper & Diff ────────────────────────────────────────────────────────────

def fetch_page_text(url):
    req = Request(url, headers={"User-Agent": "PriceRadar/1.0 (price monitoring)"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_tags(html):
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def compute_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_diff_html(old_text, new_text):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = difflib.unified_diff(old_lines, new_lines, lineterm="", n=3)
    parts = []
    for line in diff:
        esc = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        if line.startswith("+") and not line.startswith("+++"):
            parts.append(f'<span style="color:#4ade80;background:#052e16">{esc}</span>')
        elif line.startswith("-") and not line.startswith("---"):
            parts.append(f'<span style="color:#f87171;background:#450a0a;text-decoration:line-through">{esc}</span>')
        elif line.startswith("@@"):
            parts.append(f'<span style="color:#94a3b8">{esc}</span>')
        else:
            parts.append(f'<span style="color:#cbd5e1">{esc}</span>')
    return "<br>".join(parts) if parts else "<em>Sem diferenca textual visivel.</em>"


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(to_addr, subject, html_body):
    if not SMTP_HOST:
        print(f"[EMAIL SKIP] SMTP nao configurado. Destino: {to_addr} | Assunto: {subject}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, 587) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_addr, msg.as_string())
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


# ── Background Monitor ────────────────────────────────────────────────────────

def run_monitor():
    time.sleep(10)
    while True:
        try:
            _check_all_competitors()
        except Exception as e:
            print(f"[MONITOR ERROR] {e}")
        time.sleep(CHECK_INTERVAL * 3600)


def _check_all_competitors():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    competitors = db.execute(
        "SELECT c.*, u.email FROM competitors c JOIN users u ON c.user_id = u.id"
    ).fetchall()

    for comp in competitors:
        try:
            html = fetch_page_text(comp["pricing_url"])
            content = strip_tags(html)
            h = compute_hash(content)

            last = db.execute(
                "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 1",
                (comp["id"],)
            ).fetchone()

            snap_id = db.execute(
                "INSERT INTO snapshots (competitor_id, html_hash, content_text) VALUES (?, ?, ?)",
                (comp["id"], h, content)
            ).lastrowid

            if last and last["html_hash"] != h:
                diff_html = make_diff_html(last["content_text"], content)
                db.execute(
                    "INSERT INTO changes (snapshot_id, diff_html, notified_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (snap_id, diff_html)
                )
                body = (
                    f"<h2>Mudanca de preco detectada: {comp['name']}</h2>"
                    f"<p>URL: {comp['pricing_url']}</p>"
                    f"<div style='background:#0a0a0f;padding:16px;border-radius:8px;font-family:monospace;font-size:13px'>{diff_html}</div>"
                    f"<p style='margin-top:16px'>Acesse o dashboard para ver o historico completo.</p>"
                )
                send_email(comp["email"], f"[PriceRadar] Mudanca detectada: {comp['name']}", body)

            db.commit()
        except Exception as e:
            print(f"[SCRAPE ERROR] {comp['name']}: {e}")

    db.close()


# ── Routes: Auth ──────────────────────────────────────────────────────────────

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
    cur = db.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, pw_hash)
    )
    db.commit()

    session["user_id"] = cur.lastrowid
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


# ── Routes: Dashboard ─────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]

    competitors = db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM snapshots WHERE competitor_id = c.id) as snap_count,
            (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_check
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()

    recent_changes = db.execute("""
        SELECT ch.*, s.competitor_id, comp.name as competitor_name
        FROM changes ch
        JOIN snapshots s ON ch.snapshot_id = s.id
        JOIN competitors comp ON s.competitor_id = comp.id
        WHERE comp.user_id = ?
        ORDER BY ch.created_at DESC LIMIT 20
    """, (uid,)).fetchall()

    return render_template("dashboard.html", competitors=competitors, recent_changes=recent_changes)


# ── Routes: Competitors ───────────────────────────────────────────────────────

@app.route("/competitors", methods=["POST"])
@login_required
def add_competitor():
    db = get_db()
    uid = session["user_id"]

    name = request.form.get("name", "").strip()
    pricing_url = request.form.get("pricing_url", "").strip()
    notes = request.form.get("notes", "").strip()

    if not name or not pricing_url:
        flash("Nome e URL sao obrigatorios.", "error")
        return redirect(url_for("dashboard"))
    if not pricing_url.startswith(("http://", "https://")):
        pricing_url = "https://" + pricing_url

    db.execute(
        "INSERT INTO competitors (user_id, name, pricing_url, notes) VALUES (?, ?, ?, ?)",
        (uid, name, pricing_url, notes),
    )
    db.commit()
    flash(f"Concorrente '{name}' adicionado!", "success")

    if request.headers.get("HX-Request"):
        competitors = db.execute("""
            SELECT c.*,
                (SELECT COUNT(*) FROM snapshots WHERE competitor_id = c.id) as snap_count,
                (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
                 ORDER BY captured_at DESC LIMIT 1) as last_check
            FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
        """, (uid,)).fetchall()
        return render_template("_competitors_list.html", competitors=competitors)

    return redirect(url_for("dashboard"))


@app.route("/competitors/<int:id>")
@login_required
def competitor_detail(id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        abort(404)

    snapshots = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 50",
        (id,),
    ).fetchall()

    changes = db.execute("""
        SELECT ch.*, s.captured_at as snapshot_time
        FROM changes ch
        JOIN snapshots s ON ch.snapshot_id = s.id
        WHERE s.competitor_id = ?
        ORDER BY ch.created_at DESC
    """, (id,)).fetchall()

    return render_template("competitor_detail.html", competitor=comp, snapshots=snapshots, changes=changes)


@app.route("/competitors/<int:id>", methods=["DELETE"])
@login_required
def delete_competitor(id):
    db = get_db()
    uid = session["user_id"]

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
    return redirect(url_for("dashboard"))


@app.route("/competitors/<int:id>/check", methods=["POST"])
@login_required
def check_now(id):
    db = get_db()
    uid = session["user_id"]

    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        abort(404)

    try:
        html = fetch_page_text(comp["pricing_url"])
        content = strip_tags(html)
        h = compute_hash(content)

        last = db.execute(
            "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 1",
            (id,)
        ).fetchone()

        snap_id = db.execute(
            "INSERT INTO snapshots (competitor_id, html_hash, content_text) VALUES (?, ?, ?)",
            (id, h, content)
        ).lastrowid

        if last and last["html_hash"] != h:
            diff_html = make_diff_html(last["content_text"], content)
            db.execute(
                "INSERT INTO changes (snapshot_id, diff_html) VALUES (?, ?)",
                (snap_id, diff_html)
            )
            db.commit()
            return (
                '<div class="mt-2 p-3 rounded-lg bg-yellow-900/30 border border-yellow-800 text-sm">'
                '<span class="text-yellow-400 font-semibold">Mudanca detectada!</span>'
                '<p class="text-yellow-200/80 mt-1 text-xs">Diff salvo. Veja na timeline.</p>'
                '</div>'
            )
        db.commit()
        if not last:
            return '<span class="text-green-400 text-sm">Primeiro snapshot capturado.</span>'
        return '<span class="text-green-400 text-sm">Verificado — sem mudancas.</span>'
    except Exception as e:
        return f'<span class="text-red-400 text-sm">Erro: {e}</span>'


# ── Routes: Changes ───────────────────────────────────────────────────────────

@app.route("/changes/<int:id>")
@login_required
def change_diff(id):
    db = get_db()
    uid = session["user_id"]

    change = db.execute("""
        SELECT ch.*, s.competitor_id, s.content_text, s.captured_at,
               comp.name as competitor_name, comp.pricing_url
        FROM changes ch
        JOIN snapshots s ON ch.snapshot_id = s.id
        JOIN competitors comp ON s.competitor_id = comp.id
        WHERE ch.id = ? AND comp.user_id = ?
    """, (id, uid)).fetchone()
    if not change:
        abort(404)

    return render_template("change_diff.html", change=change)


# ── Init & Run ────────────────────────────────────────────────────────────────

init_db()

if os.environ.get("ENABLE_MONITOR", "1") == "1":
    monitor_thread = threading.Thread(target=run_monitor, daemon=True)
    monitor_thread.start()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
