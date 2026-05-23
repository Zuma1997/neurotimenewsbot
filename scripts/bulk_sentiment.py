"""
bulk_sentiment.py
-----------------
Batch sentiment analysis for all articles in Supabase that don't have sentiment yet.
Uses gpt-4.1-mini (cheapest model) for cost efficiency.

Estimated cost: ~$0.50-1.00 for 20,915 articles
Estimated time: ~1-2 hours

Run:
    python scripts/bulk_sentiment.py
    python scripts/bulk_sentiment.py --batch-size 100 --start-offset 5000  # resume
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

from openai import OpenAI
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

MODEL = "gpt-4.1-mini"
BATCH_SIZE = 20       # articles per API call (batching saves tokens)
PROGRESS_FILE = Path("/tmp/bulk_sentiment_progress.json")

BATCH_SENTIMENT_PROMPT = """You are a sentiment classifier for Azerbaijani news articles.

For each article below, classify the sentiment as exactly one of:
- "pozitiv" — positive news (growth, success, achievement, improvement)
- "neytral" — neutral/informational news
- "riskli"  — negative/risky news (crisis, conflict, accident, price increase, problem)

Return a JSON array with one object per article in the same order:
[{{"id": <id>, "sentiment": "<pozitiv|neytral|riskli>"}}, ...]

Articles:
{articles}

Return ONLY the JSON array, no explanation."""


def load_articles_without_sentiment(sb: Client, limit: int = 500, offset: int = 0) -> list[dict]:
    """Fetch articles that don't have sentiment yet."""
    try:
        resp = (
            sb.table("articles")
            .select("id, title, content")
            .is_("sentiment", "null")
            .order("id")
            .range(offset, offset + limit - 1)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.error("DB fetch error: %s", exc)
        return []


def count_without_sentiment(sb: Client) -> int:
    try:
        resp = (
            sb.table("articles")
            .select("id", count="exact")
            .is_("sentiment", "null")
            .execute()
        )
        return resp.count or 0
    except Exception:
        return 0


def analyze_batch(oai: OpenAI, articles: list[dict]) -> list[dict]:
    """Send a batch of articles to GPT for sentiment classification."""
    articles_text = "\n\n".join(
        f"ID {a['id']}: {a.get('title', '')} — {str(a.get('content', ''))[:200]}"
        for a in articles
    )

    prompt = BATCH_SENTIMENT_PROMPT.format(articles=articles_text)

    for attempt in range(1, 4):
        try:
            resp = oai.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=len(articles) * 30 + 50,
            )
            raw = resp.choices[0].message.content.strip()
            # Strip markdown code blocks if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            # Try common wrapper keys
            for key in ["results", "articles", "sentiments", "data"]:
                if key in data and isinstance(data[key], list):
                    return data[key]
            return []
        except Exception as exc:
            log.warning("Batch attempt %d failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(5)
    return []


def update_sentiments(sb: Client, results: list[dict]) -> int:
    """Update sentiment field for a list of {id, sentiment} dicts."""
    updated = 0
    for r in results:
        try:
            sentiment = r.get("sentiment", "neytral")
            if sentiment not in ("pozitiv", "neytral", "riskli"):
                sentiment = "neytral"
            sb.table("articles").update({"sentiment": sentiment}).eq("id", r["id"]).execute()
            updated += 1
        except Exception as exc:
            log.error("Update error for id=%s: %s", r.get("id"), exc)
    return updated


def save_progress(processed: int, total: int) -> None:
    PROGRESS_FILE.write_text(json.dumps({"processed": processed, "total": total}))


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            pass
    return {"processed": 0, "total": 0}


def main(batch_size: int = BATCH_SIZE, start_offset: int = 0) -> None:
    openai_key = os.getenv("OPENAI_API_KEY")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not all([openai_key, supabase_url, supabase_key]):
        raise EnvironmentError("Missing: OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY")

    oai = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1")
    sb = create_client(supabase_url, supabase_key)

    total = count_without_sentiment(sb)
    log.info("Articles without sentiment: %d", total)

    if total == 0:
        log.info("All articles already have sentiment! Nothing to do.")
        return

    processed = start_offset
    errors = 0
    page_size = 500  # fetch from DB in pages

    log.info("Starting bulk sentiment analysis (model=%s, batch=%d)...", MODEL, batch_size)

    while True:
        # Fetch next page of articles without sentiment
        articles = load_articles_without_sentiment(sb, limit=page_size, offset=0)
        # Note: we always fetch from offset=0 because we update as we go

        if not articles:
            log.info("No more articles to process!")
            break

        # Process in batches
        for i in range(0, len(articles), batch_size):
            batch = articles[i: i + batch_size]
            results = analyze_batch(oai, batch)

            if results:
                updated = update_sentiments(sb, results)
                processed += updated
                log.info(
                    "Progress: %d / %d (%.1f%%) | batch=%d updated",
                    processed, processed + total - processed, 100 * processed / max(total, 1),
                    updated,
                )
            else:
                errors += len(batch)
                log.warning("Batch failed, skipping %d articles", len(batch))

            save_progress(processed, total)
            time.sleep(0.3)  # rate limit

        # Check remaining
        remaining = count_without_sentiment(sb)
        log.info("Remaining without sentiment: %d", remaining)
        if remaining == 0:
            break

    log.info("=== DONE ===")
    log.info("Processed: %d | Errors: %d", processed, errors)
    PROGRESS_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--start-offset", type=int, default=0)
    args = parser.parse_args()
    main(batch_size=args.batch_size, start_offset=args.start_offset)
