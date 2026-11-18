# FinTrack AI – AI-Enhanced Personal Finance Tracker

Full-stack personal finance app: track income, expenses and budgets on an interactive dashboard, with local AI insights from **Ollama + Gemma 2**.

## Features
- Add / edit / delete transactions (income and expenses)
- Auto-categorization: keyword rules first, Gemma 2 as a fallback for unfamiliar descriptions
- Monthly analytics: income, expenses, balance, savings rate, change vs last month
- Charts: spending by category (donut) and 6-month income vs expense trend
- Budgets per category with progress bars (green / amber at 80% / red when over)
- AI insights and free-form questions ("Where can I cut spending?"), run fully locally
- Graceful fallback to rule-based insights when Ollama is not running

## Quick start
```bash
python server.py --seed      # --seed adds demo data on first run
# open http://localhost:8000
```
Requires Python 3.8+. No packages to install (standard library only).

## Enable the AI (optional)
```bash
# install Ollama from https://ollama.com, then:
ollama pull gemma2
ollama serve          # skip if it already runs as a service
```
The header pill shows AI status. Config via env vars: `OLLAMA_URL` (default `http://localhost:11434`), `OLLAMA_MODEL` (default `gemma2`), `PORT`, `DB_PATH`.

## Project layout
```
server.py          REST API, SQLite, analytics, Ollama client
static/index.html  Dashboard (vanilla JS, canvas charts)
finance.db         Created on first run
```

## REST API
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/transactions?month=YYYY-MM&type=&category=` | List transactions |
| POST | `/api/transactions` | Create `{type, amount, category?, description, date}` |
| PUT / DELETE | `/api/transactions/<id>` | Update / delete |
| GET | `/api/budgets` | List budgets |
| PUT / DELETE | `/api/budgets` | Upsert `{category, monthly_limit}` / remove `{category}` |
| GET | `/api/summary?month=YYYY-MM` | Totals, category breakdown, budget status, 6-month trend |
| POST | `/api/ai/categorize` | `{description, type}` → suggested category |
| POST | `/api/ai/insights` | `{month, question?}` → AI (or fallback) insights |
| GET | `/api/ai/status` | Ollama availability |

## How the AI is used
The server aggregates your month into a compact JSON summary and sends only that to the model, with instructions to use only the supplied numbers. Raw transactions never leave your machine, and nothing goes to any cloud service.

## Ideas to extend
- CSV/bank statement import, recurring transactions
- Savings goals, anomaly detection (z-score on category spend)
- Swap SQLite for PostgreSQL, add auth for multi-user
- Port the backend to FastAPI/Flask or Node and the frontend to React
