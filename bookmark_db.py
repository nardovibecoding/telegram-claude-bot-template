# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Bookmark-based taste profile for X curation bots.

Fetches user's Twitter bookmarks, stores them, and provides taste signals
to the curation pipeline as a soft boost (never a gate).

Bookmarks are auto-routed to bot categories:
  - "zh" for Chinese content
  - "ai" for AI-related content
  - "en" for English crypto content
  - "lists" shares the "en" pool
"""

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "bookmarks.db"
_db_initialized = False
# Minimum bookmarks before we generate taste signals
_MIN_BOOKMARKS = 3
# How many bookmarks to show in taste prompt
_TASTE_SAMPLE = 30

# Chinese content indicators (subset for classification)
_CN_INDICATORS = [
    "加密", "比特币", "以太坊", "币", "链", "挖矿", "质押", "空投",
    "合约", "杠杆", "清算", "稳定币", "代币", "公链", "跨链",
    "钱包", "交易所", "牛市", "熊市", "流动性", "治理", "协议",
    "幣", "鏈", "質押", "穩定幣", "代幣",
]

_AI_INDICATORS = [
    "openai", "anthropic", "claude", "chatgpt", "gpt-4", "gpt-5",
    "minimax", "gemini", "deepseek", "mistral", "llama",
    "transformer", "llm", "large language model", "ai agent",
    "machine learning", "deep learning", "neural network",
    "copilot", "cursor", "devin", "hugging face",
    "artificial intelligence", "generative ai",
    "人工智能", "大模型", "智能体", "生成式",
]


def _init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            tweet_id    TEXT PRIMARY KEY,
            author      TEXT,
            text        TEXT,
            url         TEXT,
            pub_date    TEXT,
            lang        TEXT,
            category    TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS taste_summaries (
            category    TEXT PRIMARY KEY,
            summary     TEXT,
            updated_at  TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bm_category ON bookmarks(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bm_created ON bookmarks(created_at DESC)")
    conn.commit()


def _classify_bookmark(text: str, tweet_lang: str) -> str:
    """Classify a bookmark into a bot category."""
    low = text.lower()
    if tweet_lang in ("zh", "zh-cn", "zh-tw", "ja") or any(kw in low for kw in _CN_INDICATORS):
        return "zh"
    if any(kw in low for kw in _AI_INDICATORS):
        return "ai"
    return "en"


def _get_conn() -> sqlite3.Connection:
    global _db_initialized
    conn = sqlite3.connect(str(DB_PATH))
    if not _db_initialized:
        _init_db(conn)
        _db_initialized = True
    return conn


# ── Fetch bookmarks from Twitter ─────────────────────────────────────────────

async def sync_bookmarks(api_key: str, max_pages: int = 10) -> int:
    """Fetch new bookmarks from Twitter and store them. Returns count of new bookmarks."""
    from x_curator import _load_client

    client = _load_client()
    conn = _get_conn()
    try:
        existing = {row[0] for row in conn.execute("SELECT tweet_id FROM bookmarks").fetchall()}

        new_count = 0
        rows_to_insert = []
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)

        try:
            bookmarks = await client.get_bookmarks(count=100)
        except Exception as e:
            logger.error("Failed to fetch bookmarks: %s", e)
            return 0

        for page in range(max_pages):
            if not bookmarks:
                break

            for tweet in bookmarks:
                tid = getattr(tweet, "id", "")
                if not tid or tid in existing:
                    continue

                created_at = getattr(tweet, "created_at", None)
                if created_at:
                    try:
                        if isinstance(created_at, str):
                            pub = datetime.strptime(created_at, "%a %b %d %H:%M:%S +0000 %Y").replace(tzinfo=timezone.utc)
                        else:
                            pub = created_at
                            if pub.tzinfo is None:
                                pub = pub.replace(tzinfo=timezone.utc)
                    except Exception:
                        pub = None
                else:
                    pub = None

                # Only bookmarks from 2026 onwards
                if pub and pub < cutoff:
                    continue

                user = getattr(tweet, "user", None)
                handle = f"@{user.screen_name}" if user else "@unknown"
                text = getattr(tweet, "text", "") or getattr(tweet, "full_text", "") or ""
                url = f"https://twitter.com/{handle.lstrip('@')}/status/{tid}" if tid else ""
                lang = getattr(tweet, "lang", None) or ""
                category = _classify_bookmark(text, lang)

                rows_to_insert.append((tid, handle, text, url, pub.isoformat() if pub else None, lang, category))
                existing.add(tid)
                new_count += 1

            try:
                bookmarks = await bookmarks.next()
            except Exception:
                break

        for row in rows_to_insert:
            conn.execute(
                "INSERT OR IGNORE INTO bookmarks (tweet_id, author, text, url, pub_date, lang, category) VALUES (?,?,?,?,?,?,?)",
                row,
            )
        conn.commit()
        logger.info("Bookmark sync: %d new bookmarks stored", new_count)
        return new_count
    finally:
        conn.close()


# ── Taste signals for curation ───────────────────────────────────────────────

def get_bookmark_count(category: str) -> int:
    conn = _get_conn()
    try:
        cats = _resolve_cats(category)
        placeholders = ",".join("?" * len(cats))
        count = conn.execute(f"SELECT COUNT(*) FROM bookmarks WHERE category IN ({placeholders})", cats).fetchone()[0]
        return count
    finally:
        conn.close()


def _resolve_cats(category: str) -> list[str]:
    if category == "lists":
        return ["en", "lists"]
    return [category]


def get_taste_prompt(category: str) -> str:
    """
    Build a taste section for the AI curation prompt from bookmarked tweets.
    Returns empty string if not enough bookmarks — curation works normally without it.
    """
    conn = _get_conn()
    try:
        cats = _resolve_cats(category)
        placeholders = ",".join("?" * len(cats))
        rows = conn.execute(
            f"SELECT author, text FROM bookmarks WHERE category IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
            (*cats, _TASTE_SAMPLE),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < _MIN_BOOKMARKS:
        return ""

    examples = []
    for author, text in rows:
        short = text[:200].replace("\n", " ")
        examples.append(f"  - {author}: {short}")
    example_block = "\n".join(examples)

    return (
        f"\n\nUSER TASTE PROFILE (from {len(rows)} bookmarked tweets — use as a SOFT preference, not a filter):\n"
        f"The user has bookmarked tweets like these. Tweets similar in topic/style should get a slight boost, "
        f"but do NOT exclude good tweets just because they don't match bookmarks.\n"
        f"{example_block}"
    )


async def update_taste_summary(category: str, api_key: str) -> str:
    """Generate and cache an AI taste summary from bookmarks."""
    conn = _get_conn()
    try:
        cats = _resolve_cats(category)
        placeholders = ",".join("?" * len(cats))
        rows = conn.execute(
            f"SELECT author, text FROM bookmarks WHERE category IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
            (*cats, _TASTE_SAMPLE),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < _MIN_BOOKMARKS:
        return ""

    examples = "\n".join(f"- {author}: {text[:300]}" for author, text in rows)

    oai = OpenAI(api_key=api_key, base_url="https://api.minimaxi.com/v1")
    loop = asyncio.get_event_loop()

    def _call():
        resp = oai.chat.completions.create(
            model="MiniMax-M2.5-highspeed",
            max_tokens=500,
            messages=[{"role": "user", "content": f"""Analyze these bookmarked tweets and describe the user's content preferences in 2-3 sentences. Focus on: topics, content style (threads vs short takes), preferred depth level, and any recurring themes.

{examples}

Reply with ONLY the preference description, nothing else."""}],
        )
        raw = resp.choices[0].message.content.strip()
        return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    try:
        summary = await loop.run_in_executor(None, _call)
    except Exception as e:
        logger.error("Taste summary generation failed: %s", e)
        return ""

    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO taste_summaries (category, summary, updated_at) VALUES (?, ?, ?)",
            (category, summary, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Updated taste summary for %s: %s", category, summary[:100])
    return summary
