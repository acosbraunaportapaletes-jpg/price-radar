import os
import re
import json
import sqlite3
import smtplib
import functools
from email.mime.text import MIMEText
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError

from flask import (
    Flask, g, request, redirect, url_for, session,
    render_template, flash, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

DATABASE = os.getenv("DATABASE_URL", "priceradar.db")


# -- DB helpers ----------------------------------------------------------------

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
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            raw_html TEXT,
            extracted_prices TEXT DEFAULT '[]',
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            diff_summary TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read INTEGER DEFAULT 0
        );
    """)
    db.close()


# -- Auth helpers --------------------------------------------------------------

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


# -- Scraper helpers -----------------------------------------------------------

PRICE_RE = re.compile(
    r"(?:R\$|US\$|\$|EUR|€|£)\s*[\d.,]+|[\d.,]+\s*(?:/m[eê]s|/month|/yr|/year)",
    re.IGNORECASE,
)


def extract_prices(html: str) -> list[str]:
    return sorted(set(PRICE_RE.findall(html)))


def fetch_page(url: str) -> str:
    headers = {"User-Agent": "PriceRadar/1.0 (price monitoring bot)"}
    req = Request(url, headers=headers)
    with urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def diff_prices(old: list[str], new: list[str]) -> str | None:
    old_set, new_set = set(old), set(new)
    if old_set == new_set:
        return None
    removed = old_set - new_set
    added = new_set - old_set
    parts = []
    if removed:
        parts.append("Removidos: " + ", ".join(sorted(removed)))
    if added:
        parts.append("Novos: " + ", ".join(sorted(added)))
    return " | ".join(parts)


# -- Email helper --------------------------------------------------------------

def send_alert_email(to_email, competitor_name, diff_summary):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    if not smtp_host:
        print(f"[email] SMTP not configured. Alert: {competitor_name} -> {diff_summary}")
        return
    try:
        body = (
            f"O concorrente {competitor_name} mudou de preco!\n\n"
            f"{diff_summary}\n\n"
            f"Acesse o PriceRadar para ver o historico completo."
        )
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"PriceRadar: {competitor_name} mudou de preco!"
        msg["From"] = smtp_user
        msg["To"] = to_email
        with smtplib.SMTP(smtp_host, 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"[email] Alert sent to {to_email}")
    except Exception as e:
        print(f"[email] Failed to send: {e}")


# -- Snapshot logic ------------------------------------------------------------

def take_snapshot(competitor_id):
    db = get_db()
    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ?", (competitor_id,)
    ).fetchone()
    if not comp:
        return {"error": "Concorrente nao encontrado"}

    try:
        html = fetch_page(comp["pricing_url"])
    except Exception as e:
        return {"error": f"Falha ao acessar URL: {e}"}

    prices = extract_prices(html)
    prices_json = json.dumps(prices, ensure_ascii=False)

    raw_html = html[:500_000]
    cur = db.execute(
        "INSERT INTO snapshots (competitor_id, raw_html, extracted_prices) VALUES (?, ?, ?)",
        (competitor_id, raw_html, prices_json),
    )
    db.commit()
    snapshot_id = cur.lastrowid

    prev = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? AND id != ? ORDER BY captured_at DESC LIMIT 1",
        (competitor_id, snapshot_id),
    ).fetchone()

    diff = None
    if prev:
        old_prices = json.loads(prev["extracted_prices"])
        diff = diff_prices(old_prices, prices)
        if diff:
            db.execute(
                "INSERT INTO alerts (snapshot_id, user_id, diff_summary) VALUES (?, ?, ?)",
                (snapshot_id, comp["user_id"], diff),
            )
            db.commit()
            user = db.execute("SELECT email FROM users WHERE id = ?", (comp["user_id"],)).fetchone()
            if user:
                try:
                    send_alert_email(user["email"], comp["name"], diff)
                except Exception as e:
                    print(f"[email] Error: {e}")

    return {"prices": prices, "diff": diff, "snapshot_id": snapshot_id}


# -- Routes: Auth --------------------------------------------------------------

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


# -- Routes: Dashboard --------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]
    competitors = db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM snapshots WHERE competitor_id = c.id) as snap_count,
            (SELECT extracted_prices FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_prices,
            (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_check
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()
    recent_alerts = db.execute("""
        SELECT a.*, c.name as competitor_name, c.id as competitor_id
        FROM alerts a
        JOIN snapshots s ON a.snapshot_id = s.id
        JOIN competitors c ON s.competitor_id = c.id
        WHERE a.user_id = ?
        ORDER BY a.sent_at DESC LIMIT 20
    """, (uid,)).fetchall()
    unread = db.execute(
        "SELECT COUNT(*) as n FROM alerts WHERE user_id = ? AND read = 0", (uid,)
    ).fetchone()["n"]
    return render_template("dashboard.html",
                           competitors=competitors,
                           alerts=recent_alerts,
                           unread_count=unread)


# -- Routes: Competitors ------------------------------------------------------

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
        else:
            if not pricing_url.startswith(("http://", "https://")):
                pricing_url = "https://" + pricing_url
            db.execute(
                "INSERT INTO competitors (user_id, name, pricing_url, notes) VALUES (?, ?, ?, ?)",
                (uid, name, pricing_url, notes),
            )
            db.commit()
            flash(f"Concorrente '{name}' adicionado!", "success")

        if request.headers.get("HX-Request"):
            comps = _load_competitors(db, uid)
            return render_template("partials/competitor_list.html", competitors=comps)
        return redirect(url_for("competitors"))

    comps = _load_competitors(db, uid)
    return render_template("competitors.html", competitors=comps)


def _load_competitors(db, uid):
    return db.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM snapshots WHERE competitor_id = c.id) as snap_count,
            (SELECT extracted_prices FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_prices,
            (SELECT captured_at FROM snapshots WHERE competitor_id = c.id
             ORDER BY captured_at DESC LIMIT 1) as last_check
        FROM competitors c WHERE c.user_id = ? ORDER BY c.created_at DESC
    """, (uid,)).fetchall()


@app.route("/competitors/<int:id>", methods=["GET", "DELETE"])
@login_required
def competitor_detail(id):
    db = get_db()
    uid = session["user_id"]
    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        if request.method == "DELETE":
            return "", 404
        flash("Concorrente nao encontrado.", "error")
        return redirect(url_for("competitors"))

    if request.method == "DELETE":
        db.execute("DELETE FROM competitors WHERE id = ?", (id,))
        db.commit()
        if request.headers.get("HX-Request"):
            return ""
        return redirect(url_for("competitors"))

    snaps = db.execute(
        "SELECT * FROM snapshots WHERE competitor_id = ? ORDER BY captured_at DESC",
        (id,),
    ).fetchall()

    snap_data = []
    for s in snaps:
        snap_data.append({
            "id": s["id"],
            "captured_at": s["captured_at"],
            "prices": json.loads(s["extracted_prices"]),
        })

    alerts_list = db.execute("""
        SELECT * FROM alerts
        WHERE snapshot_id IN (SELECT id FROM snapshots WHERE competitor_id = ?)
        ORDER BY sent_at DESC
    """, (id,)).fetchall()

    return render_template("competitor_detail.html",
                           competitor=comp,
                           snapshots=snap_data,
                           alerts=alerts_list)


@app.route("/competitors/<int:id>/check", methods=["POST"])
@login_required
def manual_check(id):
    db = get_db()
    uid = session["user_id"]
    comp = db.execute(
        "SELECT * FROM competitors WHERE id = ? AND user_id = ?", (id, uid)
    ).fetchone()
    if not comp:
        if request.headers.get("HX-Request"):
            return "<p class='text-red-400 text-sm'>Concorrente nao encontrado.</p>", 404
        abort(404)

    result = take_snapshot(id)

    if request.headers.get("HX-Request"):
        if "error" in result:
            return (
                f'<div class="mt-2 p-3 rounded-lg bg-red-900/30 border border-red-800 text-sm">'
                f'<span class="text-red-400">{result["error"]}</span></div>'
            )
        prices = result["prices"]
        label = ", ".join(prices) if prices else "nenhum preco encontrado"
        diff_html = ""
        if result["diff"]:
            diff_html = (
                f'<div class="mt-1 text-yellow-400 text-xs">'
                f'Mudanca detectada: {result["diff"]}</div>'
            )
        return (
            f'<div class="mt-2 p-3 rounded-lg bg-emerald-900/30 border border-emerald-800 text-sm">'
            f'<span class="text-emerald-400 font-semibold">Snapshot capturado!</span>'
            f'<p class="text-gray-300 text-xs mt-1">{label}</p>'
            f'{diff_html}</div>'
        )

    flash(f"Snapshot capturado. Precos: {', '.join(result.get('prices', []))}", "info")
    return redirect(url_for("competitor_detail", id=id))


# -- Routes: Alerts -----------------------------------------------------------

@app.route("/alerts")
@login_required
def alerts():
    db = get_db()
    uid = session["user_id"]
    all_alerts = db.execute("""
        SELECT a.*, s.competitor_id, c.name as competitor_name
        FROM alerts a
        JOIN snapshots s ON a.snapshot_id = s.id
        JOIN competitors c ON s.competitor_id = c.id
        WHERE a.user_id = ?
        ORDER BY a.sent_at DESC
    """, (uid,)).fetchall()
    db.execute("UPDATE alerts SET read = 1 WHERE user_id = ? AND read = 0", (uid,))
    db.commit()
    return render_template("alerts.html", alerts=all_alerts)


# -- Init & Run ----------------------------------------------------------------

init_db()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
