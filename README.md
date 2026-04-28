# PriceRadar

**Saiba quando seu concorrente muda de preco antes do proximo deal.**

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # edite com suas credenciais
python app.py
```

Acesse `http://localhost:5000`.

## Features implementadas

- **Auth** — Cadastro e login com email+senha (hash Werkzeug), sessao via cookie seguro
- **CRUD Concorrentes** — Nome, URL da pricing page, notas. Adicionar, listar, detalhar e remover
- **Price Scraper** — Captura HTML de pricing pages, extrai precos via regex, salva snapshot com historico
- **Deteccao de mudanca** — Diff automatico entre snapshots; se precos mudaram, cria alerta e dispara email SMTP
- **Dashboard** — Painel com concorrentes, contagem de alertas nao lidos, e alertas recentes
- **Timeline** — Historico de snapshots por concorrente com precos extraidos e data
- **Verificacao manual** — Botao "Verificar agora" inline via htmx, sem reload
- **Alertas** — Pagina dedicada com todos os alertas, marcados como lidos ao visualizar

## Stack

Flask + SQLite + htmx + Tailwind CDN (zero JS custom)

## Env vars

| Variavel | Descricao |
|---|---|
| `SECRET_KEY` | Chave secreta Flask para sessoes |
| `DATABASE_URL` | Caminho do arquivo SQLite (default: priceradar.db) |
| `SMTP_HOST` | Host do servidor SMTP |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Senha SMTP |
| `OPENAI_API_KEY` | Chave OpenAI (para extraccao futura com LLM) |

## Proximos passos

- Scheduler automatico (APScheduler ou cron) para snapshots diarios
- Extraccao de precos com LLM (OpenAI) alem do regex
- Webhook/Slack integration para alertas
- Playwright para paginas com JS rendering
- Dashboard com graficos de evolucao de preco
- Multi-user com planos (free/pro) e limites
- Deploy com Docker + Gunicorn
