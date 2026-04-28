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
- **CRUD Concorrentes** — Nome, URL da pricing page, seletor CSS configuravel por concorrente
- **Price Scraper** — Job agendado (APScheduler, 6h) que faz scraping das pricing pages, extrai precos via seletor CSS + fallback regex, salva snapshot
- **Deteccao de mudanca** — Compara ultimo snapshot com anterior; se preco mudou, cria alerta e dispara email SMTP
- **Dashboard** — Painel com alertas recentes, precos atuais, e acesso rapido a cada concorrente
- **Timeline** — Historico de snapshots por concorrente com preco atual vs anterior e data da alteracao
- **Verificacao manual** — Botao "Verificar agora" inline via htmx, sem reload
- **Alertas** — Pagina dedicada com todos os alertas de mudanca de preco

## Stack

Flask + SQLite + APScheduler + BeautifulSoup4 + htmx + Tailwind CDN

## Env vars

| Variavel | Descricao |
|---|---|
| `SECRET_KEY` | Chave secreta Flask para sessoes |
| `DATABASE_URL` | Caminho do arquivo SQLite (default: priceradar.db) |
| `SMTP_HOST` | Host do servidor SMTP |
| `SMTP_PORT` | Porta SMTP (default: 587) |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Senha SMTP |
| `FROM_EMAIL` | Email remetente dos alertas |

## Proximos passos

- Webhook/Slack integration para alertas
- Playwright/Selenium para paginas com JS rendering
- Dashboard com graficos de evolucao de preco
- Multi-user com planos (free/pro) e limites
- Deploy com Docker + Gunicorn
