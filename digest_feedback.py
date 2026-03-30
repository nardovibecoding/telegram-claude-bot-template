# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Shared digest feedback — stores upvote/downvote votes for any pipeline.
Generic version of x_feedback.py, keyed by pipeline name.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

VOTES_FILE = Path(__file__).parent / "digest_votes.json"
MAX_VOTES_PER_PIPELINE = 200


def _load() -> dict:
    """Load {pipeline: {key: {vote, summary, ts}}}."""
    if VOTES_FILE.exists():
        try:
            return json.loads(VOTES_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    """Save votes, trimming each pipeline to MAX_VOTES_PER_PIPELINE."""
    for pipeline in list(data.keys()):
        entries = data[pipeline]
        if len(entries) > MAX_VOTES_PER_PIPELINE:
            # Keep most recent by timestamp
            sorted_keys = sorted(
                entries.keys(),
                key=lambda k: entries[k].get("ts", ""),
                reverse=True,
            )
            keep = set(sorted_keys[:MAX_VOTES_PER_PIPELINE])
            data[pipeline] = {k: v for k, v in entries.items() if k in keep}
    tmp = VOTES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(VOTES_FILE)


def make_key(text: str) -> str:
    """MD5 hash of text, first 12 chars."""
    return hashlib.md5(text.encode()).hexdigest()[:12]


def vote_buttons(pipeline: str, key: str) -> InlineKeyboardMarkup:
    """Build inline keyboard with thumbs up/down for a digest item."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("\U0001f44d", callback_data=f"dvote:up:{pipeline}:{key}"),
        InlineKeyboardButton("\U0001f44e", callback_data=f"dvote:down:{pipeline}:{key}"),
    ]])


def record_vote(pipeline: str, key: str, vote: str, summary: str) -> None:
    """Store a vote. vote = up or down."""
    data = _load()
    if pipeline not in data:
        data[pipeline] = {}
    data[pipeline][key] = {
        "vote": vote,
        "summary": summary[:200],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    _save(data)
    logger.info("Digest vote %s for %s/%s", vote, pipeline, key)


def get_preference_prompt(pipeline: str) -> str:
    """Build preference context string for a given pipeline."""
    data = _load()
    entries = data.get(pipeline, {})
    if not entries:
        return ""

    upvoted = [v["summary"] for v in entries.values() if v.get("vote") == "up"][-15:]
    downvoted = [v["summary"] for v in entries.values() if v.get("vote") == "down"][-15:]

    if not upvoted and not downvoted:
        return ""

    lines = [f"User preferences for {pipeline} based on past feedback:"]
    if upvoted:
        lines.append("LIKED (prioritise similar content):")
        for s in upvoted:
            lines.append(f"  + {s}")
    if downvoted:
        lines.append("DISLIKED (deprioritise similar content):")
        for s in downvoted:
            lines.append(f"  - {s}")
    return "\n".join(lines)
