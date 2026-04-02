# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Shared utilities for telegram-claude-bot-template.

Centralises commonly duplicated helpers:
  - strip_think()       — remove <think>…</think> blocks from MiniMax output
  - split_message()     — split long text for Telegram's 4096-char limit
  - retry_async()       — standardised async retry with exponential backoff + jitter
  - CLAUDE_BIN          — path to the claude CLI binary
  - PROJECT_DIR         — root directory of this project
"""

import asyncio
import logging
import os
import random
import re

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_default_claude = os.path.expanduser("~/.local/bin/claude")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", _default_claude)
PROJECT_DIR = os.environ.get(
    "TELEGRAM_BOT_PROJECT_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)


# ── Text helpers ─────────────────────────────────────────────────────────────

def strip_think(text: str) -> str:
    """Remove <think>...</think> blocks from LLM output (MiniMax reasoning)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def split_message(text: str, limit: int = 4096) -> list[str]:
    """Split a long message at line boundaries to fit Telegram's character limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Find last newline before the limit
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit  # force cut if no newline found
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ── Content drafts helper ─────────────────────────────────────────────────────

def save_to_content_drafts(text: str, category: str = "insight") -> str:
    """Append a tweet-worthy insight to content_drafts/running_log.md.

    Args:
        text:     The insight, discovery, number, or idea to save.
        category: One of: insight, result, code, number, journey, mistake.

    Returns:
        The absolute path to the log file (for confirmation).
    """
    from datetime import datetime
    log_path = os.path.join(PROJECT_DIR, "content_drafts", "running_log.md")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## [{category}] {ts}\n{text}\n"
    with open(log_path, "a") as f:
        f.write(entry)
    return log_path


# ── Retry helper ─────────────────────────────────────────────────────────────

async def retry_async(coro_fn, retries: int = 3, backoff: int = 2, jitter: bool = True):
    """
    Call an async callable with exponential backoff retry.

    Parameters
    ----------
    coro_fn : callable returning an awaitable
        Will be called as ``await coro_fn()`` on each attempt.
    retries : int
        Total number of attempts (including the first).
    backoff : int | float
        Base multiplier for wait time (attempt * backoff seconds).
    jitter : bool
        If True, add random jitter (0–1 s) to each wait.

    Returns the result of a successful call, or re-raises the last exception.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                wait = attempt * backoff
                if jitter:
                    wait += random.random()
                logger.warning(
                    "retry_async attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt, retries, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "retry_async failed after %d attempts: %s", retries, exc,
                )
    raise last_exc
