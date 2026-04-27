# PriceRadar

**Saiba quando seu concorrente muda os precos. Antes do seu prospect.**

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # edite com suas credenciais
python app.py
```

Acesse http://localhost:5000

## Features implementadas

- Cadastro e login com hash seguro (werkzeug), sessao via cookie
- CRUD de concorrentes com URL de pricing (max 5 no plano free)
- Scraper agendado (APScheduler 24h) que captura snapshot HTML+texto e armazena hash
- Deteccao de mudanca por comparacao de hash + diff visual (antes/depois)
- Alertas por email (SMTP) com resumo das mudancas detectadas
- Dashboard com metricas, status por concorrente e alertas recentes
- Timeline de snapshots por concorrente com historico completo
- Filtro de alertas (todos / lidos / nao lidos)
- Interface responsiva com Tailwind CSS + htmx

## Stack

- Python 3 + Flask
- SQLite
- htmx + Tailwind CSS (CDN)
- APScheduler (jobs em background)
- BeautifulSoup4 (scraping)

## Proximos passos

- Webhook para Slack/Discord como canal de alerta
- Seletores CSS customizaveis para extrair precos especificos
- Graficos de variacao de preco ao longo do tempo
- Plano pro com pagamento via Stripe
- Deploy com Docker + Gunicorn
- Testes automatizados
