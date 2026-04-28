# PriceRadar

**Know when your competitor changes pricing. Before your prospect does.**

## Getting Started

```bash
pip install -r requirements.txt
cp .env.example .env   # edit with your credentials
python app.py
```

Then open `http://localhost:5000`.

## Features

- **Auth** — Sign up and log in with email + password (Werkzeug hashing), secure cookie-based sessions
- **Competitors** — Full CRUD for competitors with pricing page URL and name
- **Price Monitor** — Scheduled job (APScheduler, every 1h) that scrapes the pricing page, extracts text, and compares the hash against the previous snapshot; if it changed, an alert is created
- **Alerts** — When a snapshot changes, sends an email via SMTP and shows a badge on the dashboard with a side-by-side diff (before/after)
- **Battle Card** — Pre-built prompt sent to the OpenAI API that takes a pricing diff and generates a concise battle card with talking points for sales
- **htmx** — Inline manual check, inline delete, mark alert as read — all without a page reload
- **Visual Diff** — Side-by-side comparison (HTML diff) and unified text diff

## Stack

Flask + SQLite + htmx + Tailwind CDN + BeautifulSoup + APScheduler + OpenAI API

## Environment Variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask secret key for sessions |
| `DATABASE_URL` | Path to the SQLite database file |
| `OPENAI_API_KEY` | OpenAI API key for generating battle cards |
| `SMTP_HOST` | SMTP server host |
| `SMTP_PORT` | SMTP port (defaults to 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |
| `FROM_EMAIL` | Sender email address for alerts |

## Roadmap

- CSS selector targeting to monitor only the pricing section
- Puppeteer/Playwright support for JS-rendered pages
- Webhook/Slack notifications
- Multi-tenant with plans (free/pro) and usage limits
- Docker deployment