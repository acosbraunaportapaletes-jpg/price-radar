import os
import hashlib
import sqlite3
import smtplib
import difflib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from urllib.parse import urlparse

import requests
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, abort, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

DATABASE = os.getenv("DATABASE_URL", "priceradar.db")
SCAN_TOKEN = os.getenv("SCAN_TOKEN", "changeme")


# -- DB helpers --------------------------------------------------------------

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
            content_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            snapshot_old_id INTEGER REFERENCES snapshots(id),
            snapshot_new_id INTEGER NOT NULL REFERENCES snapshots(id),
            diff_summary TEXT DEFAULT '',
            seen INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.close()


# -- Auth helpers ------------------------------------------------------------

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


# -- Scraper helpers ---------------------------------------------------------

def scrape_pricing_page(url):
    headers = {"User-Agent": "PriceRadar/1.0 (pricing monitor)"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
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


# -- Email helper ------------------------------------------------------------

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

    truncated = (diff_summary or "")[:3000]
    body_html = (
        f"<h2>Mudanca de preco detectada: {competitor_name}</h2>"
        f"<p>Uma alteracao foi detectada na pagina de pricing.</p>"
        f"<pre style='background:#111;color:#ccc;padding:12px;border-radius:8px;"
        f"font-size:12px;overflow:auto;'>{truncated}</pre>"
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


# -- Snapshot logic ----------------------------------------------------------

def take_snapshot(comp_id, db):
    comp = db.execute(
        "SELECT c.*, u.email as user_email FROM competitors c "
        "JOIN users u ON c.user_id = u.id WHERE c.id = ?",
        (comp_id,)
    ).fetchone()
    if not comp:
        return None

    content = scrape_pricing_page(comp["pricing_url"])
    chash = compute_hash(content)

    last_snap = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 1",
        (comp_id,)
    ).fetchone()

    cur = db.execute(
        "INSERT INTO snapshots (competitor_id, content_text, content_hash) VALUES (?, ?, ?)",
        (comp_id, content, chash)
    )
    snap_id = cur.lastrowid
    changed = False

    if last_snap and last_snap["content_hash"] != chash:
        diff_summary = compute_diff(last_snap["content_text"], content)
        db.execute(
            "INSERT INTO alerts (competitor_id, snapshot_old_id, snapshot_new_id, diff_summary) "
            "VALUES (?, ?, ?, ?)",
            (comp_id, last_snap["id"], snap_id, diff_summary)
        )
        db.commit()
        threading.Thread(
            target=send_alert_email,
            args=(comp["user_email"], comp["name"], diff_summary),
            daemon=True,
        ).start()
        changed = True
    else:
        db.commit()

    return {"changed": changed, "snapshot_id": snap_id}


# -- Full scan ---------------------------------------------------------------

def run_scan_all():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")

    competitors = db.execute("""
        SELECT c.id FROM competitors c
    """).fetchall()

    scanned, alerts_created = 0, 0

    for comp in competitors:
        try:
            c = db.execute(
                "SELECT c.*, u.email as user_email FROM competitors c "
                "JOIN users u ON c.user_id = u.id WHERE c.id = ?",
                (comp["id"],)
            ).fetchone()
            if not c:
                continue

            content = scrape_pricing_page(c["pricing_url"])
            chash = compute_hash(content)
            scanned += 1

            last_snap = db.execute(
                "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 1",
                (comp["id"],)
            ).fetchone()

            cur = db.execute(
                "INSERT INTO snapshots (competitor_id, content_text, content_hash) VALUES (?, ?, ?)",
                (comp["id"], content, chash)
            )
            snap_id = cur.lastrowid

            if last_snap and last_snap["content_hash"] != chash:
                diff_summary = compute_diff(last_snap["content_text"], content)
                db.execute(
                    "INSERT INTO alerts (competitor_id, snapshot_old_id, snapshot_new_id, diff_summary) "
                    "VALUES (?, ?, ?, ?)",
                    (comp["id"], last_snap["id"], snap_id, diff_summary)
                )
                alerts_created += 1
                threading.Thread(
                    target=send_alert_email,
                    args=(c["user_email"], c["name"], diff_summary),
                    daemon=True,
                ).start()

            db.commit()
        except Exception as e:
            print(f"[SCAN ERROR] competitor {comp['id']}: {e}")

    db.close()
    return {"scanned": scanned, "alerts_created": alerts_created}


# -- Routes: Landing & Auth --------------------------------------------------

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


# -- Routes: Dashboard -------------------------------------------------------

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

    alerts = db.execute("""
        SELECT a.*, c.name as competitor_name
        FROM alerts a
        JOIN competitors c ON a.competitor_id = c.id
        WHERE c.user_id = ?
        ORDER BY a.created_at DESC LIMIT 50
    """, (uid,)).fetchall()

    unseen = sum(1 for a in alerts if not a["seen"])

    return render_template("dashboard.html",
                           competitors=competitors,
                           alerts=alerts,
                           unseen=unseen)


# -- Routes: Competitors CRUD ------------------------------------------------

@app.route("/competitors", methods=["GET", "POST"])
@login_required
def competitors():
    db = get_db()
    uid = session["user_id"]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pricing_url = request.form.get("pricing_url", "").strip()

        if not name or not pricing_url:
            flash("Nome e URL sao obrigatorios.", "error")
            return redirect(url_for("competitors"))

        parsed = urlparse(pricing_url)
        if not parsed.scheme or not parsed.netloc:
            flash("URL invalida. Inclua http:// ou https://", "error")
            return redirect(url_for("competitors"))

        db.execute(
            "INSERT INTO competitors (user_id, name, pricing_url) VALUES (?, ?, ?)",
            (uid, name, pricing_url)
        )
        db.commit()
        flash(f"Concorrente '{name}' adicionado!", "success")
        return redirect(url_for("competitors"))

    comps = db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM snapshots WHERE competitor_id = c.id) as snap_count,
            (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_check
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()

    return render_template("competitors.html", competitors=comps)


@app.route("/competitors/<int:id>", methods=["GET"])
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
        "SELECT id, competitor_id, content_hash, captured_at FROM snapshots "
        "WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 30",
        (id,)
    ).fetchall()

    alerts = db.execute("""
        SELECT a.*
        FROM alerts a
        WHERE a.competitor_id = ?
        ORDER BY a.created_at DESC LIMIT 30
    """, (id,)).fetchall()

    return render_template("competitor_detail.html",
                           competitor=comp,
                           snapshots=snapshots,
                           alerts=alerts)


@app.route("/competitors/<int:id>/delete", methods=["POST"])
@login_required
def competitor_delete(id):
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
    return redirect(url_for("competitors"))


@app.route("/competitors/<int:id>/check", methods=["POST"])
@login_required
def force_check(id):
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
            return '<span class="text-yellow-400 text-sm font-medium">Mudanca detectada!</span>'
        return '<span class="text-green-400 text-sm">Verificado — sem mudancas.</span>'
    except Exception as e:
        return f'<span class="text-red-400 text-sm">Erro: {e}</span>'


# -- Routes: Alerts ----------------------------------------------------------

@app.route("/alerts/<int:id>")
@login_required
def view_alert(id):
    db = get_db()
    uid = session["user_id"]

    alert = db.execute("""
        SELECT a.*, c.name as competitor_name, c.pricing_url
        FROM alerts a
        JOIN competitors c ON a.competitor_id = c.id
        WHERE a.id = ? AND c.user_id = ?
    """, (id, uid)).fetchone()
    if not alert:
        flash("Alerta nao encontrado.", "error")
        return redirect(url_for("dashboard"))

    old_snap = None
    if alert["snapshot_old_id"]:
        old_snap = db.execute(
            "SELECT * FROM snapshots WHERE id = ?", (alert["snapshot_old_id"],)
        ).fetchone()

    new_snap = db.execute(
        "SELECT * FROM snapshots WHERE id = ?", (alert["snapshot_new_id"],)
    ).fetchone()

    diff_html = ""
    if old_snap and new_snap:
        diff_html = compute_diff_html(old_snap["content_text"], new_snap["content_text"])

    if not alert["seen"]:
        db.execute("UPDATE alerts SET seen = 1 WHERE id = ?", (id,))
        db.commit()

    return render_template("alert.html",
                           alert=alert,
                           old_snap=old_snap,
                           new_snap=new_snap,
                           diff_html=diff_html)


@app.route("/alerts/<int:id>/seen", methods=["POST"])
@login_required
def mark_seen(id):
    db = get_db()
    uid = session["user_id"]
    db.execute(
        "UPDATE alerts SET seen = 1 WHERE id = ? AND competitor_id IN "
        "(SELECT id FROM competitors WHERE user_id = ?)",
        (id, uid)
    )
    db.commit()
    return '<span class="badge badge-read">Visto</span>'


# -- Routes: API scan trigger ------------------------------------------------

@app.route("/api/run-scan", methods=["POST"])
def api_run_scan():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token != SCAN_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    result = run_scan_all()
    return jsonify(result)


# -- Template context --------------------------------------------------------

@app.context_processor
def inject_user():
    return {"user": current_user()}


# -- Init & Run --------------------------------------------------------------

init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
