"""
keyword_enrichment.py
---------------------
Keyword-based news enrichment pipeline.

Instead of RSS feeds, this script:
  1. Takes a list of keywords (from Supabase `enrichment_config` table or env)
  2. Searches for fresh news articles via Google Custom Search API
  3. Fetches article content from each URL
  4. Sends to GPT-4o for relevance check + sentiment + Azerbaijani summary
  5. Generates embeddings and upserts to Supabase with is_enriched=True

Requirements:
  GOOGLE_API_KEY     — Google Cloud API key with Custom Search enabled
  GOOGLE_CSE_ID      — Custom Search Engine ID (cx parameter)
  OPENAI_API_KEY     — OpenAI API key
  SUPABASE_URL       — Supabase project URL
  SUPABASE_SERVICE_KEY — Supabase service role key

Can be run:
  - Manually: python scripts/keyword_enrichment.py
  - Via API:  POST /api/enrichment/run
  - Via GitHub Actions cron (see .github/workflows/daily_enrichment.yml)
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus

import requests
from openai import OpenAI
from supabase import create_client, Client

log = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-small"
GPT_MODEL = "gpt-4o"
MAX_RESULTS_PER_KEYWORD = 5   # Google CSE returns max 10 per query
MAX_CONTENT_CHARS = 2000

RELEVANCE_PROMPT = """You are an analyst for an Azerbaijani financial and economic news monitoring system.

Analyze this news article and return a JSON object with:
- "relevant": true if the article is related to banking, finance, economy, business, energy, companies, or politics; false otherwise
- "sentiment": one of "pozitiv", "neytral", "riskli" — where "riskli" means negative, risky or crisis-related
- "summary_az": a concise 1-2 sentence summary in Azerbaijani language (max 150 chars)

Article title: {title}
Article content: {content}

Return ONLY valid JSON, no explanation."""


def search_google(keyword: str, api_key: str, cse_id: str,
                  num: int = MAX_RESULTS_PER_KEYWORD) -> list[dict]:
    """Search Google Custom Search API for news about a keyword."""
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": keyword,
        "num": min(num, 10),
        "dateRestrict": "d1",   # last 24 hours
        "lr": "lang_az|lang_ru",  # Azerbaijani and Russian
        "sort": "date",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        log.info("Google CSE: %d results for '%s'", len(items), keyword)
        return [
            {
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "source": re.search(r"https?://([^/]+)", item.get("link", "")).group(1)
                          if item.get("link") else "",
            }
            for item in items
            if item.get("link")
        ]
    except Exception as exc:
        log.error("Google CSE error for '%s': %s", keyword, exc)
        return []


def fetch_article_content(url: str) -> str:
    """Fetch article text content from URL."""
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
        })
        resp.raise_for_status()
        # Simple text extraction — strip HTML tags
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:MAX_CONTENT_CHARS]
    except Exception:
        return ""


def analyze_article(oai: OpenAI, title: str, content: str) -> Optional[dict]:
    """GPT-4o relevance check + sentiment + Azerbaijani summary."""
    prompt = RELEVANCE_PROMPT.format(
        title=title,
        content=content[:MAX_CONTENT_CHARS],
    )
    for attempt in range(1, 4):
        try:
            resp = oai.chat.completions.create(
                model=GPT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as exc:
            log.warning("GPT attempt %d failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(3)
    return None


def embed_text(oai: OpenAI, title: str, content: str) -> Optional[list[float]]:
    """Generate embedding for title + content snippet."""
    text = f"{title}. {content[:600]}".strip()
    try:
        resp = oai.embeddings.create(model=EMBED_MODEL, input=[text])
        return resp.data[0].embedding
    except Exception as exc:
        log.error("Embedding error: %s", exc)
        return None


def get_existing_urls(sb: Client, urls: list[str]) -> set[str]:
    """Check which URLs already exist in Supabase."""
    if not urls:
        return set()
    try:
        resp = sb.table("articles").select("url").in_("url", urls).execute()
        return {row["url"] for row in (resp.data or [])}
    except Exception as exc:
        log.error("Dedup check error: %s", exc)
        return set()


def load_keywords_from_supabase(sb: Client) -> list[str]:
    """Load active keywords from enrichment_config table."""
    try:
        resp = (
            sb.table("enrichment_config")
            .select("keyword")
            .eq("active", True)
            .execute()
        )
        return [r["keyword"] for r in (resp.data or []) if r.get("keyword")]
    except Exception as exc:
        log.warning("Could not load keywords from DB: %s", exc)
        return []


def save_keywords_to_supabase(sb: Client, keywords: list[str]) -> None:
    """Save/update keywords in enrichment_config table."""
    try:
        # Deactivate all existing
        sb.table("enrichment_config").update({"active": False}).neq("id", 0).execute()
        # Insert new ones
        rows = [{"keyword": kw, "active": True} for kw in keywords if kw.strip()]
        if rows:
            sb.table("enrichment_config").upsert(rows, on_conflict="keyword").execute()
        log.info("Saved %d keywords to enrichment_config", len(rows))
    except Exception as exc:
        log.error("Could not save keywords: %s", exc)


def run_enrichment(
    keywords: list[str],
    oai: OpenAI,
    sb: Client,
    google_api_key: str,
    google_cse_id: str,
) -> dict:
    """
    Main enrichment function. Returns summary stats.
    """
    if not keywords:
        return {"enriched": 0, "skipped": 0, "errors": 0, "message": "No keywords provided"}

    log.info("Starting keyword enrichment for %d keywords: %s", len(keywords), keywords)

    all_articles: list[dict] = []

    # Step 1: Search Google for each keyword
    for kw in keywords:
        results = search_google(kw, google_api_key, google_cse_id)
        for r in results:
            r["keyword"] = kw
        all_articles.extend(results)
        time.sleep(0.3)  # rate limit

    log.info("Total articles found: %d", len(all_articles))

    if not all_articles:
        return {"enriched": 0, "skipped": 0, "errors": 0, "message": "No articles found from search"}

    # Step 2: Deduplicate
    urls = [a["url"] for a in all_articles]
    existing = get_existing_urls(sb, urls)
    new_articles = [a for a in all_articles if a["url"] not in existing]
    log.info("New articles after dedup: %d (skipped %d)", len(new_articles), len(all_articles) - len(new_articles))

    enriched = 0
    skipped = 0
    errors = 0

    # Step 3: Analyze, embed, upsert
    for article in new_articles:
        # Fetch full content
        content = fetch_article_content(article["url"]) or article.get("snippet", "")

        # GPT-4o analysis
        analysis = analyze_article(oai, article["title"], content)
        if not analysis:
            errors += 1
            continue

        if not analysis.get("relevant", False):
            skipped += 1
            log.info("  Not relevant: %s", article["title"][:60])
            continue

        # Generate embedding
        embedding = embed_text(oai, article["title"], content)
        if not embedding:
            errors += 1
            continue

        # Upsert to Supabase
        row = {
            "url": article["url"],
            "title": article["title"],
            "content": content[:5000],
            "source": article["source"],
            "category": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "embedding": embedding,
            "is_enriched": True,
            "sentiment": analysis.get("sentiment", "neytral"),
            "summary_az": analysis.get("summary_az", ""),
        }

        try:
            sb.table("articles").upsert(row, on_conflict="url").execute()
            enriched += 1
            log.info("  ✓ Enriched [%s]: %s", analysis.get("sentiment"), article["title"][:60])
        except Exception as exc:
            log.error("  Supabase upsert error: %s", exc)
            errors += 1

        time.sleep(0.3)

    log.info("Done: enriched=%d, skipped=%d, errors=%d", enriched, skipped, errors)
    return {
        "enriched": enriched,
        "skipped_irrelevant": skipped,
        "errors": errors,
        "total_found": len(all_articles),
        "total_new": len(new_articles),
        "message": f"Enriched {enriched} new articles from {len(keywords)} keywords",
    }


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    openai_key = os.getenv("OPENAI_API_KEY")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
    google_api_key = os.getenv("GOOGLE_API_KEY")
    google_cse_id = os.getenv("GOOGLE_CSE_ID")

    if not all([openai_key, supabase_url, supabase_key, google_api_key, google_cse_id]):
        raise EnvironmentError(
            "Required env vars: OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, "
            "GOOGLE_API_KEY, GOOGLE_CSE_ID"
        )

    oai = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1")
    sb = create_client(supabase_url, supabase_key)

    # Load keywords from DB, fallback to env var
    keywords = load_keywords_from_supabase(sb)
    if not keywords:
        kw_env = os.getenv("ENRICHMENT_KEYWORDS", "")
        keywords = [k.strip() for k in kw_env.split(",") if k.strip()]

    if not keywords:
        log.info("No keywords configured — pipeline healthy")
        return

    result = run_enrichment(keywords, oai, sb, google_api_key, google_cse_id)
    log.info("Result: %s", result)


if __name__ == "__main__":
    main()
