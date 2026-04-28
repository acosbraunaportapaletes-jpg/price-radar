# PriceRadar

**Saiba quando seu concorrente muda o preco. Antes do seu prospect.**

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # edite com suas credenciais
python app.py
```

Acesse `http://localhost:5000`.

## Features implementadas

- **Auth** -- Cadastro e login com email+senha (hash Werkzeug), sessao via cookie seguro
- **Concorrentes** -- Cadastre URLs de pricing pages para monitoramento
- **Snapshots** -- Crawler captura texto da pricing page, gera hash e detecta diff vs versao anterior
- **Alertas por email** -- Notificacao automatica via SMTP quando mudanca e detectada, com diff
- **Dashboard** -- Timeline de mudancas por concorrente com contadores e marcacao lido/nao-lido via htmx
- **Historico** -- Timeline de snapshots por concorrente com diffs visuais lado a lado
- **Verificacao manual** -- Force um snapshot a qualquer momento com 1 clique
- **htmx** -- Delete inline, marcar alerta como lido sem reload

## Stack

Flask + SQLite + htmx + Tailwind CDN + BeautifulSoup

## Env vars

| Variavel | Descricao |
|---|---|
| `SECRET_KEY` | Chave secreta Flask para sessoes |
| `DATABASE_URL` | Caminho do arquivo SQLite |
| `SMTP_HOST` | Host do servidor SMTP |
| `SMTP_PORT` | Porta SMTP (default 587) |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Senha SMTP |
| `FROM_EMAIL` | Email remetente dos alertas |

## Proximos passos

- Scheduler automatico (cron ou APScheduler) para checks periodicos
- Webhook/Slack notifications
- Seletor CSS para monitorar apenas a secao de precos
- Puppeteer/Playwright para paginas com JS rendering
- Multi-user com planos (free/pro) e limites
- Deploy com Docker
