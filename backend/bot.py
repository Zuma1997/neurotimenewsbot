"""
bot.py
------
Telegram bot for the AI News Search Assistant.
Backed by Supabase pgvector via NewsSearchEngine.

Run:
    python backend/bot.py

Required env vars:
    TELEGRAM_BOT_TOKEN
    OPENAI_API_KEY
    SUPABASE_URL
    SUPABASE_SERVICE_KEY  (or SUPABASE_ANON_KEY)
"""

import logging
import os
import textwrap

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


# ── Sentiment display ─────────────────────────────────────────────────────────
SENTIMENT_EMOJI = {
    "pozitiv": "🟢",
    "neytral": "🔵",
    "riskli": "🔴",
}


def sentiment_badge(sentiment: str | None) -> str:
    if not sentiment:
        return ""
    emoji = SENTIMENT_EMOJI.get(sentiment.lower(), "⚪")
    return f" {emoji} {sentiment.capitalize()}"


# ── Result formatter ──────────────────────────────────────────────────────────
MAX_PER_MSG = 5


def format_result(i: int, r: dict) -> str:
    dt = r["published_at"][:16] if r["published_at"] else "—"
    snippet = textwrap.shorten(r["snippet"], width=220, placeholder="…")
    score_pct = int(r["score"] * 100)
    enriched_tag = " ✨ *[Enriched]*" if r.get("is_enriched") else ""
    sent = sentiment_badge(r.get("sentiment"))
    summary = r.get("summary_az")
    summary_line = f"\n📝 _{summary}_" if summary else ""

    return (
        f"*{i}. {r['title']}*{enriched_tag}\n"
        f"🗓 {dt}  |  📰 `{r['source']}`  |  🎯 {score_pct}%{sent}\n"
        f"_{snippet}_{summary_line}\n"
        f"🔗 [Read more]({r['url']})"
    )


def format_categories(categories: list[dict], title: str = "Top Categories") -> str:
    if not categories:
        return "No categories found."
    lines = [f"*{title}*\n"]
    for i, cat in enumerate(categories[:8], 1):
        name = cat.get("category", "")
        count = cat.get("count", "")
        desc = cat.get("description", "")
        lines.append(f"{i}\. *{name}* — {count} articles")
        if desc:
            lines.append(f"   _{desc}_")
    return "\n".join(lines)


# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 *Xoş gəldiniz! / Welcome to the News Search Assistant!*\n\n"
        "I search through *~21,000 base articles* (May 10–15, 2026) "
        "plus *daily enriched articles* added automatically every morning.\n\n"
        "*Example queries:*\n"
        "• _AccessBank haqqında xəbərlər_\n"
        "• _Find news about SOCAR on May 13_\n"
        "• _Banking regulation between May 12 and May 14_\n"
        "• _Riskli iqtisadi xəbərlər_\n"
        "• _Negative economy news after May 13_\n\n"
        "*Commands:*\n"
        "/keywords — top keywords across all articles\n"
        "/stats — dataset statistics\n"
        "/help — show this message\n\n"
        "✨ *Enriched articles* are marked with a star — "
        "they include GPT-4o sentiment analysis and Azerbaijani summaries."
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def keywords_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Analysing top topic categories with AI…")
    try:
        engine = get_engine()
        cats = engine.global_categories()
        text = format_categories(cats, title="📊 Top Topic Categories (Latest Articles)")
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        log.error("Categories error: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Error: {exc}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Fetching stats…")
    try:
        engine = get_engine()
        s = engine.get_stats()
        text = (
            "📊 *Dataset Statistics*\n\n"
            f"📰 Total articles: *{s.get('total_articles', '—')}*\n"
            f"📦 Base dataset: *{s.get('base_articles', '—')}* (May 10–15)\n"
            f"✨ Enriched articles: *{s.get('enriched_articles', '—')}* (daily auto-added)\n\n"
            "_Enriched articles are added every day at 09:00 via n8n + Google Alerts + GPT-4o._"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        log.error("Stats error: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Error: {exc}")


async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.message.text.strip()
    if not query:
        return

    log.info("User [%s] query: %s", update.effective_user.id, query)
    thinking_msg = await update.message.reply_text("🔍 Searching…")

    try:
        engine = get_engine()
        result = engine.search(query, top_k=10)
    except Exception as exc:
        log.error("Search error: %s", exc, exc_info=True)
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Search failed: {exc}")
        return

    await thinking_msg.delete()

    parsed = result["parsed_query"]
    total = result["total_results"]
    results = result["results"]
    categories = result.get("categories", [])

    # Count enriched in results
    enriched_count = sum(1 for r in results if r.get("is_enriched"))

    # ── Summary header ────────────────────────────────────────────────────────
    date_info = ""
    if parsed.get("date_from") or parsed.get("date_to"):
        d_from = parsed.get("date_from") or "start"
        d_to = parsed.get("date_to") or "end"
        date_info = f"📅 Date range: `{d_from}` → `{d_to}`\n"

    enriched_note = f"  _(incl. {enriched_count} ✨ enriched)_" if enriched_count else ""
    header = (
        f"🔎 *Query:* _{query}_\n"
        f"📌 *Topic:* {parsed.get('topic', query)}\n"
        f"{date_info}"
        f"📋 Showing *{len(results)}* results{enriched_note}"
    )
    await update.message.reply_text(header, parse_mode="Markdown")

    if not results:
        await update.message.reply_text(
            "😕 No articles found. Try different keywords or a broader date range."
        )
        return

    # ── Send first batch ──────────────────────────────────────────────────────
    for i, r in enumerate(results[:MAX_PER_MSG], start=1):
        await update.message.reply_text(
            format_result(i, r),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    # ── Offer more results ────────────────────────────────────────────────────
    remaining = results[MAX_PER_MSG:]
    if remaining:
        context.user_data["remaining_results"] = remaining
        context.user_data["result_offset"] = MAX_PER_MSG
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"Show {min(MAX_PER_MSG, len(remaining))} more results ▼",
                callback_data="show_more",
            )
        ]])
        await update.message.reply_text(
            f"📋 {len(remaining)} more results available.",
            reply_markup=keyboard,
        )

    # ── AI Categories from results ────────────────────────────────────────────
    categories = result.get("categories", [])
    if categories:
        cat_text = format_categories(categories, title="📊 Topic Categories in Results")
        await update.message.reply_text(cat_text, parse_mode="Markdown")


async def show_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    remaining = context.user_data.get("remaining_results", [])
    offset = context.user_data.get("result_offset", 0)

    if not remaining:
        await query.edit_message_text("✅ No more results.")
        return

    batch = remaining[:MAX_PER_MSG]
    context.user_data["remaining_results"] = remaining[MAX_PER_MSG:]
    context.user_data["result_offset"] = offset + MAX_PER_MSG

    for i, r in enumerate(batch, start=offset + 1):
        await query.message.reply_text(
            format_result(i, r),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    still_left = context.user_data["remaining_results"]
    if still_left:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"Show {min(MAX_PER_MSG, len(still_left))} more ▼",
                callback_data="show_more",
            )
        ]])
        await query.message.reply_text(
            f"📋 {len(still_left)} more results available.",
            reply_markup=keyboard,
        )
    else:
        await query.edit_message_text("✅ All results shown.")


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
    app.add_handler(CallbackQueryHandler(show_more_callback, pattern="^show_more$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query))

    log.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        poll_interval=1.0,
        timeout=10,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
