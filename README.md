# PriceRadar

**Saiba quando seu concorrente muda o preco antes do seu prospect.**

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # edite com suas credenciais
python app.py
```

Acesse `http://localhost:5000`.

## Features implementadas

- **Auth** — Cadastro e login com email+senha (hash Werkzeug), sessao Flask
- **CRUD de concorrentes** — Adicione, edite e remova concorrentes com URL da pricing page e notas
- **Scraper agendado** — APScheduler captura snapshots HTML/texto e detecta diffs automaticamente
- **Alertas por email** — Notificacao automatica via SMTP quando mudanca e detectada, com diff antes/depois
- **Dashboard** — Painel com timeline de alertas, filtro por data e status lido/nao-lido
- **Pagina de alertas** — Lista completa com filtro lido/nao-lido e marcacao via htmx
- **Historico de snapshots** — Timeline de snapshots por concorrente com diff inline
- **Verificacao manual** — Force um snapshot a qualquer momento
- **htmx** — Delete inline, marcar alerta como lido sem reload

## Stack

Flask + SQLite + htmx + Tailwind CDN + APScheduler + BeautifulSoup

## Proximos passos

- Webhook/Slack notifications
- Seletor CSS para monitorar apenas a secao de precos
- Historico de precos com grafico
- Multi-user com planos (free/pro)
- Deploy com Docker + cron job externo
- Puppeteer/Playwright para paginas com JS rendering
