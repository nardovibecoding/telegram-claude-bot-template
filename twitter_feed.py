# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Twitter/X feed fetcher.
Uses twikit (no official API key — uses Twitter web client).
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from llm_client import chat_completion_async

logger = logging.getLogger(__name__)

COOKIES_FILE = str(Path(__file__).parent / "twitter_cookies.json")

_ELON_SYSTEM = (
    "You are Elon Musk. Terse. Blunt. Meme-fluent. First principles thinker. "
    "You speak like you tweet: short punchy sentences, occasional emojis, "
    "provocative takes, dry humour. Bias for free speech and disruption. "
    "You say what you think with zero filter."
)


@dataclass
class Tweet:
    author: str
    text: str
    url: str
    published: Optional[datetime] = None


# ── twikit primary ─────────────────────────────────────────────────────────────
# Uses cookies_file so login only happens once; subsequent calls load from file.

_twikit_client = None


async def _get_twikit_client():
    global _twikit_client
    if _twikit_client is not None:
        return _twikit_client
    try:
        from twikit import Client
    except ImportError:
        logger.warning("twikit not installed")
        return None

    if not Path(COOKIES_FILE).exists():
        logger.warning("No twitter_cookies.json found. Export cookies from browser to enable twikit.")
        return None

    try:
        client = Client("en-US")
        client.load_cookies(COOKIES_FILE)
        logger.info("twikit ready (loaded cookies from file)")
        _twikit_client = client
        return client
    except Exception as e:
        logger.warning("twikit cookie load failed: %s", e)
        return None


async def _fetch_twikit(accounts: list[str]) -> list[Tweet]:
    client = await _get_twikit_client()
    if client is None:
        return []

    tweets: list[Tweet] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    for account in accounts:
        handle = account.lstrip("@")
        try:
            user = await client.get_user_by_screen_name(handle)
            user_tweets = await user.get_tweets("Tweets", count=20)
            count = 0
            for t in user_tweets:
                pub = None
                if hasattr(t, "created_at") and t.created_at:
                    try:
                        pub = datetime.strptime(
                            t.created_at, "%a %b %d %H:%M:%S +0000 %Y"
                        ).replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
                if pub and pub < cutoff:
                    continue
                text = getattr(t, "full_text", None) or getattr(t, "text", "") or ""
                tweets.append(Tweet(
                    author=f"@{handle}",
                    text=text.strip(),
                    url=f"https://twitter.com/{handle}/status/{t.id}",
                    published=pub,
                ))
                count += 1
            logger.info("twikit @%s → %d tweets", handle, count)
        except Exception as e:
            logger.warning("twikit fetch @%s: %s — resetting client", handle, e)
            _twikit_client = None
    return tweets


# ── Unified fetch ──────────────────────────────────────────────────────────────

async def fetch_tweets(accounts: list[str]) -> list[Tweet]:
    """Fetch recent tweets via twikit."""
    return await _fetch_twikit(accounts)


# ── AI screening ───────────────────────────────────────────────────────────────


def _build_prompt(tweets: list[Tweet], accounts: list[str]) -> str:
    lines = []
    for t in tweets[:60]:
        ts = t.published.strftime("%H:%M") if t.published else "??"
        lines.append(f"[{t.author} {ts}] {t.text}")
    content = "\n".join(lines)
    accounts_str = ", ".join(f"@{a.lstrip('@')}" for a in accounts)
    return f"""Latest tweets from {accounts_str} in the last 24h:

{content}

Pick the 8-10 most interesting. For each: quote the tweet, then give your Elon-style hot take in 1-2 sentences.
End with "Elon's Verdict" — 2-3 sentences on the overall picture.

Format:
> [tweet text]
[your take]

...

Elon's Verdict:
[2-3 sentences]"""


async def generate_twitter_digest(api_key: str, accounts: list[str]) -> list[str]:
    """Fetch tweets and return Telegram-ready message strings."""
    logger.info("Twitter digest for: %s", accounts)
    tweets = await fetch_tweets(accounts)

    if not tweets:
        return ["No recent tweets found. Either nothing posted in 24h or the feed is down."]

    prompt = _build_prompt(tweets, accounts)

    try:
        result = await chat_completion_async(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            system=_ELON_SYSTEM,
        )
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return [f"Error generating digest: {e}"]

    today  = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    header = (
        f"Twitter Screen — {today}\n"
        f"Accounts: {', '.join('@' + a.lstrip('@') for a in accounts)}"
    )

    chunks = [header]
    remaining = result
    while remaining:
        if len(remaining) <= 4096:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, 4096)
        if cut <= 0:
            cut = 4096
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")

    return chunks
