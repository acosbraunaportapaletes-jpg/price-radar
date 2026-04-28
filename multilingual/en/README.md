# PriceRadar

**Know when your competitor changes pricing before your prospect does.**

## Getting Started

```bash
pip install -r requirements.txt
cp .env.example .env   # edit with your credentials
python app.py
```

Then open `http://localhost:5000`.

## Features

- **Auth** — Sign up and log in with email + password (Werkzeug hashing), secure cookie sessions
- **Competitors** — Full CRUD: name, pricing page URL, notes. Inline delete via htmx
- **Price Monitor** — Scheduled background job (threaded) that scrapes pricing pages, extracts text, hashes content, and detects diffs against the previous version
- **Email Alerts** — SMTP notifications when a change is detected, with a color-coded visual diff (before/after)
- **Timeline** — Snapshot history per competitor with a visual timeline of every detected change
- **Visual Diff** — Dedicated page showing a unified diff with added lines (green) and removed lines (red)
- **Manual Check** — Inline "Check Now" button via htmx — no page reload
- **htmx** — Inline delete, inline manual checks, instant feedback

## Stack

Flask + SQLite + htmx + Tailwind CDN (zero external dependencies beyond Flask)

## Environment Variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask secret key for sessions |
| `DATABASE_URL` | Path to the SQLite file (default: priceradar.db) |
| `SMTP_HOST` | SMTP server host |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |
| `CHECK_INTERVAL_HOURS` | Interval between automatic checks in hours (default: 6) |
| `ENABLE_MONITOR` | 1 to enable background monitoring, 0 to disable |

## Roadmap

- Webhook / Slack integration for alerts
- Custom CSS selectors per competitor to extract specific pricing data
- Playwright / Selenium support for JS-rendered pages
- Dashboard with pricing trend charts
- Multi-tenant with plans (free / pro) and usage limits
- Production deploy with Docker + Gunicorn