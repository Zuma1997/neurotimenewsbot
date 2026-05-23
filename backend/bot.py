"""
bot.py
------
Telegram bot for the AI News Search Assistant.
Multilingual: detects query language (az/ru/en) and responds in the same language.

Commands:
  /start, /help      - welcome message
  /keywords          - top topic categories
  /stats             - database statistics
  /dailydemo         - demo daily digest for May 13, 2026
  /subscribe         - subscribe to daily digest (09:00 UTC)
  /unsubscribe       - unsubscribe from daily digest
"""

import logging
import os
import re
from datetime import datetime, timezone, timedelta, time as dt_time

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
        log.info("Initialising search engine...")
        _engine = NewsSearchEngine()
    return _engine


# ── Multilingual labels ───────────────────────────────────────────────────────
LABELS = {
    "az": {
        "summary":   "📋 *Xülasə:*",
        "sources":   "📰 *Mənbələr:*",
        "keywords":  "🔑 *Açar sözlər:*",
        "no_results": "😕 Nəticə tapılmadı\\. Başqa sorğu cəhd edin\\.",
        "searching":  "🔍 Axtarılır…",
        "found":      "{total} nəticədən {shown} ≥80% uyğunluq",
        "more":       "Daha {n} nəticə var ▼",
        "categories": "📊 *Kateqoriyalar:*",
    },
    "ru": {
        "summary":   "📋 *Резюме:*",
        "sources":   "📰 *Источники:*",
        "keywords":  "🔑 *Ключевые слова:*",
        "no_results": "😕 Результатов не найдено\\. Попробуйте другой запрос\\.",
        "searching":  "🔍 Поиск…",
        "found":      "{total} результатов, {shown} ≥80% совпадения",
        "more":       "Ещё {n} результатов ▼",
        "categories": "📊 *Категории:*",
    },
    "en": {
        "summary":   "📋 *Summary:*",
        "sources":   "📰 *Sources:*",
        "keywords":  "🔑 *Keywords:*",
        "no_results": "😕 No results found\\. Try a different query\\.",
        "searching":  "🔍 Searching…",
        "found":      "{total} results, {shown} ≥80% match",
        "more":       "{n} more results ▼",
        "categories": "📊 *Categories:*",
    },
}


def get_labels(lang: str) -> dict:
    return LABELS.get(lang, LABELS["az"])


# ── Formatters ────────────────────────────────────────────────────────────────
def escape_md(text: str) -> str:
    """Escape special MarkdownV2 characters."""
    for ch in r"_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def format_date_header(parsed: dict, query: str) -> str:
    topic = escape_md(parsed.get("topic") or query)
    date_str = ""
    if parsed.get("date_from") and parsed.get("date_to"):
        if parsed["date_from"] == parsed["date_to"]:
            date_str = f" — {parsed['date_from']}"
        else:
            date_str = f" — {parsed['date_from']} / {parsed['date_to']}"
    elif parsed.get("date_from"):
        date_str = f" — {parsed['date_from']}\\+"
    elif parsed.get("date_to"):
        date_str = f" — ≤{parsed['date_to']}"
    return f"🔍 *{topic}{date_str}*"


def format_source_line(i: int, r: dict) -> str:
    pct = round(r.get("display_score", r["score"]) * 100)
    title = escape_md(r["title"][:70] + ("…" if len(r["title"]) > 70 else ""))
    source = escape_md(r["source"])
    url = r["url"]
    enriched = " ✨" if r.get("is_enriched") else ""
    sentiment_map = {"pozitiv": "🟢", "neytral": "🔵", "riskli": "🔴"}
    sent = sentiment_map.get(r.get("sentiment", ""), "")
    return f"{i}\\. `[{source} — {pct}%]`{sent}{enriched} [{title}]({url})"


def format_search_response(query: str, result: dict) -> list[str]:
    parsed = result["parsed_query"]
    results = result["results"]
    summary = result.get("summary")
    total_before = result.get("total_before_filter", len(results))
    lang = parsed.get("language", "az")
    lbl = get_labels(lang)

    messages = []

    header = format_date_header(parsed, query)
    found_line = f"_{lbl['found'].format(total=total_before, shown=len(results))}_"

    part1 = f"{header}\n{found_line}\n"
    if summary:
        part1 += f"\n{lbl['summary']}\n{escape_md(summary)}"
    elif not results:
        part1 += f"\n\n{lbl['no_results']}"
        messages.append(part1)
        return messages

    messages.append(part1)

    source_lines = [f"\n{lbl['sources']}"]
    for i, r in enumerate(results[:10], 1):
        source_lines.append(format_source_line(i, r))
    messages.append("\n".join(source_lines))

    topic = escape_md(parsed.get("topic") or query)
    messages.append(f"\n{lbl['keywords']} `{topic}`")

    return messages


# ── Daily Digest ──────────────────────────────────────────────────────────────

DAILY_DIGEST_PROMPT = """Sən Azərbaycan xəbər monitorinq sisteminin baş analitikisən.

Aşağıda {date} tarixində dərc edilmiş {n} xəbər məqaləsi var.
Hər məqalənin kateqoriyası və sentimentallığı göstərilib.

Məqalələr:
{articles}

Aşağıdakı formata uyğun olaraq gündəlik hesabat hazırla:

1. ÜMUMİ XÜLASƏ (3-4 cümlə): Günün ən önəmli hadisələrini əhatə edən qısa xülasə

2. KATEQORİYALAR ÜZRƏ BAXIŞ: Hər aktiv kateqoriya üçün 1-2 cümləlik qısa icmal

3. POZİTİV XƏBƏRLƏR 🟢: Ən yaxşı 3 xəbər (başlıq + qısa izah)

4. RİSKLİ XƏBƏRLƏR 🔴: Ən narahatçılıq doğuran 3 xəbər (başlıq + qısa izah)

5. STATİSTİKA: Pozitiv: X | Neytral: X | Riskli: X

Yalnız hesabat mətnini qaytar, izahat yox."""


async def generate_daily_digest(date_str: str) -> str | None:
    """Generate a full daily digest for a given date (YYYY-MM-DD)."""
    from openai import OpenAI
    engine = get_engine()
    oai = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url="https://api.openai.com/v1"
    )

    try:
        resp = (
            engine.sb.table("articles")
            .select("title, category, sentiment")
            .gte("created_at", date_str)
            .lte("created_at", date_str + "T23:59:59")
            .order("created_at")
            .limit(80)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:
        log.error("Daily digest DB error: %s", exc)
        return None

    if not rows:
        return None

    articles_text = "\n".join(
        f"{i+1}. [{r.get('category','?')}] [{r.get('sentiment','?')}] {r.get('title','')}"
        for i, r in enumerate(rows)
    )

    pos = sum(1 for r in rows if r.get("sentiment") == "pozitiv")
    neu = sum(1 for r in rows if r.get("sentiment") == "neytral")
    risk = sum(1 for r in rows if r.get("sentiment") == "riskli")

    prompt = DAILY_DIGEST_PROMPT.format(
        date=date_str,
        n=len(rows),
        articles=articles_text,
    )

    try:
        gpt_resp = oai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=1200,
        )
        # Get plain text from GPT
        digest_text = gpt_resp.choices[0].message.content.strip()

        # Make section headers bold using MarkdownV2
        # GPT returns lines like "1. \u00dcMUM\u0130 X\u00dcLAS\u018c:" or "2. KATEQOR\u0130YALAR..."
        import re
        def bold_headers(text: str) -> str:
            # Bold numbered section headers: "1. HEADER:" or "1. HEADER —"
            text = re.sub(
                r'^(\d+\.\s+)([A-Z\u0130\u018e\u0152\u00dc\u00c7\u011e\u015e\u00d6\u00dc][A-Z\u0130\u018e\u0152\u00dc\u00c7\u011e\u015e\u00d6\u00dc\s\u0130\u018e\u0152\u00dc\u00c7\u011e\u015e\u00d6\u00dc]+[:\s])',
                lambda m: m.group(1) + '*' + escape_md(m.group(2).rstrip()) + '*',
                text, flags=re.MULTILINE
            )
            return text

        digest_md = bold_headers(escape_md(digest_text))

        # Count only articles that have sentiment assigned
        total_with_sentiment = pos + neu + risk
        header = (
            f"*\[{escape_md(date_str)}\] Gundəlik Xəbər Hesabatı*\n"
            f"Məqalə: {len(rows)} \| Pozitiv: {pos} \| Neytral: {neu} \| Riskli: {risk}\n"
            f"_Sentimentlər: {total_with_sentiment}/{len(rows)} məqalə_\n"
            f"\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\n\n"
        )
        return header + digest_md
    except Exception as exc:
        log.error("Daily digest GPT error: %s", exc)
        return None


async def _send_digest_message(target, digest: str) -> None:
    """Send digest with MarkdownV2 (bold headers), fallback to plain text."""
    chunks = []
    if len(digest) <= 4000:
        chunks = [digest]
    else:
        parts = digest.split("\n\n")
        chunk = ""
        for part in parts:
            if len(chunk) + len(part) + 2 < 3800:
                chunk += part + "\n\n"
            else:
                if chunk:
                    chunks.append(chunk.strip())
                chunk = part + "\n\n"
        if chunk.strip():
            chunks.append(chunk.strip())

    for chunk in chunks:
        try:
            await target.reply_text(chunk, parse_mode="MarkdownV2",
                                    disable_web_page_preview=True)
        except Exception as e:
            log.warning("MarkdownV2 failed for digest chunk: %s", e)
            # Strip all markdown and send plain
            plain = re.sub(r'[*_`\[\]\(\)~>#\+\-=|{}\.!\\]', '', chunk)
            await target.reply_text(plain, disable_web_page_preview=True)


# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 *Xoş gəldiniz\\! / Добро пожаловать\\! / Welcome\\!*\n\n"
        "I search through *~21,000 Azerbaijani news articles* \\(May 10–15, 2026 \\+ daily updates\\)\\.\n\n"
        "*Examples / Примеры / Nümunələr:*\n"
        "• `AccessBank haqqında xəbərlər`\n"
        "• `Скажи про SOCAR что было 13 мая`\n"
        "• `Banking regulation between May 12 and May 14`\n"
        "• `Riskli iqtisadi xəbərlər`\n\n"
        "*Commands:*\n"
        "/keywords — top topic categories\n"
        "/stats — statistics\n"
        "/dailydemo — demo daily digest \\(May 13\\)\n"
        "/subscribe — daily digest at 09:00 UTC\n"
        "/unsubscribe — stop daily digest\n"
        "/help — this message"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2", disable_web_page_preview=True)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def keywords_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Analysing categories…")
    try:
        engine = get_engine()
        cats = engine.global_categories()
        if not cats:
            await update.message.reply_text("No categories found.")
            return
        lines = ["📊 *Top Topic Categories:*\n"]
        for i, cat in enumerate(cats[:8], 1):
            name = escape_md(cat.get("category", ""))
            count = cat.get("count", "?")
            desc = escape_md(cat.get("description", ""))
            lines.append(f"{i}\\. *{name}* — {count}")
            if desc:
                lines.append(f"   _{desc}_")
        await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")
    except Exception as exc:
        log.error("Categories error: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Error: {exc}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        engine = get_engine()
        s = engine.get_stats()
        text = (
            "📊 *Statistics*\n\n"
            f"📰 Total: *{s.get('total_articles', '—')}*\n"
            f"📦 Base dataset: *{s.get('base_articles', '—')}* \\(May 10–15\\)\n"
            f"✨ Enriched: *{s.get('enriched_articles', '—')}* \\(daily auto\\)"
        )
        await update.message.reply_text(text, parse_mode="MarkdownV2")
    except Exception as exc:
        log.error("Stats error: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ Error: {exc}")


async def dailydemo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a demo daily digest for May 13, 2026."""
    msg = await update.message.reply_text(
        "Gundəlik hesabat hazırlanır... (~20 san)"
    )
    digest = await generate_daily_digest("2026-05-13")
    await msg.delete()
    if not digest:
        await update.message.reply_text(
            "2026-05-13 tarixi ucun melumat tapilmadi."
        )
        return
    await _send_digest_message(update.message, digest)


async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Subscribe to daily digest."""
    chat_id = update.effective_chat.id
    subs = context.bot_data.setdefault("subscribers", set())
    subs.add(chat_id)
    await update.message.reply_text(
        "✅ *Gündəlik hesabata abunə oldunuz\\!*\n"
        "_Hər gün saat 09:00 UTC\\-də xəbər hesabatı göndəriləcək\\._\n\n"
        "Abunəliyi dayandırmaq üçün: /unsubscribe",
        parse_mode="MarkdownV2"
    )


async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unsubscribe from daily digest."""
    chat_id = update.effective_chat.id
    subs = context.bot_data.get("subscribers", set())
    subs.discard(chat_id)
    await update.message.reply_text(
        "❌ Abunəlikdən çıxdınız\\. Artıq gündəlik hesabat göndərilməyəcək\\.",
        parse_mode="MarkdownV2"
    )


async def send_daily_digest_to_all(context) -> None:
    """Job: send daily digest to all subscribed users at 09:00 UTC."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    log.info("Sending daily digest for %s", yesterday)

    digest = await generate_daily_digest(yesterday)
    if not digest:
        log.warning("No digest generated for %s", yesterday)
        return

    subscribers = context.bot_data.get("subscribers", set())
    if not subscribers:
        log.info("No subscribers for daily digest")
        return

    for chat_id in subscribers:
        try:
            chunks = []
            if len(digest) <= 4000:
                chunks = [digest]
            else:
                parts = digest.split("\n\n")
                chunk = ""
                for part in parts:
                    if len(chunk) + len(part) + 2 < 3800:
                        chunk += part + "\n\n"
                    else:
                        if chunk:
                            chunks.append(chunk.strip())
                        chunk = part + "\n\n"
                if chunk.strip():
                    chunks.append(chunk.strip())

            for chunk in chunks:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode="MarkdownV2",
                        disable_web_page_preview=True,
                    )
                except Exception:
                    plain = chunk
                    for ch in r"_*[]()~`>#+-=|{}.!\\":
                        plain = plain.replace(f"\\{ch}", ch)
                    plain = plain.replace("*", "").replace("`", "").replace("_", "")
                    await context.bot.send_message(chat_id=chat_id, text=plain,
                                                   disable_web_page_preview=True)
            log.info("Digest sent to %s", chat_id)
        except Exception as exc:
            log.warning("Failed to send digest to %s: %s", chat_id, exc)


async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.message.text.strip()
    if not query:
        return

    log.info("User [%s] query: %s", update.effective_user.id, query)
    thinking_msg = await update.message.reply_text("🔍 …")

    try:
        engine = get_engine()
        result = engine.search(query, top_k=20)
    except Exception as exc:
        log.error("Search error: %s", exc, exc_info=True)
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Error: {exc}")
        return

    await thinking_msg.delete()

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
                log.warning("MarkdownV2 failed, sending plain: %s", exc)
                plain = part
                for ch in r"_*[]()~`>#+-=|{}.!\\":
                    plain = plain.replace(f"\\{ch}", ch)
                plain = plain.replace("*", "").replace("`", "").replace("_", "")
                await update.message.reply_text(plain, disable_web_page_preview=True)

    remaining = result["results"][10:]
    if remaining:
        lang = result["parsed_query"].get("language", "az")
        lbl = get_labels(lang)
        context.user_data["remaining_results"] = remaining
        context.user_data["result_offset"] = 10
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                lbl["more"].format(n=min(5, len(remaining))),
                callback_data="show_more",
            )
        ]])
        await update.message.reply_text(
            f"📋 {len(remaining)} more",
            reply_markup=keyboard,
        )


async def show_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    remaining = context.user_data.get("remaining_results", [])
    offset = context.user_data.get("result_offset", 10)

    if not remaining:
        await query.edit_message_text("✅ Done.")
        return

    batch = remaining[:5]
    context.user_data["remaining_results"] = remaining[5:]
    context.user_data["result_offset"] = offset + 5

    for i, r in enumerate(batch, start=offset + 1):
        try:
            await query.message.reply_text(
                format_source_line(i, r),
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
        except Exception:
            await query.message.reply_text(
                f"{i}. {r['title'][:80]} — {r['source']}",
                disable_web_page_preview=True,
            )

    still_left = context.user_data["remaining_results"]
    if still_left:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"Show {min(5, len(still_left))} more ▼", callback_data="show_more")
        ]])
        await query.message.reply_text(f"📋 {len(still_left)} more", reply_markup=keyboard)
    else:
        await query.edit_message_text("✅ All results shown.")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN is not set")

    log.info("Starting Telegram bot...")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("keywords", keywords_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("dailydemo", dailydemo_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_cmd))
    app.add_handler(CallbackQueryHandler(show_more_callback, pattern="^show_more$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query))

    # Schedule daily digest at 09:00 UTC
    app.job_queue.run_daily(
        send_daily_digest_to_all,
        time=dt_time(hour=9, minute=0, second=0),
        name="daily_digest",
    )
    log.info("Daily digest scheduled at 09:00 UTC")

    log.info("Bot is running.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        poll_interval=1.0,
        timeout=10,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
