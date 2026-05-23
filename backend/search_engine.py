"""
search_engine.py
----------------
Core search logic backed by Supabase pgvector.

Flow:
  1. QueryParser      — LLM extracts topic + date range from natural language
  2. NewsSearchEngine.search() — embeds topic, calls Supabase RPC search_news(),
     filters results to 80%+ similarity, sorts highest-first
  3. SummaryGenerator — GPT-4o generates a single coherent Azerbaijani summary
     of all high-relevance results
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
SUMMARY_MODEL = "gpt-4o"
TOP_K = 20            # fetch more from DB, then filter by score
MIN_SCORE = 0.30      # minimum raw cosine similarity (pgvector scores are 0.3-0.5 range)
DISPLAY_SCORE_MIN = 0.80   # minimum displayed % (rescaled for UX)

# ── Query parse prompt ────────────────────────────────────────────────────────
QUERY_PARSE_PROMPT = """You are a query parser for a news search system.
Extract the search intent from the user's natural-language query.

Return a JSON object with these fields:
- "topic": the main search topic (string, in the original language of the query)
- "date_from": start date in YYYY-MM-DD format, or null
- "date_to": end date in YYYY-MM-DD format, or null
- "source": specific news source/domain if mentioned, or null
- "category": news category if mentioned, or null
- "language": detected language of the query — exactly one of: "az" (Azerbaijani), "ru" (Russian), "en" (English)

The dataset covers May 10–15, 2026, plus daily enriched articles added after that.
If the user says "on May 13", set both date_from and date_to to "2026-05-13".
If "after DATE" → date_from = DATE, date_to = null.
If "before DATE" → date_from = null, date_to = DATE.

Examples:
Query: "Find news about AccessBank between May 12 and May 14"
→ {"topic": "AccessBank", "date_from": "2026-05-12", "date_to": "2026-05-14", "source": null, "category": null, "language": "en"}

Query: "SOCAR haqqında xəbərlər"
→ {"topic": "SOCAR", "date_from": null, "date_to": null, "source": null, "category": null, "language": "az"}

Query: "Скажи про аксес банк что было 14ого мая"
→ {"topic": "аксес банк", "date_from": "2026-05-14", "date_to": "2026-05-14", "source": null, "category": null, "language": "ru"}

Now parse this query:
"""

# ── Summary generation prompt ─────────────────────────────────────────────────
SUMMARY_PROMPTS = {
    "az": """Sən Azərbaycan xəbər monitorinq sisteminin baş analitikisən.

İstifadəçi "{query}" sorğusunu göndərdi.
Tarix konteksti: {date_context}

Aşağıda əlaqəlilik səviyyəsinə görə sıralanmış {n} məqalə var:

{articles}

Vəzifən: Bu məqalələrin əsas məlumatlarını əhatə edən 3-5 cümləlik xülasə yaz. Azərbaycan dilində yaz. Konkret faktlar, rəqəmlər, adlar qeyd et. "Bu xəbərlərdə..." kimi adi ifadələrlə başlama.

Yalnız xülasə mətnini qaytar, izahat yox.""",

    "ru": """Tы — старший аналитик азербайджанской системы мониторинга новостей.

Пользователь ищет: "{query}"
Контекст дат: {date_context}

Ниже {n} релевантных статей, отсортированных по релевантности:

{articles}

Задача: Напиши краткое резюме на РУССКОМ языке (3-5 предложений). Упоминай конкретные факты, цифры, названия. Не начинай с шаблонных фраз.

Верни только текст резюме, без пояснений.""",

    "en": """You are a senior analyst for an Azerbaijani news monitoring system.

The user searched for: "{query}"
Date context: {date_context}

Below are {n} relevant news articles (sorted by relevance, highest first):

{articles}

Task: Write a concise summary in ENGLISH (3-5 sentences) synthesizing the key information. Mention specific facts, numbers, names. Do NOT start with generic phrases.

Return ONLY the summary text, nothing else.""",
}

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


class SummaryGenerator:
    def __init__(self, client: OpenAI):
        self.client = client

    def generate(self, results: list[dict], query: str, parsed: dict) -> Optional[str]:
        """Generate a multilingual summary based on detected query language."""
        if not results:
            return None

        lang = parsed.get("language", "az")
        if lang not in SUMMARY_PROMPTS:
            lang = "az"

        # Build date context string in the right language
        no_date = {"az": "tarix filtri yoxdur", "ru": "без фильтра дат", "en": "no date filter"}
        date_context = no_date.get(lang, "no date filter")
        if parsed.get("date_from") or parsed.get("date_to"):
            d_from = parsed.get("date_from") or ("başlangıc" if lang == "az" else ("начало" if lang == "ru" else "start"))
            d_to = parsed.get("date_to") or ("son" if lang == "az" else ("конец" if lang == "ru" else "end"))
            date_context = f"{d_from} — {d_to}"

        articles_text = "\n\n".join(
            f"{i+1}. [{round(r['score']*100)}%] {r['title']}\n   {r['snippet'][:200]}"
            for i, r in enumerate(results[:10])
        )

        prompt = SUMMARY_PROMPTS[lang].format(
            query=query,
            date_context=date_context,
            n=len(results),
            articles=articles_text,
        )

        try:
            resp = self.client.chat.completions.create(
                model=SUMMARY_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=400,
            )
            summary = resp.choices[0].message.content.strip()
            log.info("Summary generated (lang=%s, %d chars)", lang, len(summary))
            return summary
        except Exception as exc:
            log.error("Summary generation error: %s", exc)
            return None


class CategoryAnalyzer:
    def __init__(self, client: OpenAI):
        self.client = client

    def analyze(self, results: list[dict], query: str) -> list[dict]:
        if not results:
            return []

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
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            categories = json.loads(raw)
            return categories if isinstance(categories, list) else []
        except Exception as exc:
            log.error("Category analysis error: %s", exc)
            return []

    def global_categories(self, rows: list[dict]) -> list[dict]:
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
        self.summarizer = SummaryGenerator(self.oai)
        self.categorizer = CategoryAnalyzer(self.oai)
        log.info("NewsSearchEngine ready (Supabase pgvector + AI summary)")

    def _embed(self, text: str) -> list[float]:
        resp = self.oai.embeddings.create(model=EMBED_MODEL, input=[text])
        return resp.data[0].embedding

    def search(self, query: str, top_k: int = TOP_K, category_filter: str = None) -> dict:
        parsed = self.parser.parse(query)
        topic = parsed.get("topic") or query
        date_from = parsed.get("date_from")
        date_to = parsed.get("date_to")
        # category from explicit filter overrides parsed category
        category = category_filter or parsed.get("category")

        embedding = self._embed(topic)

        try:
            rpc_params: dict = {
                "query_embedding": embedding,
                "match_count": top_k * 3 if category else top_k,  # fetch more when filtering
            }
            if date_from:
                rpc_params["date_from"] = date_from
            if date_to:
                rpc_params["date_to"] = date_to

            resp = self.sb.rpc("search_news", rpc_params).execute()
            rows = resp.data or []

            # Apply category filter in Python (case-insensitive partial match)
            if category:
                rows = [
                    r for r in rows
                    if category.lower() in (r.get("category") or "").lower()
                ]
                log.info("Category filter '%s': %d rows remaining", category, len(rows))

        except Exception as exc:
            log.error("Supabase RPC error: %s", exc, exc_info=True)
            raise

        # Build raw results
        all_results = []
        for row in rows:
            snippet = str(row.get("content", ""))[:300].replace("\n", " ").strip()
            all_results.append({
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

        # Step 1: Filter by minimum raw score
        filtered = [r for r in all_results if r["score"] >= MIN_SCORE]

        # Step 2: Sort highest similarity first
        filtered.sort(key=lambda r: r["score"], reverse=True)

        # Step 3: Rescale scores for display (map raw range to 80-100% for UX)
        # Raw scores are typically 0.30-0.50 for this model/language
        if filtered:
            max_score = filtered[0]["score"]
            min_score = filtered[-1]["score"]
            score_range = max(max_score - min_score, 0.01)
            for r in filtered:
                # Rescale to 80-100% range
                normalized = (r["score"] - min_score) / score_range
                r["display_score"] = round(0.80 + normalized * 0.20, 4)
        else:
            for r in filtered:
                r["display_score"] = r["score"]

        log.info("Results: %d total → %d after score filter", len(all_results), len(filtered))

        # Step 3: Generate AI summary of filtered results
        summary = self.summarizer.generate(filtered, query, parsed) if filtered else None

        # Step 4: Categories (for web dashboard)
        categories = self.categorizer.analyze(filtered, query) if filtered else []

        return {
            "results": filtered,
            "summary": summary,
            "categories": categories,
            "parsed_query": parsed,
            "total_results": len(filtered),
            "total_before_filter": len(all_results),
        }

    def global_categories(self, top_n: int = 8) -> list[dict]:
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

    def get_categories(self, top_n: int = 20) -> list[str]:
        """Return top-N most frequent categories from the articles table."""
        try:
            resp = (
                self.sb.table("articles")
                .select("category")
                .neq("category", "")
                .execute()
            )
            rows = resp.data or []

            from collections import Counter
            counter: Counter = Counter()
            for r in rows:
                raw = (r.get("category") or "").strip()
                c = raw.strip(",\t \"'")
                if (
                    c
                    and "\n" not in c
                    and "\t" not in c
                    and len(c) >= 2
                    and len(c) < 35
                    and not c.startswith(",")
                    and not c.startswith("/")
                    and "<" not in c
                ):
                    counter[c] += 1

            # Return top-N by frequency, sorted alphabetically within top-N
            top = [cat for cat, _ in counter.most_common(top_n)]
            return sorted(top)
        except Exception as exc:
            log.error("get_categories error: %s", exc)
            return []

    def get_daily_digest(self, date_from: str, date_to: str) -> dict:
        """
        Generate an AI summary of what happened on a given date/range.
        Fetches top articles for the period and summarises them with GPT-4o.
        """
        try:
            # Fetch up to 30 articles for the date range
            resp = (
                self.sb.table("articles")
                .select("title, content, source, created_at, category")
                .gte("created_at", date_from)
                .lte("created_at", date_to + "T23:59:59")
                .order("created_at", desc=False)
                .limit(30)
                .execute()
            )
            rows = resp.data or []
        except Exception as exc:
            log.error("get_daily_digest DB error: %s", exc)
            return {"summary": None, "article_count": 0}

        if not rows:
            return {"summary": None, "article_count": 0}

        # Build article list for GPT
        articles_text = "\n\n".join(
            f"{i+1}. [{r.get('category','')}] {r.get('title','')}\n   "
            f"{str(r.get('content',''))[:200]}"
            for i, r in enumerate(rows)
        )

        date_label = date_from if date_from == date_to else f"{date_from} — {date_to}"

        prompt = f"""You are a senior news analyst for an Azerbaijani media monitoring system.

Below are {len(rows)} news articles published on {date_label}.

Write a concise daily news digest in AZERBAIJANI language (4-6 sentences).
Cover the most important events across politics, economy, society, and other key topics.
Mention specific facts, names, and numbers where available.
Do NOT start with generic phrases like 'Bu gün...' — go straight to the news.

Articles:
{articles_text}

Return ONLY the digest text, nothing else."""

        try:
            resp = self.oai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=500,
            )
            summary = resp.choices[0].message.content.strip()
            log.info("Daily digest generated for %s (%d articles)", date_label, len(rows))
            return {"summary": summary, "article_count": len(rows), "date": date_label}
        except Exception as exc:
            log.error("Daily digest GPT error: %s", exc)
            return {"summary": None, "article_count": len(rows)}

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
