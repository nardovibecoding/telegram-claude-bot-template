# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Gap Detector — find stories our RSS feeds missed.

Cross-references crawled headlines against Google News trending RSS
to identify coverage gaps. Returns missing stories for pipeline inclusion.

Usage:
    from gap_detector import detect_gaps

    gaps = await detect_gaps(
        crawled_headlines=["Fed raises rates...", "Oil prices surge..."],
        category="world",
    )
    # gaps = [{"title": ..., "url": ..., "source": ..., "summary": ...}, ...]
"""

import asyncio
import logging
import re
from html import unescape
from xml.etree.ElementTree import ParseError

import aiohttp
import feedparser

logger = logging.getLogger(__name__)

# ── Google News RSS by category ───────────────────────────────────────────────

GNEWS_URLS: dict[str, str] = {
    "world":      "https://news.google.com/rss?topic=WORLD&hl=en",
    "business":   "https://news.google.com/rss?topic=BUSINESS&hl=en",
    "technology": "https://news.google.com/rss?topic=TECHNOLOGY&hl=en",
    "crypto":     "https://news.google.com/rss/search?q=cryptocurrency&hl=en",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

MAX_GAP_STORIES = 5
SIMILARITY_THRESHOLD = 0.2  # Jaccard below this = no match


# ── Jaccard similarity ────────────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, split into word tokens."""
    text = re.sub(r"[^\w\s]", "", text.lower())
    return set(text.split())


def _jaccard(a: str, b: str) -> float:
    """Jaccard similarity between two strings."""
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _has_match(candidate: str, headlines: list[str], threshold: float = SIMILARITY_THRESHOLD) -> bool:
    """Check if candidate headline matches any existing headline."""
    for h in headlines:
        if _jaccard(candidate, h) >= threshold:
            return True
    return False


# ── Fetch Google News RSS ─────────────────────────────────────────────────────

async def _fetch_gnews(category: str, timeout: int = 15) -> list[dict]:
    """Fetch and parse Google News RSS for a category.

    Returns list of {"title": ..., "url": ..., "source": ...}.
    """
    url = GNEWS_URLS.get(category)
    if not url:
        logger.warning("Unknown category %r, falling back to 'world'", category)
        url = GNEWS_URLS["world"]

    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    logger.error("Google News RSS returned %d for %s", resp.status, category)
                    return []
                raw = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error("Failed to fetch Google News RSS for %s: %s", category, e)
        return []

    feed = feedparser.parse(raw)
    results = []
    for entry in feed.entries[:30]:  # Top 30 from Google News
        title = unescape(entry.get("title", "")).strip()
        link = entry.get("link", "")
        # Google News titles often end with " - Source Name"
        source = ""
        if " - " in title:
            parts = title.rsplit(" - ", 1)
            title = parts[0].strip()
            source = parts[1].strip()
        results.append({"title": title, "url": link, "source": source})

    logger.info("Fetched %d headlines from Google News (%s)", len(results), category)
    return results


# ── Core gap detection ────────────────────────────────────────────────────────

async def detect_gaps(
    crawled_headlines: list[str],
    category: str = "world",
) -> list[dict]:
    """Find stories our feeds missed.

    Args:
        crawled_headlines: list of headline strings from our RSS feeds.
        category: 'world', 'business', 'technology', 'crypto'.

    Returns:
        list of {"title", "url", "source", "summary"} for missing stories.
        Maximum MAX_GAP_STORIES items.
    """
    if not crawled_headlines:
        logger.warning("No crawled headlines provided — skipping gap detection")
        return []

    # 1. Fetch Google News trending for same category
    gnews = await _fetch_gnews(category)
    if not gnews:
        logger.warning("No Google News results for %s — skipping gap detection", category)
        return []

    # 2. Compare: find stories with NO match in our headlines
    gaps = []
    for item in gnews:
        if not _has_match(item["title"], crawled_headlines):
            gaps.append(item)

    logger.info(
        "Gap detection [%s]: %d Google News headlines, %d crawled, %d gaps found",
        category, len(gnews), len(crawled_headlines), len(gaps),
    )

    # 3. Cap at MAX_GAP_STORIES
    gaps = gaps[:MAX_GAP_STORIES]

    # 4. Add empty summary (caller can enrich later via LLM)
    for g in gaps:
        g.setdefault("summary", "")

    return gaps


# ── Standalone test ───────────────────────────────────────────────────────────

async def _test():
    """Quick test: detect gaps against a dummy set of headlines."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sample_headlines = [
        "Trump announces new tariffs on Chinese goods",
        "Fed holds interest rates steady amid inflation concerns",
        "Ukraine counteroffensive gains momentum in east",
        "OpenAI releases GPT-5 with advanced reasoning",
        "Bitcoin hits new all-time high above $100,000",
        "EU passes landmark AI regulation bill",
        "Apple unveils new MacBook Pro with M5 chip",
        "Oil prices surge after OPEC production cuts",
        "Tesla recalls 500,000 vehicles over safety issue",
        "Japan earthquake triggers tsunami warning",
    ]

    for cat in ("world", "business", "technology", "crypto"):
        print(f"\n{'='*60}")
        print(f"Category: {cat}")
        print(f"{'='*60}")
        gaps = await detect_gaps(sample_headlines, category=cat)
        for i, g in enumerate(gaps, 1):
            print(f"  {i}. [{g['source']}] {g['title']}")
            print(f"     {g['url']}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(_test())
