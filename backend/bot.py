"""
bot.py
------
Telegram bot for the AI News Search Assistant.
New response format:
  🔍 Topic — date
  📋 Xülasə: [AI-generated summary]
  📰 Mənbələr: [compact source list with % and link]
  🔑 Açar sözlər: [topic]

Run:
    PYTHONPATH=backend python backend/bot.py
"""

import logging
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from search_engine import NewsSearchEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Lazy engine ───────────────────────────────────────────────────────────────
_engine: NewsSearchEngine | None = None


def get_engine() -> NewsSearchEngine:
    global _engine
    if _engine is None:
        log.info("Initialising search engine…")
        _engine = NewsSearchEngine()
    return _engine


# ── Formatters ────────────────────────────────────────────────────────────────
def format_date_header(parsed: dict, query: str) -> str:
    """Build the header line: topic + date."""
    topic = parsed.get("topic") or query
    date_str = ""
    if parsed.get("date_from") and parsed.get("date_to"):
        if parsed["date_from"] == parsed["date_to"]:
            date_str = f" — {parsed['date_from']}"
        else:
            date_str = f" — {parsed['date_from']} / {parsed['date_to']}"
    elif parsed.get("date_from"):
        date_str = f" — {parsed['date_from']}+"
    elif parsed.get("date_to"):
        date_str = f" — ≤{parsed['date_to']}"
    return f"🔍 *{topic}{date_str}*"


def format_source_line(i: int, r: dict) -> str:
    """Format a single source line: [source — XX%] Title → link"""
    pct = round(r.get("display_score", r["score"]) * 100)
    title = r["title"][:70] + ("…" if len(r["title"]) > 70 else "")
    source = r["source"]
    url = r["url"]
    enriched = " ✨" if r.get("is_enriched") else ""
    sentiment_map = {"pozitiv": "🟢", "neytral": "🔵", "riskli": "🔴"}
    sent = sentiment_map.get(r.get("sentiment", ""), "")
    return f"{i}\\. `[{source} — {pct}%]`{sent}{enriched} [{title}]({url})"


def format_categories(categories: list[dict]) -> str:
    if not categories:
        return ""
    lines = ["📊 *Kateqoriyalar:*"]
    for cat in categories[:5]:
        lines.append(f"  • {cat.get('category', '')} — {cat.get('count', '')} məqalə")
    return "\n".join(lines)


def format_search_response(query: str, result: dict) -> list[str]:
    """
    Build the full response as a list of message parts (to handle Telegram length limits).
    Format:
      🔍 Topic — date
      📋 Xülasə: ...
      📰 Mənbələr: ...
      🔑 Açar sözlər: topic
    """
    parsed = result["parsed_query"]
    results = result["results"]
    summary = result.get("summary")
    total = result["total_results"]
    total_before = result.get("total_before_filter", total)

    messages = []

    # ── Part 1: Header + Summary ──────────────────────────────────────────────
    header = format_date_header(parsed, query)
    filter_note = f"_({total_before} nəticədən {total} ≥80% uyğunluq)_" if total_before > total else f"_{total} nəticə_"

    part1 = f"{header}\n{filter_note}\n"

    if summary:
        part1 += f"\n📋 *Xülasə:*\n{summary}"
    elif not results:
        part1 += "\n\n😕 Heç bir nəticə tapılmadı\\. Başqa sorğu cəhd edin\\."
        messages.append(part1)
        return messages

    messages.append(part1)

    # ── Part 2: Sources ───────────────────────────────────────────────────────
    source_lines = ["\n📰 *Mənbələr:*"]
    for i, r in enumerate(results[:10], 1):
        source_lines.append(format_source_line(i, r))

    messages.append("\n".join(source_lines))

    # ── Part 3: Keywords ──────────────────────────────────────────────────────
    topic = parsed.get("topic") or query
    kw_line = f"\n🔑 *Açar sözlər:* `{topic}`"
    messages.append(kw_line)

    return messages


# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 *Xoş gəldiniz! / Welcome!*\n\n"
        "Mən *~21,000 Azərbaycan xəbər məqaləsi* üzərindən (10–15 may 2026 + gündəlik yenilənmə) "
        "axtarış aparan AI assistantam\\.\n\n"
        "*Nümunə sorğular:*\n"
        "• _AccessBank haqqında xəbərlər_\n"
        "• _SOCAR may 13 xəbərləri_\n"
        "• _Banking regulation between May 12 and May 14_\n"
        "• _Riskli iqtisadi xəbərlər_\n\n"
        "*Komandalar:*\n"
        "/keywords — son xəbərlərin kateqoriyaları\n"
        "/stats — statistika\n"
        "/help — bu mesaj"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2", disable_web_page_preview=True)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def keywords_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ AI kateqoriyaları analiz edir…")
    try:
        engine = get_engine()
        cats = engine.global_categories()
        if not cats:
            await update.message.reply_text("Kateqoriya tapılmadı.")
            return
        lines = ["📊 *Son xəbərlərin kateqoriyaları:*\n"]
        for i, cat in enumerate(cats[:8], 1):
            desc = cat.get("description", "")
            lines.append(f"{i}\\. *{cat['category']}* — {cat.get('count', '?')} məqalə")
            if desc:
                lines.append(f"   _{desc}_")
        await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")
    except Exception as exc:
        log.error("Categories error: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Xəta: {exc}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        engine = get_engine()
        s = engine.get_stats()
        text = (
            "📊 *Statistika*\n\n"
            f"📰 Cəmi məqalə: *{s.get('total_articles', '—')}*\n"
            f"📦 Əsas dataset: *{s.get('base_articles', '—')}* \\(10–15 may\\)\n"
            f"✨ Enriched: *{s.get('enriched_articles', '—')}* \\(gündəlik avtomatik\\)"
        )
        await update.message.reply_text(text, parse_mode="MarkdownV2")
    except Exception as exc:
        log.error("Stats error: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Xəta: {exc}")


async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.message.text.strip()
    if not query:
        return

    log.info("User [%s] query: %s", update.effective_user.id, query)
    thinking_msg = await update.message.reply_text("🔍 Axtarılır…")

    try:
        engine = get_engine()
        result = engine.search(query, top_k=20)
    except Exception as exc:
        log.error("Search error: %s", exc, exc_info=True)
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Axtarış xətası: {exc}")
        return

    await thinking_msg.delete()

    # Build and send formatted response
    parts = format_search_response(query, result)
    for part in parts:
        if part.strip():
            try:
                await update.message.reply_text(
                    part,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                # Fallback: send as plain text if MarkdownV2 fails
                log.warning("MarkdownV2 failed, sending plain: %s", exc)
                plain = part.replace("\\.", ".").replace("\\(", "(").replace("\\)", ")")
                plain = plain.replace("*", "").replace("`", "").replace("_", "")
                await update.message.reply_text(plain, disable_web_page_preview=True)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN is not set")

    log.info("Starting Telegram bot…")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("keywords", keywords_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query))

    log.info("Bot is running.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        poll_interval=1.0,
        timeout=10,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
