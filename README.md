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
- **Concorrentes** — CRUD completo: nome, URL da pagina de pricing, notas. Delete inline via htmx
- **Price Monitor** — Job agendado (thread) que faz scrape da pagina de pricing, extrai texto, calcula hash e detecta diff vs versao anterior
- **Alertas por email** — Notificacao via SMTP quando mudanca e detectada, com diff visual (antes/depois) destacado em cores
- **Timeline** — Historico de snapshots por concorrente com timeline visual de todas as mudancas detectadas
- **Diff visual** — Pagina dedicada mostrando o diff unificado com linhas adicionadas (verde) e removidas (vermelho)
- **Verificacao manual** — Botao "Verificar agora" inline via htmx, sem reload da pagina
- **htmx** — Delete inline, verificacao manual inline, feedback instantaneo

## Stack

Flask + SQLite + htmx + Tailwind CDN (zero dependencias externas alem de Flask)

## Env vars

| Variavel | Descricao |
|---|---|
| `SECRET_KEY` | Chave secreta Flask para sessoes |
| `DATABASE_URL` | Caminho do arquivo SQLite (default: priceradar.db) |
| `SMTP_HOST` | Host do servidor SMTP |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASS` | Senha SMTP |
| `CHECK_INTERVAL_HOURS` | Intervalo entre verificacoes automaticas em horas (default: 6) |
| `ENABLE_MONITOR` | 1 para ativar monitor em background, 0 para desativar |

## Proximos passos

- Webhook/Slack integration para alertas
- Seletor CSS customizado por concorrente para extrair precos especificos
- Playwright/Selenium para paginas com JS rendering
- Dashboard com graficos de evolucao de preco
- Multi-user com planos (free/pro) e limites
- Deploy com Docker + Gunicorn
