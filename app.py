import os
import re
import sqlite3
import smtplib
import functools
from email.mime.text import MIMEText
from datetime import datetime

from flask import (
    Flask, g, request, redirect, url_for, session,
    render_template, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import requests as http_requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

DATABASE = os.getenv("DATABASE_URL", "priceradar.db")


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
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
            css_selector TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            raw_html TEXT,
            extracted_price TEXT,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            old_price TEXT,
            new_price TEXT,
            notified_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.close()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @functools.wraps(f)
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


# ── Scraper helpers ───────────────────────────────────────────────────────────

def extract_price(html, css_selector=None):
    soup = BeautifulSoup(html, "html.parser")
    if css_selector:
        el = soup.select_one(css_selector)
        if el:
            return el.get_text(strip=True)
    patterns = [
        r"R\$\s?[\d.,]+",
        r"US\$\s?[\d.,]+",
        r"\$\s?[\d.,]+",
        r"€\s?[\d.,]+",
        r"[\d.,]+\s?(?:USD|BRL|EUR)",
    ]
    text = soup.get_text()
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0).strip()
    return None


def scrape_competitor(competitor_id, pricing_url, css_selector, db):
    try:
        headers = {"User-Agent": "PriceRadar/1.0 (price monitoring bot)"}
        resp = http_requests.get(pricing_url, headers=headers, timeout=15)
        resp.raise_for_status()
        raw_html = resp.text
        price = extract_price(raw_html, css_selector)

        cur = db.execute(
            "INSERT INTO snapshots (competitor_id, raw_html, extracted_price) VALUES (?, ?, ?)",
            (competitor_id, raw_html, price),
        )
        db.commit()
        snapshot_id = cur.lastrowid

        prev = db.execute(
            "SELECT extracted_price FROM snapshots WHERE competitor_id = ? AND id != ? ORDER BY captured_at DESC LIMIT 1",
            (competitor_id, snapshot_id),
        ).fetchone()

        if prev and prev["extracted_price"] and price and prev["extracted_price"] != price:
            db.execute(
                "INSERT INTO alerts (snapshot_id, old_price, new_price) VALUES (?, ?, ?)",
                (snapshot_id, prev["extracted_price"], price),
            )
            db.commit()
            alert_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            row = db.execute(
                "SELECT u.email, c.name FROM competitors c JOIN users u ON c.user_id = u.id WHERE c.id = ?",
                (competitor_id,),
            ).fetchone()
            if row:
                send_alert_email(row["email"], row["name"], prev["extracted_price"], price)
                db.execute("UPDATE alerts SET notified_at = CURRENT_TIMESTAMP WHERE id = ?", (alert_id,))
                db.commit()

        return price
    except Exception as e:
        print(f"[scraper] Error scraping competitor {competitor_id}: {e}")
        return None


def send_alert_email(to_email, competitor_name, old_price, new_price):
    smtp_host = os.getenv("SMTP_HOST")
    if not smtp_host:
        print(f"[email] SMTP not configured. Alert: {competitor_name} {old_price} -> {new_price}")
        return
    try:
        body = (
            f"O concorrente {competitor_name} mudou o preco de {old_price} para {new_price}.\n\n"
            f"Acesse o PriceRadar para mais detalhes."
        )
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"PriceRadar: {competitor_name} mudou de preco!"
        msg["From"] = os.getenv("FROM_EMAIL", "noreply@priceradar.app")
        msg["To"] = to_email

        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(smtp_host, port) as server:
            server.starttls()
            server.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", ""))
            server.send_message(msg)
        print(f"[email] Alert sent to {to_email}")
    except Exception as e:
        print(f"[email] Failed to send: {e}")


def run_scheduled_scrape():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    competitors = db.execute("SELECT id, pricing_url, css_selector FROM competitors").fetchall()
    for c in competitors:
        scrape_competitor(c["id"], c["pricing_url"], c["css_selector"], db)
    db.close()
    print(f"[scheduler] Scrape done for {len(competitors)} competitors at {datetime.utcnow()}")


# ── Routes: Auth ──────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
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
        cur = db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, pw_hash))
        db.commit()
        session["user_id"] = cur.lastrowid
        session["user_email"] = email
        flash("Conta criada com sucesso!", "success")
        return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Email ou senha incorretos.", "error")
            return render_template("login.html"), 401
        session["user_id"] = user["id"]
        session["user_email"] = user["email"]
        return redirect(url_for("dashboard"))
    return render_template("login.html")


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
            (SELECT extracted_price FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_price,
            (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_check
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()
    recent_alerts = db.execute("""
        SELECT a.*, s.competitor_id, c.name as competitor_name
        FROM alerts a
        JOIN snapshots s ON a.snapshot_id = s.id
        JOIN competitors c ON s.competitor_id = c.id
        WHERE c.user_id = ?
        ORDER BY a.created_at DESC LIMIT 20
    """, (uid,)).fetchall()
    return render_template("dashboard.html", competitors=competitors, alerts=recent_alerts)


# ── Routes: Competitors ───────────────────────────────────────────────────────

@app.route("/competitors", methods=["GET", "POST"])
@login_required
def competitors():
    db = get_db()
    uid = session["user_id"]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pricing_url = request.form.get("pricing_url", "").strip()
        css_selector = request.form.get("css_selector", "").strip() or None
        if not name or not pricing_url:
            flash("Nome e URL sao obrigatorios.", "error")
        else:
            if not pricing_url.startswith(("http://", "https://")):
                pricing_url = "https://" + pricing_url
            db.execute(
                "INSERT INTO competitors (user_id, name, pricing_url, css_selector) VALUES (?, ?, ?, ?)",
                (uid, name, pricing_url, css_selector),
            )
            db.commit()
            flash(f"Concorrente '{name}' adicionado!", "success")

        if request.headers.get("HX-Request"):
            comps = db.execute("""
                SELECT c.*,
                    (SELECT COUNT(*) FROM snapshots WHERE competitor_id = c.id) as snap_count,
                    (SELECT extracted_price FROM snapshots WHERE competitor_id = c.id
                     ORDER BY captured_at DESC LIMIT 1) as last_price,
                    (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
                     ORDER BY captured_at DESC LIMIT 1) as last_check
                FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
            """, (uid,)).fetchall()
            return render_template("partials/competitor_list.html", competitors=comps)
        return redirect(url_for("competitors"))

    comps = db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM snapshots WHERE competitor_id = c.id) as snap_count,
            (SELECT extracted_price FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_price,
            (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_check
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()
    return render_template("competitors.html", competitors=comps)


@app.route("/competitors/<int:id>/delete", methods=["DELETE", "POST"])
@login_required
def delete_competitor(id):
    db = get_db()
    uid = session["user_id"]
    comp = db.execute("SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)).fetchone()
    if not comp:
        abort(404)
    db.execute("DELETE FROM competitors WHERE id = ?", (id,))
    db.commit()
    if request.headers.get("HX-Request"):
        return ""
    flash("Concorrente removido.", "success")
    return redirect(url_for("competitors"))


@app.route("/competitors/<int:id>/snapshots")
@login_required
def snapshots(id):
    db = get_db()
    uid = session["user_id"]
    comp = db.execute("SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)).fetchone()
    if not comp:
        abort(404)
    snaps = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC", (id,)
    ).fetchall()
    alerts_map = {}
    for s in snaps:
        alert = db.execute("SELECT * FROM alerts WHERE snapshot_id = ?", (s["id"],)).fetchone()
        if alert:
            alerts_map[s["id"]] = alert
    return render_template("snapshots.html", competitor=comp, snapshots=snaps, alerts_map=alerts_map)


@app.route("/competitors/<int:id>/check", methods=["POST"])
@login_required
def manual_check(id):
    db = get_db()
    uid = session["user_id"]
    comp = db.execute("SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)).fetchone()
    if not comp:
        abort(404)
    price = scrape_competitor(comp["id"], comp["pricing_url"], comp["css_selector"], db)
    if request.headers.get("HX-Request"):
        label = price or "nao encontrado"
        alert = db.execute("""
            SELECT a.* FROM alerts a
            JOIN snapshots s ON a.snapshot_id = s.id
            WHERE s.competitor_id = ?
            ORDER BY a.created_at DESC LIMIT 1
        """, (id,)).fetchone()
        if alert and alert["created_at"] and (datetime.utcnow() - datetime.fromisoformat(alert["created_at"])).total_seconds() < 5:
            return (
                f'<div class="mt-2 p-3 rounded-lg bg-yellow-900/30 border border-yellow-800 text-sm">'
                f'<span class="text-yellow-400 font-semibold">Mudanca de preco detectada!</span>'
                f'<p class="text-yellow-200/80 mt-1 text-xs">{alert["old_price"]} &rarr; {alert["new_price"]}</p>'
                f'</div>'
            )
        return f'<span class="text-green-400 text-sm">Preco: {label}</span>'
    flash(f"Check realizado. Preco: {price or 'nao encontrado'}", "info")
    return redirect(url_for("snapshots", id=id))


# ── Routes: Alerts ────────────────────────────────────────────────────────────

@app.route("/alerts")
@login_required
def alerts():
    db = get_db()
    uid = session["user_id"]
    all_alerts = db.execute("""
        SELECT a.*, s.competitor_id, s.extracted_price, c.name as competitor_name
        FROM alerts a
        JOIN snapshots s ON a.snapshot_id = s.id
        JOIN competitors c ON s.competitor_id = c.id
        WHERE c.user_id = ?
        ORDER BY a.created_at DESC
    """, (uid,)).fetchall()
    return render_template("alerts.html", alerts=all_alerts)


# ── Init & Run ────────────────────────────────────────────────────────────────

init_db()

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(run_scheduled_scrape, "interval", hours=6, id="scrape_job")
scheduler.start()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
