import { useState, useCallback } from "react";
import axios from "axios";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_BASE || "";

interface Article {
  title: string;
  source: string;
  url: string;
  published_at: string;
  category: string;
  snippet: string;
  score: number;
}

interface Keyword {
  keyword: string;
  count: number;
}

interface SearchResponse {
  results: Article[];
  keywords: Keyword[];
  parsed_query: Record<string, string | null>;
  total_in_range: number;
}

function ScoreBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    pct >= 70 ? "#22c55e" : pct >= 45 ? "#f59e0b" : "#94a3b8";
  return (
    <span
      style={{
        background: color,
        color: "#fff",
        borderRadius: 4,
        padding: "2px 7px",
        fontSize: 12,
        fontWeight: 700,
      }}
    >
      {pct}%
    </span>
  );
}

function ArticleCard({ article, index }: { article: Article; index: number }) {
  const date = article.published_at
    ? new Date(article.published_at).toLocaleString("ru-RU", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-index">#{index + 1}</span>
        <ScoreBadge score={article.score} />
        <span className="card-source">{article.source}</span>
        <span className="card-date">{date}</span>
        {article.category && (
          <span className="card-category">{article.category}</span>
        )}
      </div>
      <a
        className="card-title"
        href={article.url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {article.title || "(no title)"}
      </a>
      <p className="card-snippet">{article.snippet}</p>
    </div>
  );
}

function KeywordCloud({ keywords }: { keywords: Keyword[] }) {
  if (!keywords.length) return null;
  const max = keywords[0].count;
  return (
    <div className="keyword-section">
      <h3>🔑 Top Keywords in Results</h3>
      <div className="keyword-cloud">
        {keywords.map((kw) => {
          const size = 12 + (kw.count / max) * 14;
          const opacity = 0.5 + (kw.count / max) * 0.5;
          return (
            <span
              key={kw.keyword}
              className="keyword-tag"
              style={{ fontSize: size, opacity }}
              title={`${kw.count} occurrences`}
            >
              {kw.keyword}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export default function App() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [globalKws, setGlobalKws] = useState<Keyword[] | null>(null);
  const [loadingGlobal, setLoadingGlobal] = useState(false);

  const handleSearch = useCallback(async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      const { data } = await axios.post<SearchResponse>(`${API_BASE}/api/search`, {
        query: query.trim(),
        top_k: 15,
      });
      setResponse(data);
    } catch (e: unknown) {
      const msg =
        axios.isAxiosError(e) ? e.response?.data?.detail || e.message : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [query]);

  const handleGlobalKeywords = async () => {
    setLoadingGlobal(true);
    try {
      const { data } = await axios.get(`${API_BASE}/api/keywords/global?top_n=30`);
      setGlobalKws(data.keywords);
    } catch (e: unknown) {
      const msg = axios.isAxiosError(e) ? e.message : String(e);
      setError(msg);
    } finally {
      setLoadingGlobal(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSearch();
  };

  const parsed = response?.parsed_query;

  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <h1>📰 News Search Assistant</h1>
          <p className="subtitle">
            AI-powered search over ~21,000 Azerbaijani news articles · May 10–15, 2026
          </p>
        </div>
      </header>

      <div className="search-section">
        <div className="search-box">
          <input
            className="search-input"
            type="text"
            placeholder='e.g. "Find news about AccessBank between May 12 and May 14"'
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            className="search-btn"
            onClick={handleSearch}
            disabled={loading || !query.trim()}
          >
            {loading ? "⏳" : "Search"}
          </button>
        </div>
        <div className="example-queries">
          {[
            "AccessBank haqqında xəbərlər",
            "Banking regulation news",
            "SOCAR news on May 13",
            "Negative economy news after May 12",
            "Customs between May 11 and May 14",
          ].map((ex) => (
            <button
              key={ex}
              className="example-btn"
              onClick={() => setQuery(ex)}
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="error-box">❌ {error}</div>}

      {parsed && (
        <div className="parsed-info">
          <span>
            🔎 <strong>Topic:</strong> {parsed.topic}
          </span>
          {(parsed.date_from || parsed.date_to) && (
            <span>
              📅 <strong>Dates:</strong> {parsed.date_from || "start"} →{" "}
              {parsed.date_to || "end"}
            </span>
          )}
          <span>
            📊 <strong>Articles in range:</strong> {response?.total_in_range}
          </span>
          <span>
            📋 <strong>Results shown:</strong> {response?.results.length}
          </span>
        </div>
      )}

      {response && (
        <div className="results-section">
          {response.results.length === 0 ? (
            <div className="no-results">
              😕 No articles found. Try different keywords or date range.
            </div>
          ) : (
            <>
              <div className="results-grid">
                {response.results.map((article, i) => (
                  <ArticleCard key={article.url + i} article={article} index={i} />
                ))}
              </div>
              <KeywordCloud keywords={response.keywords} />
            </>
          )}
        </div>
      )}

      <div className="global-kw-section">
        <button
          className="global-kw-btn"
          onClick={handleGlobalKeywords}
          disabled={loadingGlobal}
        >
          {loadingGlobal ? "Loading…" : "📊 Show Global Top Keywords"}
        </button>
        {globalKws && (
          <div className="keyword-section">
            <h3>🌐 Top Keywords — Full Dataset</h3>
            <div className="keyword-table">
              {globalKws.map((kw, i) => (
                <div key={kw.keyword} className="kw-row">
                  <span className="kw-rank">#{i + 1}</span>
                  <span className="kw-word">{kw.keyword}</span>
                  <span className="kw-bar-wrap">
                    <span
                      className="kw-bar"
                      style={{
                        width: `${(kw.count / globalKws[0].count) * 100}%`,
                      }}
                    />
                  </span>
                  <span className="kw-count">{kw.count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <footer className="footer">
        Built for Neurotime Hackathon · Powered by OpenAI + Supabase pgvector
      </footer>
    </div>
  );
}
