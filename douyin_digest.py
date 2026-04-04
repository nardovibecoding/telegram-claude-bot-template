#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Douyin 医美 digest: search keywords via MediaCrawler,
rank by engagement, dedup, send top picks to Telegram thread 2.

Usage:
  python douyin_digest.py          # daily auto (cron)
  python douyin_digest.py --force  # ignore sent flag

Requires: TELEGRAM_BOT_TOKEN_XCN, MediaCrawler at ~/MediaCrawler/
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("douyin_digest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_XCN", "")
GROUP_ID = -1003827304557
DOUYIN_THREAD = 2
KEYWORDS = ["上海面吸", "上海颌面", "上海眉弓peek", "成都颌面"]
PICKS_PER_KEYWORD = 3

HKT = timezone(timedelta(hours=8))
SENT_FLAG = str(BASE_DIR / ".douyin_digest_sent")
CACHE_FILE = str(BASE_DIR / ".douyin_digest_cache.json")
MEDIACRAWLER_DIR = os.path.expanduser("~/MediaCrawler")
MEDIACRAWLER_VENV = os.path.join(MEDIACRAWLER_DIR, "venv", "bin", "activate")


# ── Cache ────────────────────────────────────────────────────────────────────
def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"seen": []}


def _save_cache(cache: dict):
    seen = cache.get("seen", [])
    if len(seen) > 500:
        cache["seen"] = seen[-300:]
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)


# ── MediaCrawler runner ─────────────────────────────────────────────────────
def _run_mediacrawler(keyword: str) -> list:
    """Run MediaCrawler for a single keyword, parse JSONL output."""
    log.info("Running MediaCrawler for Douyin keyword: %s", keyword)

    import platform as plat
    is_linux = plat.system() == "Linux"
    xvfb_prefix = "xvfb-run -a " if is_linux else ""
    cmd = (
        f"source {MEDIACRAWLER_VENV} && "
        f"cd {MEDIACRAWLER_DIR} && "
        f"{xvfb_prefix}python main.py --platform dy --type search "
        f"--keywords \"{keyword}\" --get_comment false --headless true"
    )

    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            log.warning("MediaCrawler exit %d for '%s': %s",
                        result.returncode, keyword, result.stderr[-500:] if result.stderr else "")
    except subprocess.TimeoutExpired:
        log.error("MediaCrawler timeout for '%s'", keyword)
        return []
    except Exception as e:
        log.error("MediaCrawler failed for '%s': %s", keyword, e)
        return []

    # Find today's JSONL output
    today_str = datetime.now(HKT).strftime("%Y-%m-%d")
    jsonl_dir = os.path.join(MEDIACRAWLER_DIR, "data", "douyin", "jsonl")
    if not os.path.isdir(jsonl_dir):
        log.warning("No JSONL dir: %s", jsonl_dir)
        return []

    posts = []
    for fname in os.listdir(jsonl_dir):
        if not fname.endswith(".jsonl"):
            continue
        if "contents" not in fname:
            continue
        if today_str not in fname:
            continue
        fpath = os.path.join(jsonl_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        # Only include items from this keyword
                        if item.get("source_keyword", "") == keyword:
                            posts.append(item)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            log.warning("Error reading %s: %s", fpath, e)

    log.info("Found %d Douyin posts for '%s'", len(posts), keyword)
    return posts


# ── Ranking ──────────────────────────────────────────────────────────────────
def _engagement(post: dict) -> int:
    """Calculate total engagement: likes + comments + shares."""
    liked = _safe_int(post.get("liked_count", 0))
    comments = _safe_int(post.get("comment_count", 0))
    shares = _safe_int(post.get("share_count", 0))
    collected = _safe_int(post.get("collected_count", 0))
    return liked + comments + shares + collected


def _safe_int(val) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _pick_top(posts: list, cache: dict, n: int) -> list:
    """Pick top N unseen posts by engagement."""
    seen = set(cache.get("seen", []))
    unseen = [p for p in posts if p.get("aweme_id", "") not in seen]
    unseen.sort(key=_engagement, reverse=True)
    return unseen[:n]


# ── Telegram formatting ─────────────────────────────────────────────────────
def _format_post(post: dict) -> str:
    """Format a single Douyin post as HTML card."""
    import html as html_mod

    title = html_mod.escape(post.get("title", "") or post.get("desc", "")[:80])
    if not title:
        title = "(untitled)"

    liked = _safe_int(post.get("liked_count", 0))
    collected = _safe_int(post.get("collected_count", 0))
    comments = _safe_int(post.get("comment_count", 0))
    shares = _safe_int(post.get("share_count", 0))
    nickname = html_mod.escape(post.get("nickname", ""))
    aweme_url = post.get("aweme_url", "")

    # Fallback URL from aweme_id
    aweme_id = post.get("aweme_id", "")
    if not aweme_url and aweme_id:
        aweme_url = f"https://www.douyin.com/video/{aweme_id}"

    desc = html_mod.escape((post.get("desc", "") or "")[:120])

    stats = f"\u2764\ufe0f {liked}  \u2b50 {collected}  \U0001f4ac {comments}  \U0001f501 {shares}"

    parts = [
        f"<b>{title[:60]}</b>",
        f"\U0001f464 {nickname}" if nickname else "",
        stats,
        f"{desc}" if desc and desc != title else "",
        f"\U0001f517 <a href=\"{aweme_url}\">View on Douyin</a>" if aweme_url else "",
    ]
    return "\n".join(p for p in parts if p)


# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    force = "--force" in sys.argv

    # Sent flag check
    if not force and os.path.exists(SENT_FLAG):
        with open(SENT_FLAG) as f:
            if today in f.read():
                log.info("Already ran for %s", today)
                return

    if not BOT_TOKEN:
        log.error("No TELEGRAM_BOT_TOKEN_XCN")
        return

    cache = _load_cache()
    all_messages = []

    for keyword in KEYWORDS:
        try:
            posts = _run_mediacrawler(keyword)
            if not posts:
                log.warning("No posts for '%s'", keyword)
                all_messages.append(f"\U0001f50d <b>{keyword}</b> (0 results)\n")
                continue

            picks = _pick_top(posts, cache, PICKS_PER_KEYWORD)
            if not picks:
                log.info("No new posts for '%s' (all seen)", keyword)
                all_messages.append(f"\U0001f50d <b>{keyword}</b> (all seen)\n")
                continue

            # Build section
            section = f"\U0001f50d <b>{keyword}</b> ({len(picks)} picks)\n\n"
            for i, post in enumerate(picks, 1):
                section += f"{i}. {_format_post(post)}\n\n"
                # Mark as seen
                aweme_id = post.get("aweme_id", "")
                if aweme_id:
                    cache.setdefault("seen", []).append(aweme_id)

            all_messages.append(section)

        except Exception as e:
            log.error("Error processing keyword '%s': %s", keyword, e)
            all_messages.append(f"\U0001f50d <b>{keyword}</b> (\u274c error)\n")

    _save_cache(cache)

    if not all_messages:
        log.info("No messages to send")
        with open(SENT_FLAG, "a") as f:
            f.write(today + "\n")
        return

    # Send to Telegram
    from telegram import Bot
    bot = Bot(token=BOT_TOKEN)

    header = (
        f"\U0001f3ac <b>Douyin \u533b\u7f8e Digest \u2014 {today}</b>\n"
        f"{'─' * 30}\n\n"
    )

    full_text = header + "\n".join(all_messages)

    # Split if needed (TG limit 4096)
    chunks = _split_message(full_text, 4000)

    for chunk in chunks:
        try:
            await bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=DOUYIN_THREAD,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await asyncio.sleep(1)
        except Exception as e:
            log.error("Telegram send failed: %s", e)

    log.info("Sent Douyin digest with %d keyword sections", len(KEYWORDS))

    with open(SENT_FLAG, "a") as f:
        f.write(today + "\n")


def _split_message(text: str, limit: int = 4000) -> list:
    """Split long text for Telegram."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


if __name__ == "__main__":
    asyncio.run(main())
