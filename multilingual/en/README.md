# PriceRadar

**Know when your competitor changes pricing before your prospect does.**

## Getting started

```bash
pip install -r requirements.txt
cp .env.example .env   # edit with your credentials
python app.py
```

Open `http://localhost:5000`.

## Features

- **Auth** — Sign up and log in with email + password (Werkzeug hashing), secure cookie sessions
- **Competitor CRUD** — Name, pricing page URL, configurable CSS selector per competitor
- **Price Scraper** — Scheduled job (APScheduler, every 6h) that scrapes pricing pages, extracts prices via CSS selector + regex fallback, and saves a snapshot
- **Change detection** — Compares the latest snapshot against the previous one; if pricing changed, creates an alert and fires off an SMTP email
- **Dashboard** — Overview panel with recent alerts, current prices, and quick access to each competitor
- **Timeline** — Snapshot history per competitor showing current vs. previous price and change date
- **Manual check** — Inline "Check now" button powered by htmx, no page reload
- **Alerts** — Dedicated page listing all price change alerts

## Stack

Flask + SQLite + APScheduler + BeautifulSoup4 + htmx + Tailwind CDN

## Environment variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask secret key for sessions |
| `DATABASE_URL` | Path to the SQLite file (default: priceradar.db) |
| `SMTP_HOST` | SMTP server host |
| `SMTP_PORT` | SMTP port (default: 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |
| `FROM_EMAIL` | Sender email for alerts |

## Roadmap

- Webhook / Slack integration for alerts
- Playwright / Selenium support for JS-rendered pages
- Dashboard with price trend charts
- Multi-user with tiered plans (free / pro) and usage limits
- Docker + Gunicorn deployment