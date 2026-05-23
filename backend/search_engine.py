"""
search_engine.py
----------------
Core search logic backed by Supabase pgvector.

Flow:
  1. QueryParser      — LLM extracts topic + date range from natural language
  2. NewsSearchEngine.search() — embeds topic, calls Supabase RPC search_news(),
     returns results + AI-generated topic categories
  3. CategoryAnalyzer — GPT analyses returned articles and returns meaningful
     topic categories with counts (replaces naive keyword frequency)
"""

import json
import logging
import os
from typing import Optional

from openai import OpenAI
from supabase import create_client, Client

log = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-small"
PARSE_MODEL = "gpt-4.1-mini"
TOP_K = 15

# ── Query parse prompt ────────────────────────────────────────────────────────
QUERY_PARSE_PROMPT = """You are a query parser for a news search system.
Extract the search intent from the user's natural-language query.

Return a JSON object with these fields:
- "topic": the main search topic (string, in the original language of the query)
- "date_from": start date in YYYY-MM-DD format, or null
- "date_to": end date in YYYY-MM-DD format, or null
- "source": specific news source/domain if mentioned, or null
- "category": news category if mentioned, or null

The dataset covers May 10–15, 2026, plus daily enriched articles added after that.
If the user says "on May 13", set both date_from and date_to to "2026-05-13".
If "after DATE" → date_from = DATE, date_to = null.
If "before DATE" → date_from = null, date_to = DATE.

Examples:
Query: "Find news about AccessBank between May 12 and May 14"
→ {"topic": "AccessBank", "date_from": "2026-05-12", "date_to": "2026-05-14", "source": null, "category": null}

Query: "SOCAR haqqında xəbərlər"
→ {"topic": "SOCAR", "date_from": null, "date_to": null, "source": null, "category": null}

Query: "Show banking news on May 13"
→ {"topic": "banking", "date_from": "2026-05-13", "date_to": "2026-05-13", "source": null, "category": null}

Now parse this query:
"""

# ── Category analysis prompt ──────────────────────────────────────────────────
CATEGORY_PROMPT = """You are an analyst for an Azerbaijani news monitoring system.

Below are titles and snippets from {n} news articles returned for the query: "{query}"

Your task: identify the main TOPIC CATEGORIES present in these articles.
Return a JSON array of objects, each with:
- "category": short category name in the same language as the articles (Azerbaijani/Russian/English)
- "count": estimated number of articles belonging to this category
- "description": one short sentence describing what this category covers

Rules:
- Return 5–8 meaningful categories maximum
- Categories should be SPECIFIC and INFORMATIVE (e.g. "Kredit siyasəti", "Bank tənzimlənməsi", "SOCAR layihələri")
- Do NOT use generic categories like "Xəbərlər", "Məlumat", "Digər"
- Focus on: organizations, sectors, policy topics, events, economic themes
- Sort by count descending

Articles:
{articles}

Return ONLY valid JSON array, no explanation."""


class QueryParser:
    def __init__(self, client: OpenAI):
        self.client = client

    def parse(self, query: str) -> dict:
        try:
            resp = self.client.chat.completions.create(
                model=PARSE_MODEL,
                messages=[{"role": "user", "content": QUERY_PARSE_PROMPT + f'"{query}"'}],
                temperature=0,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            result = json.loads(resp.choices[0].message.content)
            log.info("Parsed query: %s", result)
            return result
        except Exception as exc:
            log.error("Query parse error: %s", exc)
            return {"topic": query, "date_from": None, "date_to": None,
                    "source": None, "category": None}


class CategoryAnalyzer:
    def __init__(self, client: OpenAI):
        self.client = client

    def analyze(self, results: list[dict], query: str) -> list[dict]:
        """Use GPT to identify meaningful topic categories from search results."""
        if not results:
            return []

        # Build compact article list for the prompt
        articles_text = "\n".join(
            f"{i+1}. {r.get('title', '')} — {r.get('snippet', '')[:120]}"
            for i, r in enumerate(results[:15])
        )

        prompt = CATEGORY_PROMPT.format(
            n=len(results),
            query=query,
            articles=articles_text,
        )

        try:
            resp = self.client.chat.completions.create(
                model=PARSE_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=600,
            )
            raw = resp.choices[0].message.content.strip()
            # Strip markdown code blocks if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            categories = json.loads(raw)
            log.info("Categories: %s", categories)
            return categories if isinstance(categories, list) else []
        except Exception as exc:
            log.error("Category analysis error: %s", exc)
            return []

    def global_categories(self, rows: list[dict]) -> list[dict]:
        """Analyse a sample of recent articles to get global topic overview."""
        if not rows:
            return []

        articles_text = "\n".join(
            f"{i+1}. {r.get('title', '')}"
            for i, r in enumerate(rows[:80])
        )

        prompt = CATEGORY_PROMPT.format(
            n=len(rows),
            query="ümumi xəbər lenti (general news feed)",
            articles=articles_text,
        )

        try:
            resp = self.client.chat.completions.create(
                model=PARSE_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=600,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            categories = json.loads(raw)
            return categories if isinstance(categories, list) else []
        except Exception as exc:
            log.error("Global category error: %s", exc)
            return []


class NewsSearchEngine:
    def __init__(self):
        openai_key = os.getenv("OPENAI_API_KEY")
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")

        if not openai_key:
            raise EnvironmentError("OPENAI_API_KEY is not set")
        if not supabase_url or not supabase_key:
            raise EnvironmentError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")

        self.oai = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1")
        self.sb: Client = create_client(supabase_url, supabase_key)
        self.parser = QueryParser(self.oai)
        self.categorizer = CategoryAnalyzer(self.oai)
        log.info("NewsSearchEngine ready (Supabase pgvector + AI categories)")

    def _embed(self, text: str) -> list[float]:
        resp = self.oai.embeddings.create(model=EMBED_MODEL, input=[text])
        return resp.data[0].embedding

    def search(self, query: str, top_k: int = TOP_K) -> dict:
        parsed = self.parser.parse(query)
        topic = parsed.get("topic") or query
        date_from = parsed.get("date_from")
        date_to = parsed.get("date_to")

        embedding = self._embed(topic)

        try:
            rpc_params: dict = {
                "query_embedding": embedding,
                "match_count": top_k,
            }
            if date_from:
                rpc_params["date_from"] = date_from
            if date_to:
                rpc_params["date_to"] = date_to

            resp = self.sb.rpc("search_news", rpc_params).execute()
            rows = resp.data or []
        except Exception as exc:
            log.error("Supabase RPC error: %s", exc, exc_info=True)
            raise

        results = []
        for row in rows:
            snippet = str(row.get("content", ""))[:300].replace("\n", " ").strip()
            results.append({
                "title": row.get("title", ""),
                "source": row.get("source", ""),
                "url": row.get("url", row.get("link", "")),
                "published_at": str(row.get("created_at", "")),
                "category": row.get("category", ""),
                "snippet": snippet,
                "score": round(float(row.get("similarity", 0)), 4),
                "is_enriched": bool(row.get("is_enriched", False)),
                "sentiment": row.get("sentiment", None),
                "summary_az": row.get("summary_az", None),
            })

        # AI-powered category analysis of results
        categories = self.categorizer.analyze(results, query)

        return {
            "results": results,
            "categories": categories,
            "parsed_query": parsed,
            "total_results": len(results),
        }

    def global_categories(self, top_n: int = 8) -> list[dict]:
        """Fetch recent article titles and get AI-generated topic overview."""
        try:
            resp = (
                self.sb.table("articles")
                .select("title")
                .order("created_at", desc=True)
                .limit(80)
                .execute()
            )
            rows = resp.data or []
        except Exception as exc:
            log.error("Global categories DB error: %s", exc)
            return []

        return self.categorizer.global_categories(rows)

    def get_stats(self) -> dict:
        try:
            total = self.sb.table("articles").select("id", count="exact").execute()
            enriched = (
                self.sb.table("articles")
                .select("id", count="exact")
                .eq("is_enriched", True)
                .execute()
            )
            return {
                "total_articles": total.count,
                "enriched_articles": enriched.count,
                "base_articles": (total.count or 0) - (enriched.count or 0),
            }
        except Exception as exc:
            log.error("Stats error: %s", exc)
            return {}
