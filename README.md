# AI News Search Assistant (Neurotime Hackathon)

## 🤖 Live Demo — No Setup Required!

**Test the bot right now:** [@slnacessbankbot](https://t.me/slnacessbankbot)

Try these queries:
- `AccessBank haqqında xəbərlər`
- `Find news about SOCAR on May 13`
- `Banking regulation between May 12 and May 14`
- `/stats` `/keywords`

---

This repository contains the solution for the **Neurotime Hackathon Task: AI News Search Assistant with Date-Aware Retrieval**.

## Overview
The system allows users to search through ~21,000 base news articles (May 10–15, 2026) plus **daily enriched articles** using natural language. It handles date ranges, topic extraction, and keyword analysis, and provides a **Telegram Bot** and a **Web Dashboard**.

### 🌟 Key Differentiator: Daily Data Enrichment (Python + GitHub Actions + GPT-4o)
Our unique feature is the **Daily Data Enrichment Pipeline**. Every day at 09:00 UTC, a GitHub Actions cron job runs `scripts/rss_enrichment.py`:
1. Pulls fresh news from major Azerbaijani RSS feeds (Oxu.az, Trend.az, 1news.az, etc.).
2. Deduplicates against existing articles in Supabase.
3. Passes new articles to **GPT-4o** for relevance check, sentiment analysis (`pozitiv`, `neytral`, `riskli`), and Azerbaijani summarization.
4. Generates embeddings and upserts the enriched articles into Supabase with an `is_enriched = true` flag.
5. Saves the daily digest to Supabase (for the web dashboard) and sends a digest email via Microsoft Outlook OAuth.

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
- **Daily Enrichment:** GitHub Actions pipeline adds fresh articles every morning with GPT-4o sentiment + Azerbaijani summaries.
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

### 3. Database Setup

> ✅ **Database is pre-loaded.** Our Supabase instance already contains all 20,915 articles with embeddings. You do NOT need to run `load_to_supabase.py` or have `news_data.xlsx`.

Simply fill in `.env` with your credentials and run the project.

<details>
<summary>Optional: Load your own Supabase instance from scratch</summary>

1. Run `supabase/schema.sql` in Supabase SQL Editor
2. Place `news_data.xlsx` in `data/` folder
3. Run `python scripts/load_to_supabase.py`

</details>

---

## Running the Project (Local Evaluation)

We have provided a `docker-compose.yml` to make it incredibly easy for judges to run the entire stack locally with a single command.

### 1. Configure Environment
Create a `.env` file in the root directory (you can copy `.env.example`) and fill in the required keys:
```env
OPENAI_API_KEY=sk-proj-...
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1...
```

### 2. Run via Docker Compose
Run the following command in the project root:
```bash
docker compose up --build
```
This single command will:
1. Start the **FastAPI Backend** on port 8000.
2. Serve the **Web Dashboard** at `http://localhost:8000`.
3. Start the **Telegram Bot** in the background.

### 3. Test the Solution
- **Web Dashboard:** Open [http://localhost:8000](http://localhost:8000) in your browser.
- **Telegram Bot:** Send a message to your configured bot (e.g., `Find news about AccessBank`, `/stats`, `/keywords`).

### Alternative: Run via Python (Without Docker)
If you prefer not to use Docker, you can run the services manually:
```bash
pip install -r requirements.txt

# Start the API and Web Dashboard (Terminal 1)
PYTHONPATH=backend uvicorn backend.app:app --host 0.0.0.0 --port 8000

# Start the Telegram Bot (Terminal 2)
PYTHONPATH=backend python backend/bot.py
```

---

## Known Limitations
- The vector search relies on the `text-embedding-3-small` model, which handles Azerbaijani and Russian well, but might miss some highly specific local slang.
- Keyword extraction uses a frequency-based approach with a predefined stopword list. For production, a dedicated NER (Named Entity Recognition) model would yield cleaner entities.
