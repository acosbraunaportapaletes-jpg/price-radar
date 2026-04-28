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
- **Concorrentes** -- CRUD de concorrentes com URL da pagina de pricing e nome
- **Price Monitor** -- Job agendado (APScheduler, 1h) que faz scrape da pagina de pricing, extrai texto, compara hash com snapshot anterior; se mudou, cria alerta
- **Alertas** -- Quando snapshot muda, envia email via SMTP e mostra badge na dashboard com diff lado-a-lado (antes/depois)
- **Battle Card** -- Prompt pre-pronto enviado a OpenAI API que recebe diff de pricing e gera battle card resumida com talking points para vendas
- **htmx** -- Verificacao manual inline, delete inline, marcar alerta como lido sem reload
- **Diff visual** -- Comparacao lado a lado (HTML diff) e diff unificado texto

## Stack

Flask + SQLite + htmx + Tailwind CDN + BeautifulSoup + APScheduler + OpenAI API

## Env vars

| Variavel | Descricao |
|---|---|
| `SECRET_KEY` | Chave secreta Flask para sessoes |
| `DATABASE_URL` | Caminho do arquivo SQLite |
| `OPENAI_API_KEY` | Chave da API OpenAI para gerar battle cards |
| `SMTP_HOST` | Host do servidor SMTP |
| `SMTP_PORT` | Porta SMTP (default 587) |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Senha SMTP |
| `FROM_EMAIL` | Email remetente dos alertas |

## Proximos passos

- Seletor CSS para monitorar apenas a secao de precos
- Puppeteer/Playwright para paginas com JS rendering
- Webhook/Slack notifications
- Multi-user com planos (free/pro) e limites
- Deploy com Docker
