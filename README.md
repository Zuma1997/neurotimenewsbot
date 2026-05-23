# AI News Search Assistant (Neurotime Hackathon)

This repository contains the solution for the **Neurotime Hackathon Task: AI News Search Assistant with Date-Aware Retrieval**.

## Overview
The system allows users to search through ~21,000 base news articles (May 10–15, 2026) plus **daily enriched articles** using natural language. It handles date ranges, topic extraction, and keyword analysis, and provides a **Telegram Bot** and a **Web Dashboard**.

### 🌟 Key Differentiator: Daily Data Enrichment (n8n + GPT-4o)
Our unique feature is the **Daily Data Enrichment Pipeline**. Every day at 09:00, an n8n workflow:
1. Pulls fresh news from Google Alerts RSS feeds (keywords: bank, maliyyə, iqtisadiyyat, SOCAR, etc.).
2. Passes articles to **GPT-4o** for relevance check, sentiment analysis (`pozitiv`, `neytral`, `riskli`), and Azerbaijani summarization.
3. Upserts the enriched articles into Supabase with an `is_enriched = true` flag.
4. Sends a daily digest email via Microsoft Outlook OAuth.
*(See `docs/n8n_enrichment_pipeline.md` for details).*

### Architecture & Approach
To comply with the cost constraints (under $10) and ensure high performance, we use a **Supabase pgvector hybrid search architecture**:
1. **Query Parsing (LLM):** User's natural language query is parsed by `gpt-4.1-mini` to extract the `topic`, `date_from`, `date_to`, `source`, and `category`.
2. **Vector + Date Search (Supabase RPC):** The parsed topic is embedded using `text-embedding-3-small`. We call a custom Supabase RPC function (`search_news`) that first applies SQL date filters, then performs cosine similarity vector search on the filtered subset.
3. **Keyword Extraction (Frequency):** Extracts the most common entities/keywords from the returned results, filtering out stopwords.

### Features
- **Natural Language Search:** e.g., *"Find news about AccessBank between May 12 and May 14"*
- **Date-Aware Retrieval:** Understands "on May 13", "after May 12", etc.
- **Telegram Bot:** Interactive, paginated results, `/keywords`, `/stats` commands, enriched-article badges and sentiment indicators.
- **Web Dashboard (Bonus):** React frontend with FastAPI backend, relevance score badges, and keyword clouds.
- **Daily Enrichment:** n8n pipeline adds fresh articles every morning with GPT-4o sentiment + Azerbaijani summaries.
- **Cost-Efficient:** One-time embedding cost ~$0.50. Each search costs ~$0.0001 (only query parsing).

---

## Setup Instructions

### 1. Prerequisites
- Python 3.11+
- Node.js (for the web frontend)
- OpenAI API Key
- Telegram Bot Token (from @BotFather)
- Supabase project with `pgvector` extension enabled

### 2. Installation

Clone the repository and install backend dependencies:
```bash
pip install -r requirements.txt
```

Set up your environment variables by copying the example file:
```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY and TELEGRAM_BOT_TOKEN
```

Place the provided dataset (`news_data.xlsx`) into the `data/` directory.

### 3. Database Setup & Initial Load
1. Run the SQL schema in your Supabase SQL Editor: `supabase/schema.sql`
2. Load the base dataset (20k articles) into Supabase:
```bash
python scripts/load_to_supabase.py
```
*This script generates embeddings and upserts rows. It takes a few minutes and costs ~$0.50-$1.00 in OpenAI credits.*

---

## Running the Project

### Option A: Telegram Bot
To start the Telegram assistant:
```bash
python backend/bot.py
```
Then message your bot on Telegram and try queries like:
- *"SOCAR haqqında xəbərlər"*
- *"Banking regulation news between May 12 and May 14"*
- `/keywords`

### Option B: Web Interface
To run the full web application (FastAPI backend + React frontend):

1. Build the React frontend:
```bash
cd frontend
npm install
npm run build
cd ..
```

2. Start the FastAPI server:
```bash
pip install fastapi uvicorn
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

3. Open your browser at `http://localhost:8000`

---

## Known Limitations
- The vector search relies on the `text-embedding-3-small` model, which handles Azerbaijani and Russian well, but might miss some highly specific local slang.
- Keyword extraction uses a frequency-based approach with a predefined stopword list. For production, a dedicated NER (Named Entity Recognition) model would yield cleaner entities.
