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
  is_enriched?: boolean;
  sentiment?: string;
  summary_az?: string;
}

interface Category {
  category: string;
  count: number | string;
  description?: string;
}

interface SearchResponse {
  results: Article[];
  categories: Category[];
  parsed_query: Record<string, string | null>;
  total_results: number;
}

function ScoreBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const bg = pct >= 70 ? "#6DC49A" : pct >= 45 ? "#87CEEB" : "#c0c3d0";
  return (
    <span className="score-badge" style={{ background: bg }}>
      {pct}%
    </span>
  );
}

function SentimentBadge({ sentiment }: { sentiment?: string }) {
  if (!sentiment) return null;
  const labels: Record<string, string> = {
    pozitiv: "🟢 Pozitiv",
    neytral: "🔵 Neytral",
    riskli: "🔴 Riskli",
  };
  return (
    <span className={`sentiment-${sentiment.toLowerCase()}`}>
      {labels[sentiment.toLowerCase()] || sentiment}
    </span>
  );
}

function ArticleCard({ article, index }: { article: Article; index: number }) {
  const date = article.published_at
    ? new Date(article.published_at).toLocaleString("az-AZ", {
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
        {article.is_enriched && (
          <span className="card-enriched">✨ Enriched</span>
        )}
        {article.sentiment && (
          <SentimentBadge sentiment={article.sentiment} />
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
      {article.summary_az && (
        <div className="card-summary">{article.summary_az}</div>
      )}
    </div>
  );
}

function CategoriesBlock({
  categories,
  title,
}: {
  categories: Category[];
  title: string;
}) {
  if (!categories.length) return null;
  return (
    <div className="category-section">
      <h3>{title}</h3>
      <div className="category-list">
        {categories.map((cat, i) => (
          <div key={i} className="category-item">
            <div className="category-num">{i + 1}</div>
            <div className="category-body">
              <span className="category-name">{cat.category}</span>
              <span className="category-count">{cat.count} articles</span>
              {cat.description && (
                <div className="category-desc">{cat.description}</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [globalCats, setGlobalCats] = useState<Category[] | null>(null);
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
      const msg = axios.isAxiosError(e)
        ? e.response?.data?.detail || e.message
        : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [query]);

  const handleGlobalCategories = async () => {
    setLoadingGlobal(true);
    try {
      const { data } = await axios.get(`${API_BASE}/api/keywords/global?top_n=8`);
      setGlobalCats(data.keywords || data.categories || []);
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
      {/* Header */}
      <header className="header">
        <div className="header-inner">
          <h1>
            News <span className="accent">Intelligence</span>
          </h1>
          <p className="subtitle">
            AI-powered search · ~21,000 Azerbaijani articles · May 10–15, 2026 + daily enrichment
          </p>
        </div>
      </header>

      {/* Search */}
      <div className="search-section">
        <div className="search-box">
          <input
            className="search-input"
            type="text"
            placeholder='e.g. "AccessBank haqqında xəbərlər" or "SOCAR news on May 13"'
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            className="search-btn"
            onClick={handleSearch}
            disabled={loading || !query.trim()}
          >
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
        <div className="example-queries">
          {[
            "AccessBank haqqında xəbərlər",
            "Banking regulation news",
            "SOCAR news on May 13",
            "Riskli iqtisadi xəbərlər",
            "Customs between May 11 and May 14",
          ].map((ex) => (
            <button key={ex} className="example-btn" onClick={() => setQuery(ex)}>
              {ex}
            </button>
          ))}
        </div>
      </div>

      {/* Error */}
      {error && <div className="error-box">⚠️ {error}</div>}

      {/* Parsed query info */}
      {parsed && (
        <div className="parsed-info">
          <span>
            <strong>Topic:</strong> {parsed.topic}
          </span>
          {(parsed.date_from || parsed.date_to) && (
            <span>
              <strong>Dates:</strong> {parsed.date_from || "start"} →{" "}
              {parsed.date_to || "end"}
            </span>
          )}
          <span>
            <strong>Results:</strong> {response?.total_results}
          </span>
          {(response?.results.filter((r) => r.is_enriched).length ?? 0) > 0 && (
            <span>
              <strong>✨ Enriched:</strong>{" "}
              {response?.results.filter((r) => r.is_enriched).length}
            </span>
          )}
        </div>
      )}

      {/* Results */}
      {response && (
        <div className="results-section">
          {response.results.length === 0 ? (
            <div className="no-results">
              No articles found. Try different keywords or a broader date range.
            </div>
          ) : (
            <>
              <div className="results-grid">
                {response.results.map((article, i) => (
                  <ArticleCard key={article.url + i} article={article} index={i} />
                ))}
              </div>
              {response.categories?.length > 0 && (
                <CategoriesBlock
                  categories={response.categories}
                  title="📊 Topic Categories in Results"
                />
              )}
            </>
          )}
        </div>
      )}

      {/* Global categories */}
      <div className="global-kw-section">
        <button
          className="global-kw-btn"
          onClick={handleGlobalCategories}
          disabled={loadingGlobal}
        >
          {loadingGlobal ? "Analysing…" : "📊 Show Global Topic Categories"}
        </button>
        {globalCats && globalCats.length > 0 && (
          <CategoriesBlock
            categories={globalCats}
            title="🌐 Global Topic Categories — Latest Articles"
          />
        )}
      </div>

      <footer className="footer">
        Built for Neurotime Hackathon · Powered by OpenAI + Supabase pgvector
      </footer>
    </div>
  );
}
