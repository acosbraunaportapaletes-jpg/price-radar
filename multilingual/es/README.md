# PriceRadar

**Entérate cuando tu competencia cambia sus precios antes que tu prospecto.**

## Cómo ejecutar

```bash
pip install -r requirements.txt
cp .env.example .env   # edita con tus credenciales
python app.py
```

Accede a `http://localhost:5000`.

## Features implementadas

- **Auth** — Registro e inicio de sesión con email+contraseña (hash Werkzeug), sesión Flask
- **CRUD de competidores** — Agrega, edita y elimina competidores con URL de su página de precios y notas
- **Scraper programado** — APScheduler captura snapshots HTML/texto y detecta diffs automáticamente
- **Alertas por email** — Notificación automática vía SMTP cuando se detecta un cambio, con diff antes/después
- **Dashboard** — Panel con timeline de alertas, filtro por fecha y estado leído/no leído
- **Página de alertas** — Lista completa con filtro leído/no leído y marcación vía htmx
- **Historial de snapshots** — Timeline de snapshots por competidor con diff inline
- **Verificación manual** — Fuerza un snapshot en cualquier momento
- **htmx** — Delete inline, marcar alerta como leído sin recargar

## Stack

Flask + SQLite + htmx + Tailwind CDN + APScheduler + BeautifulSoup

## Próximos pasos

- Webhook/Slack notifications
- Selector CSS para monitorear solo la sección de precios
- Historial de precios con gráfica
- Multi-usuario con planes (free/pro)
- Deploy con Docker + cron job externo
- Puppeteer/Playwright para páginas con JS rendering