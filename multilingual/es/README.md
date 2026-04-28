# PriceRadar

**Entérate cuando tu competidor cambia sus precios. Antes que tu prospecto.**

## Cómo ejecutar

```bash
pip install -r requirements.txt
cp .env.example .env   # edita con tus credenciales
python app.py
```

Accede a `http://localhost:5000`.

## Features implementadas

- **Auth** -- Registro e inicio de sesión con email+contraseña (hash Werkzeug), sesión vía cookie segura
- **Competidores** -- CRUD de competidores con URL de la página de pricing y nombre
- **Price Monitor** -- Job programado (APScheduler, 1h) que hace scrape de la página de pricing, extrae texto, compara hash con snapshot anterior; si cambió, crea alerta
- **Alertas** -- Cuando el snapshot cambia, envía email vía SMTP y muestra badge en el dashboard con diff lado a lado (antes/después)
- **Battle Card** -- Prompt pre-armado enviado a OpenAI API que recibe el diff de pricing y genera una battle card resumida con talking points para ventas
- **htmx** -- Verificación manual inline, delete inline, marcar alerta como leído sin recargar
- **Diff visual** -- Comparación lado a lado (HTML diff) y diff unificado en texto

## Stack

Flask + SQLite + htmx + Tailwind CDN + BeautifulSoup + APScheduler + OpenAI API

## Variables de entorno

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Clave secreta de Flask para sesiones |
| `DATABASE_URL` | Ruta del archivo SQLite |
| `OPENAI_API_KEY` | Clave de la API de OpenAI para generar battle cards |
| `SMTP_HOST` | Host del servidor SMTP |
| `SMTP_PORT` | Puerto SMTP (default 587) |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Contraseña SMTP |
| `FROM_EMAIL` | Email remitente de las alertas |

## Próximos pasos

- Selector CSS para monitorear solo la sección de precios
- Puppeteer/Playwright para páginas con JS rendering
- Webhook/Slack notifications
- Multi-usuario con planes (free/pro) y límites
- Deploy con Docker