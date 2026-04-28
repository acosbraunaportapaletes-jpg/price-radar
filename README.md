# PriceRadar

**Saiba quando seu concorrente muda de preco antes do seu prospect.**

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # edite com suas credenciais
python app.py
```

Acesse `http://localhost:5000`.

## Features implementadas

- **Auth** — Cadastro e login com email+senha (hash Werkzeug), sessao via cookie seguro
- **Concorrentes** — CRUD completo de concorrentes com URL da pagina de pricing e nome
- **Scraper** — Captura snapshots HTML/texto da pricing page, extrai texto limpo com BeautifulSoup, compara hash com versao anterior
- **Alertas** — Quando diff detecta mudanca, envia email ao usuario via SMTP e marca alerta no dashboard
- **Dashboard** — Timeline de mudancas detectadas por concorrente com diff visual (antes/depois) lado a lado
- **htmx** — Verificacao manual inline, delete inline, marcar alerta como visto sem reload
- **API Scan** — Endpoint `POST /api/run-scan` protegido por Bearer token para trigger manual ou cron externo

## Stack

Flask + SQLite + htmx + Tailwind CDN + BeautifulSoup

## Env vars

| Variavel | Descricao |
|---|---|
| `SECRET_KEY` | Chave secreta Flask para sessoes |
| `DATABASE_URL` | Caminho do arquivo SQLite (default: priceradar.db) |
| `SMTP_HOST` | Host do servidor SMTP |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Senha SMTP |
| `SCAN_INTERVAL_HOURS` | Intervalo entre scans (para cron externo) |
| `SCAN_TOKEN` | Token para autenticar chamadas ao /api/run-scan |

## Proximos passos

- Seletor CSS para monitorar apenas a secao de precos
- Playwright para paginas com JS rendering
- Webhook/Slack notifications
- Multi-user com planos (free/pro) e limites
- Deploy com Docker + cron job para scans automaticos
