# PriceRadar

**Know when your competitor changes pricing before your prospect does.**

## Getting started

```bash
pip install -r requirements.txt
cp .env.example .env   # add your credentials
python app.py
```

Then open `http://localhost:5000`.

## Features

- **Auth** — Sign up and log in with email + password (Werkzeug hashing), secure cookie sessions
- **Competitors** — Add competitors with their pricing page URL, name, and configurable check interval
- **Smart scraper** — Extracts prices with BeautifulSoup (price regex) + OpenAI LLM fallback for complex pages
- **Change detection** — Diffs between snapshots: catches price changes, new plans, and removed plans
- **Email alerts** — SMTP notifications when a change is detected, with positioning suggestions
- **Dashboard** — View monitored competitors + recent alerts + manual checks via htmx
- **Scheduled jobs** — APScheduler runs every 30 min, respects the check interval configured per competitor
- **htmx** — Inline manual checks, inline delete, mark alerts as read — no page reload

## Stack

Flask + SQLite + htmx + Tailwind CDN + BeautifulSoup + APScheduler

## Environment variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask secret key for sessions |
| `DATABASE_URL` | SQLite file path (default: priceradar.db) |
| `OPENAI_API_KEY` | OpenAI key for LLM fallback on price extraction |
| `SMTP_HOST` | SMTP server host |
| `SMTP_PORT` | SMTP port (default: 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |
| `FROM_EMAIL` | Sender email address |

## Roadmap

- Webhook / Slack integration for alerts
- Dashboard with pricing trend charts
- Multi-tenant with plans (free / pro) and usage limits
- Custom CSS selector per competitor
- Playwright support for JS-rendered pages
- Docker + Gunicorn deployment