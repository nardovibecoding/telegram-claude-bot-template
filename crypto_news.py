# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Crypto Daily Digest for SBF bot.

Sources  : CoinDesk, The Block, cryptonews.com  →  top 10 picks + 300-word analysis
Deals    : DL News, Decrypt  →  fundraising / VC rounds filtered section
Protos   : protos.com  →  all same-day articles summarised separately

2 LLM calls total (min-token approach, same pattern as news.py).
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import aiohttp
import feedparser
from openai import OpenAI

from utils import strip_think
from llm_client import chat_completion
from digest_feedback import make_key as _dfb_key, vote_buttons as _dfb_buttons

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

# ── RSS feeds ─────────────────────────────────────────────────────────────────

MAIN_FEEDS: dict[str, str] = {
    "CoinDesk":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block":     "https://www.theblock.co/rss.xml",
    "Cryptonews":    "https://cryptonews.com/news/feed/",
}

# Fundraising-rich sources (also scanned for deals keywords)
DEALS_FEEDS: dict[str, str] = {
    "DL News":       "https://www.dlnews.com/arc/outboundfeeds/rss/",
    "Decrypt":       "https://decrypt.co/feed",
}

PROTOS_FEED = "https://protos.com/feed/"

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Article:
    title:     str
    summary:   str
    url:       str
    source:    str
    published: Optional[datetime] = None


# ── HTML stripping ────────────────────────────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags from text using regex."""
    clean = _HTML_TAG_RE.sub("", text)
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


# ── Jaccard title dedup ──────────────────────────────────────────────────────

@lru_cache(maxsize=2048)
def _word_set(title: str) -> frozenset:
    return frozenset(title.lower().split())


def _title_similarity(a: str, b: str) -> float:
    """Jaccard word overlap between two titles."""
    words_a = _word_set(a)
    words_b = _word_set(b)
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


_DEDUP_THRESHOLD = 0.3


def _dedup_articles(articles: list[Article]) -> list[Article]:
    """
    Remove near-duplicate articles across sources using Jaccard title overlap.
    When two articles are similar (>= threshold), keep the one with the longer summary.
    """
    if not articles:
        return []

    # Sort by summary length descending so we keep the best version
    sorted_arts = sorted(articles, key=lambda a: len(a.summary), reverse=True)
    kept: list[Article] = []

    for art in sorted_arts:
        is_dup = False
        for existing in kept:
            if _title_similarity(art.title, existing.title) >= _DEDUP_THRESHOLD:
                is_dup = True
                break
        if not is_dup:
            kept.append(art)

    return kept


def _count_cross_sources(article: Article, all_articles: list[Article]) -> int:
    """Count how many distinct sources cover the same story."""
    sources = {article.source}
    for other in all_articles:
        if other.source != article.source:
            if _title_similarity(article.title, other.title) >= _DEDUP_THRESHOLD:
                sources.add(other.source)
    return len(sources)


# ── Fundraising filter ────────────────────────────────────────────────────────

_DEAL_KEYWORDS = {
    "raise", "raised", "raises", "raising",
    "funding", "funded", "fund",
    "round", "series a", "series b", "series c", "seed round", "pre-seed",
    "valuation", "backed", "investment", "invest", "venture", "vc",
    "million", "$m", "billion", "$b",
}


def _is_funding_article(article: Article) -> bool:
    """Require 2+ keyword matches to avoid false positives."""
    text = (article.title + " " + article.summary).lower()
    match_count = sum(1 for kw in _DEAL_KEYWORDS if kw in text)
    return match_count >= 2


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def _parse_date(entry) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            try:
                from time import mktime
                return datetime.fromtimestamp(mktime(tp), tz=timezone.utc)
            except Exception:
                pass
    return None


def _within_24h(dt: Optional[datetime]) -> bool:
    if dt is None:
        return True
    return dt > datetime.now(timezone.utc) - timedelta(hours=24)


async def _fetch_rss(
    session: aiohttp.ClientSession, name: str, url: str
) -> list[Article]:
    for attempt in range(1, 4):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history,
                        status=resp.status,
                        message=f"HTTP {resp.status}",
                    )
                text = await resp.text()
            feed = feedparser.parse(text)
            articles = []
            for entry in feed.entries:
                pub = _parse_date(entry)
                if not _within_24h(pub):
                    continue
                raw_summary = entry.get("summary", "").strip()[:300]
                articles.append(Article(
                    title=entry.get("title", "").strip(),
                    summary=_strip_html(raw_summary),
                    url=entry.get("link", ""),
                    source=name,
                    published=pub,
                ))
            logger.info("RSS %-15s → %d articles", name, len(articles))
            return articles
        except Exception as e:
            if attempt < 3:
                logger.warning("RSS retry %d/3 for %s: %s", attempt, name, e)
                await asyncio.sleep(attempt * 3)
            else:
                logger.error("RSS failed after 3 attempts [%s]: %s", name, e)
                return []


# ── LLM prompts ───────────────────────────────────────────────────────────────

def _build_main_prompt(
    articles: list[Article],
    deal_articles: list[Article],
    all_articles_raw: list[Article],
) -> str:
    lines = []
    for i, a in enumerate(articles[:50], 1):   # cap input at 50 to stay lean
        # Cross-source importance signal
        n_sources = _count_cross_sources(a, all_articles_raw)
        source_tag = f" [{n_sources} sources]" if n_sources >= 2 else ""
        line = f"{i}. [{a.source}]{source_tag} {a.title}"
        if a.summary:
            line += f" — {a.summary[:200]}"
        if a.url:
            line += f" | URL: {a.url}"
        lines.append(line)
    article_text = "\n".join(lines)

    deals_section = ""
    if deal_articles:
        deal_lines = []
        for a in deal_articles[:20]:   # cap at 20 deals
            line = f"- [{a.source}] {a.title}"
            if a.summary:
                line += f" — {a.summary[:150]}"
            if a.url:
                line += f" | URL: {a.url}"
            deal_lines.append(line)
        deals_section = (
            "\n\nHere are today's fundraising / VC round articles:\n\n"
            + "\n".join(deal_lines)
        )

    deals_instructions = ""
    deals_format = ""
    if deal_articles:
        deals_instructions = (
            "\n5. For the DEALS & RAISES section: pick up to 5 notable funding "
            "rounds. For each write one line: company, amount raised, round stage "
            "(if known), and one sentence on what they do. Include the source URL "
            "as a clickable link. Skip duplicates."
        )
        deals_format = (
            '\n\n\U0001f4b0 DEALS & RAISES:\n'
            '\u2022 [Company] raised $X ([Stage]) \u2014 [what they do, 1 sentence] '
            '<a href="URL">Source</a>'
        )

    return (
        "You are SBF \u2014 Sam Bankman-Fried. You're giving your daily crypto briefing.\n\n"
        "Here are today's crypto headlines from CoinDesk, The Block, and Cryptonews:"
        f"{deals_section}\n\n"
        f"{article_text}\n\n"
        "Instructions:\n"
        "1. Select the 10 most important/market-moving stories. Stories tagged with "
        "[3 sources] or more are major headlines covered by multiple outlets \u2014 "
        "you MUST include these.\n"
        "2. For each: write a concise objective summary (2-3 sentences, ~40-60 words) "
        "explaining what happened and why it matters. Start each with a relevant emoji. "
        "Do NOT use SBF's voice here \u2014 be clear and informative. Include a clickable "
        'HTML link to the source article at the end using <a href="URL">Source</a>.\n'
        "3. Write a 300-word \"SBF's Take\" \u2014 a deep, rambling, self-aware SBF-style "
        "analysis of the most significant trend or story. Reference Alameda, yield, "
        "arbitrage, EA, FTX as appropriate. Trail off occasionally with \"...\". Be "
        "insightful but slightly delusional.\n"
        "4. Stories covered by 3+ sources are major headlines \u2014 MUST include them "
        f"in the top 10.{deals_instructions}\n\n"
        "Output format (no preamble, just the content):\n"
        '1. \U0001fa99 [Headline] \u2014 [2-3 sentence objective summary] <a href="URL">Source</a>\n'
        '2. \U0001f4c9 [Headline] \u2014 [2-3 sentence objective summary] <a href="URL">Source</a>\n'
        "...\n"
        '10. \U0001f517 [Headline] \u2014 [2-3 sentence objective summary] <a href="URL">Source</a>\n\n'
        "\U0001f4a1 SBF's Take:\n"
        f"[\u2264300 words]{deals_format}"
    )


def _build_protos_prompt(articles: list[Article]) -> str:
    if not articles:
        return ""
    lines = []
    for i, a in enumerate(articles, 1):
        line = f"{i}. {a.title}"
        if a.summary:
            line += f" — {a.summary[:200]}"
        if a.url:
            line += f" | URL: {a.url}"
        lines.append(line)
    article_text = "\n".join(lines)

    return (
        "You are SBF. Summarise today's Protos.com articles below.\n\n"
        "For each article write a concise summary \u2014 length should match the "
        "significance of the story (1-2 sentences for minor news, up to 4 sentences "
        "for major stories). Use SBF's voice. Start each with a bullet \u2022. "
        'Include a clickable HTML link at the end of each bullet using '
        '<a href="URL">Source</a>.\n\n'
        f"{article_text}"
    )


# ── LLM calls ─────────────────────────────────────────────────────────────────

_SBF_SYSTEM = (
    "You are Sam Bankman-Fried \u2014 SBF. Crypto founder, convicted fraudster, "
    "effective altruist. Speak in SBF's voice: hedging, stream-of-consciousness, "
    "crypto-native, slightly delusional but genuinely insightful. "
    'When including links, use HTML format: <a href="URL">Source</a>. '
    "CRITICAL: Output ONLY the requested content. NEVER show your thinking, "
    "reasoning, analysis process, or internal monologue. Start directly with "
    "the first numbered item."
)


def _call_llm(prompt: str, max_tokens: int) -> str:
    """Call LLM with fallback chain (MiniMax → Groq → DeepSeek)."""
    text = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        system=_SBF_SYSTEM,
    )
    text = strip_think(text)
    # Strip reasoning preamble — some models (Kimi) dump thinking as plain text
    # before the actual content. Find the FIRST "1." that starts a news item
    # (preceded by emoji or start-of-line after blank line)
    # Strategy: find "1." lines, pick the one that looks like actual content
    candidates = list(re.finditer(r"^1[\.\)．]\s*", text, re.MULTILINE))
    if len(candidates) >= 2:
        # Multiple "1." — the LAST one is likely the real content start
        # (earlier ones are in the reasoning/prompt echo)
        best = candidates[-1]
        text = text[best.start():]
    elif len(candidates) == 1:
        m = candidates[0]
        if m.start() > 100:
            text = text[m.start():]
    return text


# ── Main entry point ──────────────────────────────────────────────────────────

async def generate_crypto_digest(api_key: str) -> list[dict]:
    """
    Fetch crypto news and return Telegram-ready message chunks.
    LLM call 1: top 10 picks + 300-word SBF analysis + deals section (main + deals sources)
    LLM call 2: Protos.com same-day summary
    Both LLM calls run in parallel via asyncio.gather.
    """
    logger.info("Starting crypto digest\u2026")
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        main_tasks = [
            _fetch_rss(session, name, url)
            for name, url in MAIN_FEEDS.items()
        ]
        deals_tasks = [
            _fetch_rss(session, name, url)
            for name, url in DEALS_FEEDS.items()
        ]
        protos_task = [_fetch_rss(session, "Protos", PROTOS_FEED)]
        results = await asyncio.gather(*(main_tasks + deals_tasks + protos_task))

    n_main  = len(MAIN_FEEDS)
    n_deals = len(DEALS_FEEDS)
    main_results  = results[:n_main]
    deals_results = results[n_main:n_main + n_deals]
    protos_articles = results[-1]

    # Collect all raw main articles (before dedup) for cross-source counting
    all_articles_raw: list[Article] = []
    for batch in main_results:
        all_articles_raw.extend(batch)

    # Jaccard dedup across CoinDesk/TheBlock/Cryptonews
    main_articles = _dedup_articles(all_articles_raw)
    logger.info(
        "Main sources: %d raw \u2192 %d after dedup",
        len(all_articles_raw), len(main_articles),
    )

    # Gap detection — find crypto stories our RSS feeds missed
    try:
        from gap_detector import detect_gaps
        crawled_headlines = [a.title for a in main_articles[:30]]
        gaps = await detect_gaps(crawled_headlines, category="crypto")
        if gaps:
            logger.info("Gap detector found %d missing crypto stories", len(gaps))
            for g in gaps:
                main_articles.append(Article(
                    title=g["title"],
                    summary=g.get("summary", ""),
                    url=g["url"],
                    source=g.get("source", "Google News"),
                ))
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Crypto gap detection failed: %s", e)

    # Collect funding articles: keyword-filtered from deals feeds + main feeds
    all_for_deals = list(main_articles)
    for batch in deals_results:
        all_for_deals.extend(batch)
    deal_articles = [a for a in all_for_deals if _is_funding_article(a)]

    logger.info("Deal articles (filtered, 2+ kw match): %d", len(deal_articles))
    logger.info("Protos: %d articles", len(protos_articles))

    loop = asyncio.get_event_loop()

    # Build prompts
    main_prompt = _build_main_prompt(main_articles, deal_articles, all_articles_raw)
    protos_prompt = _build_protos_prompt(protos_articles) if protos_articles else ""

    # Run both LLM calls in parallel via asyncio.gather
    async def _run_main():
        return await loop.run_in_executor(
            None, _call_llm, main_prompt, 1500
        )

    async def _run_protos():
        if not protos_prompt:
            return ""
        return await loop.run_in_executor(
            None, _call_llm, protos_prompt, 800
        )

    main_text, protos_text = await asyncio.gather(_run_main(), _run_protos())

    # Parse main_text into sections: News, SBF's Take, Deals & Raises
    from digest_ui import build_digest_message

    def _html_esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _preserve_links(t: str) -> str:
        """Escape HTML but preserve <a href="...">...</a> links."""
        link_pattern = r'<a\s+href="([^"]*)">(.*?)</a>'
        links = re.findall(link_pattern, t)
        placeholder_map = {}
        for idx, (href, text) in enumerate(links):
            placeholder = f"__LINK_PLACEHOLDER_{idx}__"
            original = f'<a href="{href}">{text}</a>'
            placeholder_map[placeholder] = (
                f'<a href="{_html_esc(href)}">{_html_esc(text)}</a>'
            )
            t = t.replace(original, placeholder, 1)
        t = _html_esc(t)
        for placeholder, link_html in placeholder_map.items():
            t = t.replace(placeholder, link_html)
        return t

    # Split main_text into news / take / deals
    news_part = main_text
    take_part = ""
    deals_part = ""

    take_match = re.search(r"\U0001f4a1\s*SBF.s Take:?", main_text)
    if take_match:
        news_part = main_text[:take_match.start()]
        rest = main_text[take_match.end():]
        deals_match = re.search(r"\U0001f4b0\s*DEALS\s*&?\s*RAISES:?", rest)
        if deals_match:
            take_part = rest[:deals_match.start()]
            deals_part = rest[deals_match.end():]
        else:
            take_part = rest
    else:
        deals_match = re.search(r"\U0001f4b0\s*DEALS\s*&?\s*RAISES:?", main_text)
        if deals_match:
            news_part = main_text[:deals_match.start()]
            deals_part = main_text[deals_match.end():]

    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    messages: list[dict] = [
        {"text": "\U0001f7e7" * 15},
        {"text": f"\U0001fa99 <b>CRYPTO DAILY \u2014 {today}</b>", "parse_mode": "HTML"},
    ]

    if news_part.strip():
        msg = build_digest_message("<b>\U0001f4f0 Top Stories</b>", _preserve_links(news_part))
        key = _dfb_key("crypto_news_" + today)
        msg["reply_markup"] = _dfb_buttons("crypto", key)
        messages.append(msg)
    if take_part.strip():
        messages.append(
            build_digest_message("<b>\U0001f4a1 SBF's Take</b>", _html_esc(take_part))
        )
    if protos_text:
        msg = build_digest_message("<b>\U0001f50d Protos Today</b>", _preserve_links(protos_text))
        key = _dfb_key("crypto_protos_" + today)
        msg["reply_markup"] = _dfb_buttons("crypto", key)
        messages.append(msg)
    if deals_part.strip():
        msg = build_digest_message(
            "<b>\U0001f4b0 Deals &amp; Raises</b>", _preserve_links(deals_part)
        )
        key = _dfb_key("crypto_deals_" + today)
        msg["reply_markup"] = _dfb_buttons("crypto", key)
        messages.append(msg)

    logger.info("Crypto digest complete.")
    return messages
