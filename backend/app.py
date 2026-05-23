"""
app.py
------
FastAPI backend for the News Search Assistant.
Backed by Supabase pgvector via NewsSearchEngine.

Run:
    uvicorn backend.app:app --host 0.0.0.0 --port 8000
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from search_engine import NewsSearchEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="News Search Assistant", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*",  # Allow all origins (GitHub Pages, local dev, etc.)
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── Lazy engine ───────────────────────────────────────────────────────────────
_engine: NewsSearchEngine | None = None


def get_engine() -> NewsSearchEngine:
    global _engine
    if _engine is None:
        log.info("Initialising search engine…")
        _engine = NewsSearchEngine()
    return _engine


# ── Models ────────────────────────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    top_k: int = 15


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "backend": "supabase-pgvector"}


@app.post("/api/search")
def search(req: SearchRequest):
    log.info("Search: %s", req.query)
    try:
        return get_engine().search(req.query, top_k=req.top_k)
    except Exception as exc:
        log.error("Search error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/keywords/global")
def global_keywords(top_n: int = Query(default=20, le=50)):
    try:
        return {"keywords": get_engine().global_keywords(top_n=top_n)}
    except Exception as exc:
        log.error("Keywords error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/stats")
def stats():
    try:
        return get_engine().get_stats()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/categories")
def categories():
    """Return distinct category list from the articles table."""
    try:
        return {"categories": get_engine().get_categories()}
    except Exception as exc:
        log.error("Categories error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/digest")
def daily_digest(
    date_from: str = Query(..., description="Start date YYYY-MM-DD"),
    date_to: str = Query(None, description="End date YYYY-MM-DD (defaults to date_from)"),
):
    """Generate an AI-powered daily news digest for a date or date range."""
    if not date_to:
        date_to = date_from
    log.info("Digest request: %s — %s", date_from, date_to)
    try:
        return get_engine().get_daily_digest(date_from, date_to)
    except Exception as exc:
        log.error("Digest error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Serve dashboard.html ─────────────────────────────────────────────────────
DASHBOARD = Path(__file__).parent.parent / "dashboard.html"

@app.get("/dashboard")
@app.get("/dashboard.html")
def serve_dashboard():
    if DASHBOARD.exists():
        return FileResponse(str(DASHBOARD))
    raise HTTPException(status_code=404, detail="dashboard.html not found")

@app.get("/")
def root():
    if DASHBOARD.exists():
        return FileResponse(str(DASHBOARD))
    return {"status": "ok", "message": "News Search API", "docs": "/docs"}
