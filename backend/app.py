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
    allow_origins=["*"],
    allow_methods=["*"],
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


# ── Serve React frontend (after build) ───────────────────────────────────────
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        return FileResponse(str(FRONTEND_DIST / "index.html"))
