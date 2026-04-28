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
- **Concorrentes** — Cadastro com URL de pricing, nome e intervalo de verificacao configuravel
- **Scraper inteligente** — Extrai precos com BeautifulSoup (regex de precos) + fallback via OpenAI LLM para paginas complexas
- **Deteccao de mudancas** — Diff entre snapshots: detecta preco alterado, plano novo ou plano removido
- **Alertas por email** — Notificacao via SMTP quando mudanca e detectada, com sugestao de posicionamento
- **Dashboard** — Lista concorrentes monitorados + alertas recentes + verificacao manual via htmx
- **Job agendado** — APScheduler roda a cada 30 min, respeita intervalo configurado por concorrente
- **htmx** — Verificacao manual inline, delete inline, marcar alerta como lido sem reload

## Stack

Flask + SQLite + htmx + Tailwind CDN + BeautifulSoup + APScheduler

## Env vars

| Variavel | Descricao |
|---|---|
| `SECRET_KEY` | Chave secreta Flask para sessoes |
| `DATABASE_URL` | Caminho do arquivo SQLite (default: priceradar.db) |
| `OPENAI_API_KEY` | Chave da OpenAI para fallback LLM na extracao de precos |
| `SMTP_HOST` | Host do servidor SMTP |
| `SMTP_PORT` | Porta SMTP (default: 587) |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Senha SMTP |
| `FROM_EMAIL` | Email remetente |

## Proximos passos

- Webhook/Slack integration para alertas
- Dashboard com graficos de evolucao de preco
- Multi-user com planos (free/pro) e limites
- Seletor CSS customizado por concorrente
- Playwright para paginas com JS rendering
- Deploy com Docker + Gunicorn
