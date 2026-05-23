"""
rss_enrichment.py
-----------------
Daily enrichment pipeline for the AI News Search Assistant.

Steps:
  1. Fetch RSS feeds from configured Azerbaijani news sources
  2. Deduplicate against existing Supabase records
  3. For each new article: GPT-4o relevance check + sentiment + Azerbaijani summary
  4. Generate text-embedding-3-small embedding for relevant articles
  5. Upsert to Supabase with is_enriched=True, sentiment, summary_az
  6. Build daily digest and send via Microsoft Outlook OAuth

Run manually:
    python scripts/rss_enrichment.py

Run via GitHub Actions: see .github/workflows/daily_enrichment.yml
"""

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import requests
from openai import OpenAI
from supabase import create_client, Client

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://az.trend.az/rss/azerbaijan.rss",
    "https://az.trend.az/rss/business.rss",
    "https://az.trend.az/rss/economy.rss",
    "https://www.azernews.az/rss/Azerbaijan.xml",
    "https://www.azernews.az/rss/Business.xml",
    "https://report.az/rss.xml",
    "https://banker.az/feed/",
    "https://abb-bank.az/rss",
]

EMBED_MODEL = "text-embedding-3-small"
GPT_MODEL = "gpt-4o"
MAX_CONTENT_CHARS = 1500   # chars sent to GPT for analysis
REQUEST_TIMEOUT = 15       # seconds for HTTP requests

RELEVANCE_PROMPT = """You are an analyst for an Azerbaijani financial and economic news monitoring system.

Analyze this news article and return a JSON object with:
- "relevant": true if the article is related to banking, finance, economy, business, energy, or major companies (SOCAR, AccessBank, etc.); false otherwise
- "sentiment": one of "pozitiv", "neytral", "riskli" — where "riskli" means negative, risky or crisis-related
- "summary_az": a concise 1-2 sentence summary in Azerbaijani language (max 150 chars)

Article title: {title}
Article content: {content}

Return ONLY valid JSON, no explanation."""


# ── RSS Fetching ──────────────────────────────────────────────────────────────
def fetch_rss(url: str) -> list[dict]:
    """Fetch and parse an RSS feed. Returns list of article dicts."""
    articles = []
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 NewsBot/1.0"
        })
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        # Handle both RSS 2.0 and Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)

        for item in items:
            def get(tag: str, default: str = "") -> str:
                el = item.find(tag) or item.find(f"atom:{tag}", ns)
                return (el.text or "").strip() if el is not None else default

            link = get("link") or get("guid")
            # For Atom feeds, link might be in href attribute
            link_el = item.find("link")
            if link_el is not None and not link_el.text:
                link = link_el.get("href", "")

            title = get("title")
            description = get("description") or get("summary") or get("content")
            pub_date = get("pubDate") or get("published") or get("updated")

            if link and title:
                articles.append({
                    "url": link.strip(),
                    "title": title,
                    "content": re.sub(r"<[^>]+>", "", description),  # strip HTML
                    "pub_date": pub_date,
                    "source": re.search(r"https?://([^/]+)", url).group(1) if re.search(r"https?://([^/]+)", url) else url,
                })

        log.info("Fetched %d articles from %s", len(articles), url)
    except Exception as exc:
        log.error("Failed to fetch RSS %s: %s", url, exc)
    return articles


# ── Deduplication ─────────────────────────────────────────────────────────────
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


# ── GPT-4o Analysis ───────────────────────────────────────────────────────────
def analyze_article(oai: OpenAI, title: str, content: str) -> Optional[dict]:
    """Send article to GPT-4o for relevance + sentiment + summary."""
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
            result = json.loads(resp.choices[0].message.content)
            return result
        except Exception as exc:
            log.warning("GPT analysis attempt %d failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(3)
    return None


# ── Embedding ─────────────────────────────────────────────────────────────────
def embed_text(oai: OpenAI, title: str, content: str) -> Optional[list[float]]:
    """Generate embedding for title + content snippet."""
    text = f"{title}. {content[:600]}".strip()
    try:
        resp = oai.embeddings.create(model=EMBED_MODEL, input=[text])
        return resp.data[0].embedding
    except Exception as exc:
        log.error("Embedding error: %s", exc)
        return None


# ── Parse pub date ────────────────────────────────────────────────────────────
def parse_date(date_str: str) -> Optional[str]:
    """Try to parse RSS date string to ISO format."""
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt).isoformat()
        except ValueError:
            continue
    return datetime.now(timezone.utc).isoformat()


# ── Outlook Email Digest ──────────────────────────────────────────────────────
def send_digest_email(articles: list[dict]) -> bool:
    """Send daily digest via Microsoft Outlook OAuth (Graph API)."""
    client_id = os.getenv("OUTLOOK_CLIENT_ID")
    client_secret = os.getenv("OUTLOOK_CLIENT_SECRET")
    tenant_id = os.getenv("OUTLOOK_TENANT_ID")
    email_to = os.getenv("DIGEST_EMAIL_TO")

    if not all([client_id, client_secret, tenant_id, email_to]):
        log.warning("Outlook credentials not set — skipping email digest")
        return False

    # Get access token
    try:
        token_resp = requests.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=15,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]
    except Exception as exc:
        log.error("Failed to get Outlook token: %s", exc)
        return False

    # Build HTML email body
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    rows = ""
    for a in articles[:20]:
        sentiment_color = {"pozitiv": "#22c55e", "neytral": "#3b82f6", "riskli": "#ef4444"}.get(
            a.get("sentiment", "neytral"), "#3b82f6"
        )
        rows += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #e2e8f0;">
            <a href="{a['url']}" style="font-weight:600;color:#1e40af;text-decoration:none;">{a['title']}</a><br>
            <span style="font-size:12px;color:#64748b;">{a['source']} · {a.get('published_at','')[:10]}</span>
            <span style="margin-left:8px;padding:2px 8px;border-radius:4px;font-size:11px;background:{sentiment_color};color:#fff;">{a.get('sentiment','neytral')}</span><br>
            <span style="font-size:13px;color:#374151;">{a.get('summary_az','')}</span>
          </td>
        </tr>"""

    html_body = f"""
    <html><body style="font-family:sans-serif;max-width:700px;margin:0 auto;">
      <h2 style="color:#1e3a5f;">📰 Daily News Digest — {today}</h2>
      <p style="color:#64748b;">{len(articles)} new relevant articles enriched today.</p>
      <table style="width:100%;border-collapse:collapse;">{rows}</table>
      <p style="color:#94a3b8;font-size:12px;margin-top:20px;">
        Powered by AI News Search Assistant · Neurotime Hackathon
      </p>
    </body></html>"""

    # Send email via Graph API
    try:
        sender = email_to  # send as the same address
        mail_payload = {
            "message": {
                "subject": f"📰 Daily News Digest — {today} ({len(articles)} articles)",
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": email_to}}],
            }
        }
        send_resp = requests.post(
            f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=mail_payload,
            timeout=15,
        )
        send_resp.raise_for_status()
        log.info("Digest email sent to %s", email_to)
        return True
    except Exception as exc:
        log.error("Failed to send digest email: %s", exc)
        return False


# ── Save digest to Supabase for web display ───────────────────────────────────
def save_digest_to_supabase(sb: Client, articles: list[dict]) -> None:
    """Save today's digest summary to a digests table for the web dashboard."""
    if not articles:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = {
        "date": today,
        "article_count": len(articles),
        "articles": json.dumps([
            {
                "title": a["title"],
                "url": a["url"],
                "source": a["source"],
                "sentiment": a.get("sentiment"),
                "summary_az": a.get("summary_az"),
            }
            for a in articles[:20]
        ], ensure_ascii=False),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table("digests").upsert(digest, on_conflict="date").execute()
        log.info("Digest saved to Supabase (date=%s, articles=%d)", today, len(articles))
    except Exception as exc:
        log.warning("Could not save digest to Supabase (table may not exist): %s", exc)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    openai_key = os.getenv("OPENAI_API_KEY")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not all([openai_key, supabase_url, supabase_key]):
        raise EnvironmentError("Missing required env vars: OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY")

    oai = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1")
    sb: Client = create_client(supabase_url, supabase_key)

    log.info("=== Daily Enrichment Pipeline started ===")

    # Step 1: Fetch all RSS feeds
    all_articles: list[dict] = []
    for feed_url in RSS_FEEDS:
        all_articles.extend(fetch_rss(feed_url))

    log.info("Total articles fetched from RSS: %d", len(all_articles))

    if not all_articles:
        log.info("No new articles today — pipeline healthy")
        return

    # Step 2: Deduplicate against Supabase
    urls = [a["url"] for a in all_articles]
    existing_urls = get_existing_urls(sb, urls)
    new_articles = [a for a in all_articles if a["url"] not in existing_urls]
    log.info("New articles after deduplication: %d (skipped %d duplicates)",
             len(new_articles), len(all_articles) - len(new_articles))

    if not new_articles:
        log.info("No new articles today — pipeline healthy")
        return

    # Step 3-5: Analyze, embed, upsert
    enriched: list[dict] = []
    skipped_irrelevant = 0

    for i, article in enumerate(new_articles):
        log.info("[%d/%d] Processing: %s", i + 1, len(new_articles), article["title"][:80])

        # GPT-4o analysis
        analysis = analyze_article(oai, article["title"], article["content"])
        if not analysis:
            log.warning("  → GPT analysis failed, skipping")
            continue

        if not analysis.get("relevant", False):
            log.info("  → Not relevant, skipping")
            skipped_irrelevant += 1
            continue

        sentiment = analysis.get("sentiment", "neytral")
        summary_az = analysis.get("summary_az", "")
        log.info("  → Relevant | sentiment=%s | summary=%s", sentiment, summary_az[:60])

        # Generate embedding
        embedding = embed_text(oai, article["title"], article["content"])
        if not embedding:
            log.warning("  → Embedding failed, skipping")
            continue

        # Build Supabase row
        row = {
            "url": article["url"],
            "title": article["title"],
            "content": article["content"][:5000],
            "source": article["source"],
            "category": "",
            "created_at": parse_date(article.get("pub_date", "")),
            "embedding": embedding,
            "is_enriched": True,
            "sentiment": sentiment,
            "summary_az": summary_az,
        }

        # Upsert to Supabase
        try:
            sb.table("articles").upsert(row, on_conflict="url").execute()
            enriched.append({**article, "sentiment": sentiment, "summary_az": summary_az})
            log.info("  → Upserted to Supabase ✓")
        except Exception as exc:
            log.error("  → Supabase upsert failed: %s", exc)

        # Small delay to avoid rate limits
        time.sleep(0.5)

    log.info("=== Pipeline complete ===")
    log.info("  Enriched: %d | Skipped (irrelevant): %d | Skipped (errors): %d",
             len(enriched), skipped_irrelevant, len(new_articles) - len(enriched) - skipped_irrelevant)

    # Step 6: Save digest to Supabase for web display
    if enriched:
        save_digest_to_supabase(sb, enriched)

    log.info("Pipeline finished. No email digest configured.")
    log.info("View enriched articles in the Telegram bot or web dashboard.")


if __name__ == "__main__":
    main()
