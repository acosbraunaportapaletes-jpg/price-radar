# PriceRadar

**Enteráte cuando tu competidor cambia de precio antes que tu prospecto.**

## Cómo ejecutar

```bash
pip install -r requirements.txt
cp .env.example .env   # editá con tus credenciales
python app.py
```

Accedé a `http://localhost:5000`.

## Features implementadas

- **Auth** — Registro y login con email+contraseña (hash Werkzeug), sesión vía cookie segura
- **Competidores** — Alta con URL de pricing, nombre e intervalo de verificación configurable
- **Scraper inteligente** — Extrae precios con BeautifulSoup (regex de precios) + fallback vía OpenAI LLM para páginas complejas
- **Detección de cambios** — Diff entre snapshots: detecta precio modificado, plan nuevo o plan eliminado
- **Alertas por email** — Notificación vía SMTP cuando se detecta un cambio, con sugerencia de posicionamiento
- **Dashboard** — Lista competidores monitoreados + alertas recientes + verificación manual vía htmx
- **Job programado** — APScheduler corre cada 30 min, respeta el intervalo configurado por competidor
- **htmx** — Verificación manual inline, eliminación inline, marcar alerta como leída sin recargar

## Stack

Flask + SQLite + htmx + Tailwind CDN + BeautifulSoup + APScheduler

## Variables de entorno

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Clave secreta de Flask para sesiones |
| `DATABASE_URL` | Ruta del archivo SQLite (default: priceradar.db) |
| `OPENAI_API_KEY` | Clave de OpenAI para fallback LLM en la extracción de precios |
| `SMTP_HOST` | Host del servidor SMTP |
| `SMTP_PORT` | Puerto SMTP (default: 587) |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Contraseña SMTP |
| `FROM_EMAIL` | Email remitente |

## Próximos pasos

- Webhook/Slack integration para alertas
- Dashboard con gráficos de evolución de precio
- Multi-user con planes (free/pro) y límites
- Selector CSS personalizado por competidor
- Playwright para páginas con JS rendering
- Deploy con Docker + Gunicorn