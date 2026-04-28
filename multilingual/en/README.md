# PriceRadar

**Know when your competitor changes pricing before your prospect does.**

## Getting started

```bash
pip install -r requirements.txt
cp .env.example .env   # add your credentials
python app.py
```

Open `http://localhost:5000`.

## Features

- **Auth** — Sign up and log in with email + password (Werkzeug hashing), Flask sessions
- **Competitor CRUD** — Add, edit, and remove competitors with their pricing page URL and notes
- **Scheduled scraper** — APScheduler captures HTML/text snapshots and detects diffs automatically
- **Email alerts** — Automatic SMTP notifications when a change is detected, with before/after diff
- **Dashboard** — Overview panel with alert timeline, date filters, and read/unread status
- **Alerts page** — Full alert list with read/unread filtering and inline status toggling via htmx
- **Snapshot history** — Per-competitor snapshot timeline with inline diffs
- **Manual check** — Force a snapshot at any time
- **htmx** — Inline delete, mark alerts as read without page reload

## Stack

Flask + SQLite + htmx + Tailwind CDN + APScheduler + BeautifulSoup

## Roadmap

- Webhook/Slack notifications
- CSS selector targeting to monitor only the pricing section
- Pricing history with charts
- Multi-user with plans (free/pro)
- Docker deploy + external cron job
- Puppeteer/Playwright for JS-rendered pages