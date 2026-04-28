import os
import json
import sqlite3
import hashlib
import smtplib
import re
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import wraps

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


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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
            company_name TEXT,
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
            raw_html TEXT,
            extracted_json TEXT,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
            diff_summary TEXT NOT NULL,
            seen INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Price extraction
# ---------------------------------------------------------------------------

PRICE_RE = re.compile(
    r"(?:R\$|US\$|\$|€|£)\s*[\d.,]+|[\d.,]+\s*(?:/\s*m[eê]s|/mo|/month|/year|/ano)",
    re.IGNORECASE,
)


def extract_prices_from_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    plans = []
    seen = set()

    cards = soup.select(
        ".pricing, .price, .plan, [class*=pricing], [class*=price], [class*=plan]"
    )
    if not cards:
        cards = soup.select("section, article, .card, .col, [class*=card], [class*=col]")

    for card in cards:
        text = card.get_text(" ", strip=True)
        prices = PRICE_RE.findall(text)
        if prices:
            title_el = card.find(["h1", "h2", "h3", "h4", "strong", "b"])
            title = title_el.get_text(strip=True) if title_el else "Plano"
            price_str = prices[0].strip()
            key = f"{title}|{price_str}"
            if key not in seen:
                seen.add(key)
                plans.append({"plan": title, "price": price_str})

    if not plans:
        full_text = soup.get_text(" ", strip=True)
        all_prices = PRICE_RE.findall(full_text)
        for i, p in enumerate(all_prices[:10]):
            plans.append({"plan": f"Item {i+1}", "price": p.strip()})

    return plans


def try_llm_extraction(html: str) -> list[dict] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)[:4000]

        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Extract pricing plans from this page text. "
                            "Return JSON array of {plan, price}. Only valid JSON, no markdown."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                "temperature": 0,
            },
            timeout=30,
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(content)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

def compute_diff(old: list[dict], new: list[dict]) -> list[str]:
    changes = []
    old_map = {item.get("plan", ""): item.get("price", "") for item in old}
    new_map = {item.get("plan", ""): item.get("price", "") for item in new}

    for plan, price in new_map.items():
        if plan not in old_map:
            changes.append(f"NOVO PLANO: {plan} — {price}")
        elif old_map[plan] != price:
            changes.append(f"PRECO ALTERADO: {plan} — de {old_map[plan]} para {price}")

    for plan in old_map:
        if plan not in new_map:
            changes.append(f"PLANO REMOVIDO: {plan} (era {old_map[plan]})")

    return changes


# ---------------------------------------------------------------------------
# Email helper
# ---------------------------------------------------------------------------

def send_alert_email(to_email, competitor_name, diff_text):
    smtp_host = os.getenv("SMTP_HOST")
    if not smtp_host:
        print(f"[ALERT] SMTP nao configurado. Mudanca em: {competitor_name}")
        return

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
        f"<pre style='background:#111;color:#ccc;padding:12px;border-radius:8px;"
        f"font-size:13px;'>{diff_text}</pre>"
        f"<p><b>Sugestao:</b> revise seu posicionamento de preco considerando essas alteracoes.</p>"
        f"<p>Acesse o dashboard para ver o historico completo.</p>"
    )
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


# ---------------------------------------------------------------------------
# Snapshot logic
# ---------------------------------------------------------------------------

def take_snapshot(comp_id, db=None):
    own_conn = db is None
    if own_conn:
        db = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")

    comp = db.execute(
        "SELECT c.*, u.email as user_email FROM competitors c "
        "JOIN users u ON c.user_id = u.id WHERE c.id = ?",
        (comp_id,),
    ).fetchone()
    if not comp:
        if own_conn:
            db.close()
        return None

    try:
        resp = requests.get(
            comp["pricing_url"],
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PriceRadar/1.0)"},
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        if own_conn:
            db.close()
        raise

    plans = extract_prices_from_html(html)
    if not plans:
        llm_plans = try_llm_extraction(html)
        if llm_plans:
            plans = llm_plans

    extracted_json = json.dumps(plans, ensure_ascii=False)
    db.execute(
        "INSERT INTO snapshots (competitor_id, raw_html, extracted_json) VALUES (?, ?, ?)",
        (comp_id, html, extracted_json),
    )
    db.commit()

    snapshots = db.execute(
        "SELECT id, extracted_json, captured_at FROM snapshots "
        "WHERE competitor_id = ? ORDER BY id DESC LIMIT 2",
        (comp_id,),
    ).fetchall()

    changed = False
    diff_text = ""
    if len(snapshots) == 2:
        new_data = json.loads(snapshots[0]["extracted_json"] or "[]")
        old_data = json.loads(snapshots[1]["extracted_json"] or "[]")
        diffs = compute_diff(old_data, new_data)
        if diffs:
            changed = True
            diff_text = "\n".join(diffs)
            db.execute(
                "INSERT INTO alerts (competitor_id, diff_summary) VALUES (?, ?)",
                (comp_id, diff_text),
            )
            db.commit()
            threading.Thread(
                target=send_alert_email,
                args=(comp["user_email"], comp["name"], diff_text),
                daemon=True,
            ).start()

    if own_conn:
        db.close()
    return {"changed": changed, "diff": diff_text, "plans": plans}


def run_scheduled_checks():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")

    competitors = db.execute("SELECT * FROM competitors").fetchall()

    for comp in competitors:
        last = db.execute(
            "SELECT captured_at FROM snapshots WHERE competitor_id = ? ORDER BY id DESC LIMIT 1",
            (comp["id"],),
        ).fetchone()

        should_run = True
        if last:
            try:
                last_time = datetime.fromisoformat(last["captured_at"])
                if datetime.utcnow() - last_time < timedelta(hours=comp["check_interval_hours"]):
                    should_run = False
            except Exception:
                pass

        if should_run:
            try:
                take_snapshot(comp["id"], db)
            except Exception as e:
                print(f"[SCAN ERROR] competitor {comp['id']}: {e}")

    db.close()


# ---------------------------------------------------------------------------
# Routes: Landing & Auth
# ---------------------------------------------------------------------------

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
    company = request.form.get("company_name", "").strip()

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
        "INSERT INTO users (email, password_hash, company_name) VALUES (?, ?, ?)",
        (email, pw_hash, company or None),
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


# ---------------------------------------------------------------------------
# Routes: Dashboard
# ---------------------------------------------------------------------------

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
        WHERE c.user_id = ? AND a.seen = 0
        ORDER BY a.created_at DESC LIMIT 10
    """, (uid,)).fetchall()

    unseen = len(alerts)

    return render_template(
        "dashboard.html", competitors=competitors, alerts=alerts, unseen=unseen
    )


# ---------------------------------------------------------------------------
# Routes: Competitors
# ---------------------------------------------------------------------------

@app.route("/competitors", methods=["POST"])
@login_required
def add_competitor():
    db = get_db()
    uid = session["user_id"]

    name = request.form.get("name", "").strip()
    pricing_url = request.form.get("pricing_url", "").strip()
    interval = request.form.get("check_interval_hours", "24")

    if not name or not pricing_url:
        flash("Nome e URL sao obrigatorios.", "error")
        return redirect(url_for("dashboard"))

    if not pricing_url.startswith(("http://", "https://")):
        pricing_url = "https://" + pricing_url

    try:
        interval = max(1, int(interval))
    except ValueError:
        interval = 24

    db.execute(
        "INSERT INTO competitors (user_id, name, pricing_url, check_interval_hours) VALUES (?, ?, ?, ?)",
        (uid, name, pricing_url, interval),
    )
    db.commit()
    flash(f"Concorrente '{name}' adicionado!", "success")
    return redirect(url_for("dashboard"))


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

    snapshots_raw = db.execute(
        "SELECT id, extracted_json, captured_at FROM snapshots "
        "WHERE competitor_id = ? ORDER BY captured_at DESC LIMIT 30",
        (id,),
    ).fetchall()

    snapshots = []
    for s in snapshots_raw:
        plans = []
        if s["extracted_json"]:
            try:
                plans = json.loads(s["extracted_json"])
            except json.JSONDecodeError:
                pass
        snapshots.append({"id": s["id"], "captured_at": s["captured_at"], "plans": plans})

    alerts = db.execute(
        "SELECT * FROM alerts WHERE competitor_id = ? ORDER BY created_at DESC LIMIT 30",
        (id,),
    ).fetchall()

    return render_template(
        "competitor_detail.html", competitor=comp, snapshots=snapshots, alerts=alerts
    )


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
            diff_html = result["diff"].replace("\n", "<br>")
            return (
                f'<div class="mt-2 p-3 rounded-lg bg-yellow-900/30 border border-yellow-800 text-sm">'
                f'<span class="text-yellow-400 font-semibold">Mudanca detectada!</span>'
                f'<div class="text-yellow-200/80 mt-1 text-xs">{diff_html}</div>'
                f'</div>'
            )
        plans_count = len(result["plans"]) if result else 0
        return (
            f'<span class="text-green-400 text-sm">'
            f'Verificado — sem mudancas ({plans_count} plano{"s" if plans_count != 1 else ""} encontrado{"s" if plans_count != 1 else ""}).'
            f'</span>'
        )
    except Exception as e:
        return f'<span class="text-red-400 text-sm">Erro: {e}</span>'


# ---------------------------------------------------------------------------
# Routes: Alerts
# ---------------------------------------------------------------------------

@app.route("/alerts")
@login_required
def alerts_list():
    db = get_db()
    uid = session["user_id"]
    filter_seen = request.args.get("seen")

    query = """
        SELECT a.*, c.name as competitor_name
        FROM alerts a
        JOIN competitors c ON a.competitor_id = c.id
        WHERE c.user_id = ?
    """
    params = [uid]

    if filter_seen == "0":
        query += " AND a.seen = 0"
    elif filter_seen == "1":
        query += " AND a.seen = 1"

    query += " ORDER BY a.created_at DESC LIMIT 50"
    alerts = db.execute(query, params).fetchall()
    return render_template("alerts.html", alerts=alerts, filter_seen=filter_seen)


@app.route("/alerts/<int:id>/seen", methods=["POST"])
@login_required
def mark_seen(id):
    db = get_db()
    uid = session["user_id"]
    db.execute(
        "UPDATE alerts SET seen = 1 WHERE id = ? AND competitor_id IN "
        "(SELECT id FROM competitors WHERE user_id = ?)",
        (id, uid),
    )
    db.commit()
    return '<span class="text-xs text-gray-500">Lido</span>'


# ---------------------------------------------------------------------------
# Template context
# ---------------------------------------------------------------------------

@app.context_processor
def inject_user():
    return {"user": current_user()}


# ---------------------------------------------------------------------------
# Init & Run
# ---------------------------------------------------------------------------

init_db()

if __name__ == "__main__":
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    scheduler.add_job(run_scheduled_checks, "interval", minutes=30)
    scheduler.start()

    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
