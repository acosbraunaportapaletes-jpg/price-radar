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
- **Competidores** — CRUD completo: nombre, URL de la página de pricing, notas. Eliminación inline vía htmx
- **Price Monitor** — Job programado (thread) que hace scraping de la página de pricing, extrae texto, calcula hash y detecta diferencias vs la versión anterior
- **Alertas por email** — Notificación vía SMTP cuando se detecta un cambio, con diff visual (antes/después) resaltado en colores
- **Timeline** — Historial de snapshots por competidor con timeline visual de todos los cambios detectados
- **Diff visual** — Página dedicada mostrando el diff unificado con líneas agregadas (verde) y eliminadas (rojo)
- **Verificación manual** — Botón "Verificar ahora" inline vía htmx, sin recarga de página
- **htmx** — Eliminación inline, verificación manual inline, feedback instantáneo

## Stack

Flask + SQLite + htmx + Tailwind CDN (cero dependencias externas además de Flask)

## Variables de entorno

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Clave secreta de Flask para sesiones |
| `DATABASE_URL` | Ruta del archivo SQLite (default: priceradar.db) |
| `SMTP_HOST` | Host del servidor SMTP |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Contraseña SMTP |
| `CHECK_INTERVAL_HOURS` | Intervalo entre verificaciones automáticas en horas (default: 6) |
| `ENABLE_MONITOR` | 1 para activar monitor en background, 0 para desactivar |

## Próximos pasos

- Webhook/Slack integration para alertas
- Selector CSS personalizado por competidor para extraer precios específicos
- Playwright/Selenium para páginas con JS rendering
- Dashboard con gráficas de evolución de precio
- Multi-usuario con planes (free/pro) y límites
- Deploy con Docker + Gunicorn