# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Shared expand/collapse UI for digest messages."""
import hashlib
import json
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent / "digest_content_cache.json"


# ── Cache ────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    # Keep at most 500 entries
    keys = list(cache.keys())
    if len(keys) > 500:
        for k in keys[:-500]:
            del cache[k]
    # Atomic write to prevent corruption from concurrent processes
    tmp = _CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False))
    tmp.replace(_CACHE_PATH)


def store_content(key: str, collapsed: str, expanded: str) -> None:
    # Re-read cache each time to merge with other processes' writes
    cache = _load_cache()
    cache[key] = {"c": collapsed, "e": expanded}
    _save_cache(cache)


def get_content(key: str) -> tuple[str, str] | None:
    """Return (collapsed, expanded) or None."""
    cache = _load_cache()
    entry = cache.get(key)
    if entry:
        return entry["c"], entry["e"]
    return None


# ── Message builders ─────────────────────────────────────────────────────

def make_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


def expand_button(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("展開 ▼", callback_data=f"dex:{key}")
    ]])


def collapse_button(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("收起 ▲", callback_data=f"dcl:{key}")
    ]])


def build_digest_message(header: str, body: str) -> dict:
    """
    Build a digest message dict ready for sending.
    Uses Telegram's native expandable blockquote — no server dependency.
    """
    full = f"{header}\n<blockquote expandable>{body}</blockquote>"
    # Hard cap at 4096
    if len(full) > 4096:
        # Trim body inside blockquote
        max_body = 4096 - len(header) - len("\n<blockquote expandable></blockquote>") - 5
        full = f"{header}\n<blockquote expandable>{body[:max_body]}…</blockquote>"
    return {
        "text": full,
        "parse_mode": "HTML",
    }


# ── Callback handler (add to any bot that sends digests) ────────────────

async def handle_digest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle expand/collapse button presses."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    if not data.startswith(("dex:", "dcl:")):
        return

    action, key = data[:3], data[4:]
    content = get_content(key)
    if not content:
        await query.answer("內容已過期")
        return

    collapsed, expanded = content

    try:
        if action == "dex":
            await query.edit_message_text(
                text=expanded,
                parse_mode="HTML",
                reply_markup=collapse_button(key),
            )
        else:
            await query.edit_message_text(
                text=collapsed,
                parse_mode="HTML",
                reply_markup=expand_button(key),
            )
        await query.answer()
    except Exception as e:
        logger.warning("Digest callback error: %s", e)
        await query.answer("Error")
