# AI News Search Assistant (Neurotime Hackathon)

## 🤖 Live Demo — No Setup Required!

**Test the bot right now:** [@slnacessbankbot](https://t.me/slnacessbankbot)

Try these queries:
- `AccessBank haqqında xəbərlər`
- `Скажи про SOCAR что было 13 мая`
- `Banking regulation between May 12 and May 14`
- `Riskli iqtisadi xəbərlər`
- `/stats` `/keywords`

---

## Overview

An AI-powered news search assistant built for the **Neurotime Hackathon Task: AI News Search Assistant with Date-Aware Retrieval**.

The system searches through **~21,000 Azerbaijani news articles** (May 10–15, 2026) plus **daily enriched articles** using natural language. It supports Azerbaijani, Russian, and English queries, handles date ranges, and provides both a **Telegram Bot** and a **Web Dashboard**.

---

## 🌟 Key Features

| Feature | Description |
|---------|-------------|
| 🔍 **Natural Language Search** | Understands queries in Azerbaijani, Russian, and English |
| 📅 **Date-Aware Retrieval** | "on May 13", "between May 12 and May 14", "after May 11" |
| 🌐 **Multilingual Responses** | Detects query language and responds in the same language (az/ru/en) |
| 📋 **AI-Generated Summary** | GPT-4o synthesizes all results into a coherent paragraph |
| 🎯 **Relevance Scoring** | Results filtered to ≥80% similarity, sorted highest first |
| 😊 **Sentiment Analysis** | Every article classified as 🟢 Pozitiv / 🔵 Neytral / 🔴 Riskli |
| ✨ **Daily Enrichment** | GitHub Actions runs every day at 09:00 UTC — fetches fresh news via NewsAPI, analyzes with GPT-4o, adds to Supabase |
| 📊 **AI Topic Categories** | GPT-4o identifies meaningful topic clusters from results |
| 🤖 **Telegram Bot** | Full natural language interaction, paginated results |
| 🖥️ **Web Dashboard** | Dark UI with date digest, category filter, settings panel |

---

## Architecture

```
User Query (Telegram / Web)
        │
        ▼
  QueryParser (gpt-4.1-mini)
  ├── topic extraction
  ├── date range parsing
  └── language detection (az/ru/en)
        │
        ▼
  Supabase pgvector RPC: search_news()
  ├── SQL date filter first
  └── cosine similarity on filtered subset
        │
        ▼
  Filter ≥80% + Sort by score
        │
        ├──► SummaryGenerator (gpt-4o)
        │    └── Multilingual summary in detected language
        │
        └──► CategoryAnalyzer (gpt-4.1-mini)
             └── AI topic categories
```

**Why Supabase pgvector instead of FAISS:**
The `search_news()` RPC function applies SQL date filters first, then runs vector similarity only on the filtered subset — this is far more efficient than filtering post-search.

---

## Daily Enrichment Pipeline

Every day at **09:00 UTC**, a GitHub Actions cron job runs `scripts/keyword_enrichment.py`:

1. Loads active keywords from `enrichment_config` table in Supabase
2. Searches for fresh articles via **NewsAPI.org** (free tier, 100 req/day)
3. Passes each article to **GPT-4o** for:
   - Relevance check (skip irrelevant articles)
   - Sentiment classification (`pozitiv` / `neytral` / `riskli`)
   - Azerbaijani summary generation (`summary_az`)
4. Generates `text-embedding-3-small` embedding
5. Upserts to Supabase with `is_enriched = true`

Keywords are managed via the **⚙️ Settings** panel in the web dashboard.

---

## Sentiment Analysis

All 20,915 base articles have been processed with `gpt-4.1-mini` for sentiment classification. Every article in the database has a `sentiment` field:

- 🟢 **Pozitiv** — positive news (growth, achievement, improvement)
- 🔵 **Neytral** — neutral/informational
- 🔴 **Riskli** — negative/risky (crisis, accident, price increase)

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Vector Store | Supabase pgvector (`vector(1536)`) |
| Embeddings | OpenAI `text-embedding-3-small` |
| Query Parsing | OpenAI `gpt-4.1-mini` |
| Summary Generation | OpenAI `gpt-4o` |
| Sentiment Analysis | OpenAI `gpt-4.1-mini` |
| Telegram Bot | `python-telegram-bot` |
| Web Dashboard | Single-file HTML + Vanilla JS |
| API Backend | FastAPI + Uvicorn |
| Enrichment | NewsAPI.org + GitHub Actions |
| Deployment | Railway (bot + API) |

---

## Setup Instructions

### 1. Prerequisites

- Python 3.11+
- Docker (for docker compose)
- OpenAI API Key
- Telegram Bot Token (from @BotFather)
- Supabase project with `pgvector` extension
- NewsAPI.org key (free at [newsapi.org/register](https://newsapi.org/register))

### 2. Configure Environment

```bash
cp .env.example .env
# Fill in your credentials
```

Required variables:

```env
OPENAI_API_KEY=sk-proj-...
TELEGRAM_BOT_TOKEN=123456:ABC...
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGci...
NEWS_API_KEY=your_newsapi_key
```

### 3. Database Setup

> ✅ **Database is pre-loaded.** Our Supabase instance already contains all 20,915 articles with embeddings and sentiment analysis. You do NOT need to run `load_to_supabase.py` or have `news_data.xlsx`.

Simply fill in `.env` with your credentials and run the project.

<details>
<summary>Optional: Load your own Supabase instance from scratch</summary>

1. Run `supabase/schema.sql` in Supabase SQL Editor
2. Run `supabase/enrichment_schema.sql` for the keywords config table
3. Place `news_data.xlsx` in `data/` folder
4. Run `python scripts/load_to_supabase.py`
5. Run `python scripts/bulk_sentiment.py` to add sentiment to all articles

</details>

---

## Running the Project

### Option A: Docker Compose (Recommended)

```bash
docker compose up --build
```

This starts:
- **FastAPI backend + Web Dashboard** at `http://localhost:8000`
- **Telegram Bot** in the background

### Option B: Python (Without Docker)

```bash
pip install -r requirements.txt

# Terminal 1 — API + Web Dashboard
PYTHONPATH=backend uvicorn backend.app:app --host 0.0.0.0 --port 8000

# Terminal 2 — Telegram Bot
PYTHONPATH=backend python backend/bot.py
```

### Option C: Run Enrichment Manually

```bash
# Fetch fresh news for configured keywords
PYTHONPATH=backend python scripts/keyword_enrichment.py

# Run bulk sentiment analysis on all articles
PYTHONPATH=backend python scripts/bulk_sentiment.py
```

---

## GitHub Actions Secrets

To enable the daily enrichment pipeline, add these secrets in **Settings → Secrets → Actions**:

| Secret | Description |
|--------|-------------|
| `OPENAI_API_KEY` | OpenAI API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `NEWS_API_KEY` | NewsAPI.org key |

---

## Web Dashboard Features

- **Search bar** with example queries
- **Date filter** — select a date to get an AI-generated daily digest
- **Category filter** — 20 most common categories from the database
- **⚙️ Settings panel** — manage enrichment keywords, run enrichment manually
- **Results** with relevance score, sentiment badge, source, date
- **AI Topic Categories** sidebar — GPT-4o clusters results by topic
- **Sources** sidebar — breakdown by news outlet

---

## Known Limitations

- NewsAPI free tier returns results primarily in English/Russian; Azerbaijani-language articles may be limited
- The vector similarity scores are in the 0.30–0.50 range for this model/language combination; scores are rescaled to 80–100% for display
- Keyword extraction uses frequency analysis; a dedicated NER model would yield cleaner entities
