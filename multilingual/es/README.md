# PriceRadar

**Entérate cuando tu competidor cambia de precio antes que tu prospecto.**

## Cómo ejecutar

```bash
pip install -r requirements.txt
cp .env.example .env   # edita con tus credenciales
python app.py
```

Accede a `http://localhost:5000`.

## Features implementadas

- **Auth** — Registro e inicio de sesión con email+contraseña (hash Werkzeug), sesión vía cookie segura
- **CRUD Competidores** — Nombre, URL de la pricing page, selector CSS configurable por competidor
- **Price Scraper** — Job programado (APScheduler, 6h) que hace scraping de las pricing pages, extrae precios vía selector CSS + fallback regex, guarda snapshot
- **Detección de cambios** — Compara el último snapshot con el anterior; si el precio cambió, crea alerta y dispara email SMTP
- **Dashboard** — Panel con alertas recientes, precios actuales y acceso rápido a cada competidor
- **Timeline** — Historial de snapshots por competidor con precio actual vs anterior y fecha del cambio
- **Verificación manual** — Botón "Verificar ahora" inline vía htmx, sin reload
- **Alertas** — Página dedicada con todas las alertas de cambio de precio

## Stack

Flask + SQLite + APScheduler + BeautifulSoup4 + htmx + Tailwind CDN

## Variables de entorno

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Clave secreta de Flask para sesiones |
| `DATABASE_URL` | Ruta del archivo SQLite (default: priceradar.db) |
| `SMTP_HOST` | Host del servidor SMTP |
| `SMTP_PORT` | Puerto SMTP (default: 587) |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Contraseña SMTP |
| `FROM_EMAIL` | Email remitente de las alertas |

## Próximos pasos

- Webhook/Slack integration para alertas
- Playwright/Selenium para páginas con JS rendering
- Dashboard con gráficos de evolución de precio
- Multi-user con planes (free/pro) y límites
- Deploy con Docker + Gunicorn