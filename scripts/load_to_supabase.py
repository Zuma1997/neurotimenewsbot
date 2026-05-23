"""
load_to_supabase.py
-------------------
One-time script: loads the base dataset (news_data.xlsx) into Supabase,
generates OpenAI embeddings for each article, and upserts them into the
`articles` table with is_enriched = false.

Prerequisites:
  - Supabase table `articles` must exist (see supabase/schema.sql)
  - Environment variables: OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY

Usage:
    python scripts/load_to_supabase.py
    python scripts/load_to_supabase.py --batch-size 50 --start-offset 5000
"""

import argparse
import logging
import os
import re
import time
from pathlib import Path

import pandas as pd
from openai import OpenAI
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_FILE = BASE_DIR / "data" / "news_data.xlsx"

EMBED_MODEL = "text-embedding-3-small"
BATCH_SIZE = 50          # articles per embedding API call
MAX_TEXT_CHARS = 600     # chars from content used for embedding
UPSERT_CHUNK = 50        # rows per Supabase upsert call


def get_domain(url: str) -> str:
    m = re.search(r"https?://([^/]+)", str(url))
    return m.group(1) if m else ""


def make_embed_text(title: str, content: str) -> str:
    t = str(title) if title else ""
    c = str(content)[:MAX_TEXT_CHARS] if content else ""
    return f"{t}. {c}".strip()


def load_and_clean() -> pd.DataFrame:
    log.info("Reading %s …", RAW_FILE)
    df = pd.read_excel(RAW_FILE)
    df = df.rename(columns={"link": "url", "created_at": "published_at"})
    df["source"] = df["url"].apply(get_domain)
    df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce")
    df = df.dropna(subset=["content"])
    df["content"] = df["content"].astype(str)
    df["title"] = df["title"].fillna("").astype(str)
    df["category"] = df["category"].fillna("").astype(str)
    df = df.drop_duplicates(subset=["url"]).reset_index(drop=True)
    log.info("Clean dataset: %d articles", len(df))
    return df


def embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with retry logic."""
    for attempt in range(1, 6):
        try:
            resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
            return [e.embedding for e in resp.data]
        except Exception as exc:
            if attempt == 5:
                raise
            log.warning("Embedding error (attempt %d): %s — retrying in 5s", attempt, exc)
            time.sleep(5)
    return []


def upsert_rows(supabase: Client, rows: list[dict]) -> None:
    """Upsert a chunk of rows into Supabase articles table."""
    for attempt in range(1, 4):
        try:
            supabase.table("articles").upsert(rows, on_conflict="url").execute()
            return
        except Exception as exc:
            if attempt == 3:
                raise
            log.warning("Supabase upsert error (attempt %d): %s — retrying in 3s", attempt, exc)
            time.sleep(3)


def main(batch_size: int = BATCH_SIZE, start_offset: int = 0) -> None:
    # ── Validate env ──────────────────────────────────────────────────────────
    openai_key = os.getenv("OPENAI_API_KEY")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not all([openai_key, supabase_url, supabase_key]):
        raise EnvironmentError(
            "Missing required env vars: OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY"
        )

    oai = OpenAI(api_key=openai_key, base_url="https://api.openai.com/v1")
    sb: Client = create_client(supabase_url, supabase_key)

    df = load_and_clean()
    df = df.iloc[start_offset:].reset_index(drop=True)
    total = len(df)
    log.info("Starting from offset %d, %d articles to process", start_offset, total)

    processed = 0
    for i in range(0, total, batch_size):
        batch_df = df.iloc[i : i + batch_size]

        # Build embedding texts
        texts = [
            make_embed_text(row["title"], row["content"])
            for _, row in batch_df.iterrows()
        ]

        # Get embeddings
        embeddings = embed_batch(oai, texts)

        # Build rows for Supabase
        rows = []
        for j, (_, row) in enumerate(batch_df.iterrows()):
            pub_at = row["published_at"]
            rows.append({
                "url": row["url"],
                "title": row["title"],
                "content": row["content"],
                "category": row["category"],
                "source": row["source"],
                "created_at": pub_at.isoformat() if pd.notna(pub_at) else None,
                "embedding": embeddings[j],
                "is_enriched": False,
            })

        # Upsert in sub-chunks to avoid payload size limits
        for k in range(0, len(rows), UPSERT_CHUNK):
            upsert_rows(sb, rows[k : k + UPSERT_CHUNK])

        processed += len(batch_df)
        log.info(
            "Progress: %d / %d (%.1f%%)",
            start_offset + processed,
            start_offset + total,
            100 * processed / total,
        )

    log.info("Done. Total articles loaded: %d", start_offset + processed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load news dataset into Supabase")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--start-offset", type=int, default=0,
                        help="Skip first N rows (for resuming interrupted loads)")
    args = parser.parse_args()
    main(batch_size=args.batch_size, start_offset=args.start_offset)
