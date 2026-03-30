# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
X (Twitter) list digest — raw tweet text, per category, with expand/collapse UI.
Categories and accounts defined in x_list.json.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_ui import build_digest_message
from twitter_feed import fetch_tweets

logger = logging.getLogger(__name__)

X_LIST_FILE = Path(__file__).parent / "x_list.json"
LOOKBACK_HOURS = 24


def load_x_list() -> list[dict]:
    """Return list of category dicts from x_list.json."""
    if not X_LIST_FILE.exists():
        return []
    return json.loads(X_LIST_FILE.read_text()).get("categories", [])


def _format_tweet(tweet) -> str:
    ts = tweet.published.strftime("%H:%M") if tweet.published else "??"
    return f"{tweet.author} [{ts}]\n{tweet.text}\n{tweet.url}"


async def fetch_category(category: dict) -> dict:
    """
    Fetch tweets for one category.
    Returns {"name": str, "message": dict} ready for send_message(**message).
    """
    name     = category["name"]
    accounts = category.get("accounts", [])

    if not accounts:
        msg = build_digest_message(
            f"<b>{name}</b>",
            "No accounts configured for this category yet."
        )
        return {"name": name, "message": msg}

    tweets = await fetch_tweets(accounts)

    # Filter to lookback window
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    tweets = [t for t in tweets if t.published and t.published >= cutoff]
    tweets.sort(key=lambda t: t.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    if not tweets:
        msg = build_digest_message(
            f"<b>{name}</b>",
            f"No tweets in the last {LOOKBACK_HOURS}h."
        )
        return {"name": name, "message": msg}

    lines = [_format_tweet(t) for t in tweets]
    body  = "\n\n".join(lines)
    header = f"<b>{name}</b>  ({len(tweets)} tweets, last {LOOKBACK_HOURS}h)"
    msg = build_digest_message(header, body)
    return {"name": name, "message": msg}


async def generate_xlist_digest() -> list[dict]:
    """
    Fetch all categories in x_list.json.
    Returns list of {"name": str, "message": dict}.
    """
    categories = load_x_list()
    if not categories:
        return [{"name": "X List", "message": {"text": "x_list.json is empty or missing.", "reply_markup": None}}]

    results = []
    for cat in categories:
        try:
            result = await fetch_category(cat)
            results.append(result)
        except Exception as e:
            logger.error("x_feed error for category '%s': %s", cat.get("name"), e)
            results.append({
                "name": cat.get("name", "?"),
                "message": {"text": f"Error fetching {cat.get('name')}: {e}", "reply_markup": None}
            })
    return results
