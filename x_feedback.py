# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
X curator feedback — stores upvote/downvote votes and builds preference profile.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

VOTES_FILE = Path(__file__).parent / "x_votes.json"
MAX_VOTES   = 500  # keep last N votes


def _load() -> list:
    if VOTES_FILE.exists():
        try:
            return json.loads(VOTES_FILE.read_text())
        except Exception:
            return []
    return []


def _load_as_dict() -> dict[str, dict]:
    """Load votes into a dict keyed by vote key for O(1) lookup."""
    return {v["key"]: v for v in _load() if "key" in v}


def _save(votes: list) -> None:
    if len(votes) > MAX_VOTES:
        votes = votes[-MAX_VOTES:]
    fd, tmp = tempfile.mkstemp(dir=VOTES_FILE.parent, suffix=".tmp")
    try:
        os.write(fd, json.dumps(votes, ensure_ascii=False, indent=2).encode())
        os.close(fd)
        os.replace(tmp, VOTES_FILE)
    except:
        os.unlink(tmp); raise


def record_vote(key: str, url: str, author: str, summary: str, vote: int) -> None:
    """vote: +1 (upvote) or -1 (downvote)"""
    by_key = _load_as_dict()
    # Replace any existing vote for this key (O(1) dict update)
    by_key[key] = {
        "key":     key,
        "url":     url,
        "author":  author,
        "summary": summary,
        "vote":    vote,
        "ts":      datetime.now(timezone.utc).isoformat(),
    }
    _save(list(by_key.values()))
    logger.info("Recorded vote %+d for %s", vote, url)


def get_vote(key: str) -> int | None:
    """Return existing vote for key, or None. O(1) dict lookup."""
    by_key = _load_as_dict()
    entry = by_key.get(key)
    return entry.get("vote") if entry else None


def get_preference_prompt() -> str:
    """Build a preference context string to inject into the AI curation prompt."""
    votes = _load()
    upvoted   = [v["summary"] for v in votes if v.get("vote") == 1][-15:]
    downvoted = [v["summary"] for v in votes if v.get("vote") == -1][-15:]
    # Balance: neither side should exceed 2x the other
    if upvoted and downvoted:
        if len(downvoted) > len(upvoted) * 2:
            downvoted = downvoted[-(len(upvoted) * 2):]
        elif len(upvoted) > len(downvoted) * 2:
            upvoted = upvoted[-(len(downvoted) * 2):]

    if not upvoted and not downvoted:
        return ""

    lines = ["User preferences based on past feedback:"]
    if upvoted:
        lines.append("LIKED (prioritise similar content):")
        for s in upvoted:
            lines.append(f"  + {s}")
    if downvoted:
        lines.append("DISLIKED (deprioritise similar content):")
        for s in downvoted:
            lines.append(f"  - {s}")
    return "\n".join(lines)
