#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""XHS (Xiaohongshu) 医美 digest: search keywords via XHS MCP REST API,
rank by engagement, dedup, send top picks to Telegram thread 3.

Usage:
  python xhs_digest.py          # daily auto (cron)
  python xhs_digest.py --force  # ignore sent flag

Requires: TELEGRAM_BOT_TOKEN_TWITTER, XHS MCP running at localhost:18060
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("xhs_digest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_TWITTER", "")
GROUP_ID = -1003827304557
XHS_THREAD = 3
KEYWORDS = ["上海面吸", "上海颌面", "上海眉弓peek", "成都颌面"]
PICKS_PER_KEYWORD = 3
XHS_MCP_BASE = "http://localhost:18060/api/v1"

HKT = timezone(timedelta(hours=8))
SENT_FLAG = str(BASE_DIR / ".xhs_digest_sent")
CACHE_FILE = str(BASE_DIR / ".xhs_digest_cache.json")


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


# ── XHS MCP REST client ──────────────────────────────────────────────────────
async def _check_login(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.get(f"{XHS_MCP_BASE}/login/status", timeout=10)
        data = r.json()
        return data.get("data", {}).get("is_logged_in", False)
    except Exception as e:
        log.error("Login check failed: %s", e)
        return False


async def _search_xhs(client: httpx.AsyncClient, keyword: str) -> list:
    """Call XHS MCP search endpoint, return list of feed dicts."""
    log.info("Searching XHS for: %s", keyword)
    try:
        r = await client.post(
            f"{XHS_MCP_BASE}/feeds/search",
            json={"keyword": keyword, "filters": {"sort_by": "最多点赞"}},
            timeout=60,
        )
        data = r.json()
        if data.get("code") != 0:
            log.warning("XHS search error for '%s': %s", keyword, data)
            return []
        feeds = data.get("data", {}).get("feeds", [])
        log.info("Got %d feeds for '%s'", len(feeds), keyword)
        return feeds
    except Exception as e:
        log.error("XHS search failed for '%s': %s", keyword, e)
        return []


# ── Ranking ──────────────────────────────────────────────────────────────────
def _engagement(feed: dict) -> int:
    """Calculate total engagement from XHS feed dict."""
    def si(val):
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    # MCP returns: liked_count, collected_count, comment_count, share_count
    # Also check interact_info nested dict
    info = feed.get("interact_info", {}) or {}
    liked = si(feed.get("liked_count") or info.get("liked_count", 0))
    collected = si(feed.get("collected_count") or info.get("collected_count", 0))
    comments = si(feed.get("comment_count") or info.get("comment_count", 0))
    shares = si(feed.get("share_count") or info.get("share_count", 0))
    return liked + collected + comments + shares


def _pick_top(feeds: list, cache: dict, n: int) -> list:
    """Pick top N unseen feeds by engagement."""
    seen = set(cache.get("seen", []))
    unseen = [f for f in feeds if f.get("note_id", f.get("id", "")) not in seen]
    unseen.sort(key=_engagement, reverse=True)
    return unseen[:n]


# ── Telegram formatting ─────────────────────────────────────────────────────
def _safe_int(val) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _format_post(feed: dict) -> str:
    """Format a single XHS feed as HTML card."""
    import html as html_mod

    info = feed.get("interact_info", {}) or {}

    title = feed.get("title", "") or feed.get("desc", "")[:80] or ""
    title = html_mod.escape(title[:80]) or "(untitled)"

    liked = _safe_int(feed.get("liked_count") or info.get("liked_count", 0))
    collected = _safe_int(feed.get("collected_count") or info.get("collected_count", 0))
    comments = _safe_int(feed.get("comment_count") or info.get("comment_count", 0))
    shares = _safe_int(feed.get("share_count") or info.get("share_count", 0))

    author = feed.get("user", {}) or {}
    nickname = html_mod.escape(author.get("nickname", "") or feed.get("nickname", "") or "")

    note_id = feed.get("note_id") or feed.get("id", "")
    clean_url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""

    desc = html_mod.escape((feed.get("desc", "") or "")[:120])

    stats = f"❤️ {liked}  ⭐ {collected}  💬 {comments}  🔁 {shares}"

    parts = [
        f"<b>{title}</b>",
        f"👤 {nickname}" if nickname else "",
        stats,
        f"{desc}" if desc and desc != title else "",
        f"🔗 <a href=\"{clean_url}\">View on XHS</a>" if clean_url else "",
    ]
    return "\n".join(p for p in parts if p)


# ── Auto QR on login failure ─────────────────────────────────────────────────
async def _auto_login_qr():
    """Auto-generate XHS QR and send to admin when login expired."""
    import aiohttp, json as _json, base64
    from io import BytesIO
    url = "http://localhost:18060/mcp"
    accept_hdr = {"Accept": "application/json, text/event-stream"}

    async def _mcp_post(session, payload, headers, timeout=30):
        r = await session.post(url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout))
        ct = r.headers.get("Content-Type", "")
        sid = r.headers.get("Mcp-Session-Id", "")
        if "event-stream" in ct:
            result = None
            text = await r.text()
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("data:"):
                    try:
                        result = _json.loads(line[5:].strip())
                    except _json.JSONDecodeError:
                        pass
            return result, sid
        elif "json" in ct:
            return await r.json(), sid
        else:
            return None, sid

    try:
        from telegram import Bot
        bot = Bot(token=BOT_TOKEN)

        async with aiohttp.ClientSession() as session:
            _, sid = await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "initialize", "id": 1,
                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                  "clientInfo": {"name": "digest", "version": "1.0"}}}, accept_hdr)
            headers = {**accept_hdr}
            if sid:
                headers["Mcp-Session-Id"] = sid
            await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "notifications/initialized",
                 "params": {}}, headers)
            result, _ = await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "tools/call", "id": 2,
                 "params": {"name": "get_login_qrcode", "arguments": {}}},
                headers, timeout=30)
            content = result["result"]["content"]
            for item in content:
                if item.get("type") == "text":
                    try:
                        info = _json.loads(item["text"])
                        img_b64 = info.get("img", "")
                        if img_b64:
                            if img_b64.startswith("data:"):
                                img_b64 = img_b64.split(",", 1)[1]
                            qr_data = base64.b64decode(img_b64)
                            if len(qr_data) > 100:
                                qr_file = BytesIO(qr_data)
                                qr_file.name = "xhs_qr.bmp"
                                await bot.send_document(
                                    chat_id=GROUP_ID,
                                    message_thread_id=XHS_THREAD,
                                    document=qr_file,
                                    caption="⚠️ XHS login expired. Scan with 小红书 app to re-login."
                                )
                                log.info("Auto QR sent to admin group")
                                return
                    except Exception:
                        pass
    except Exception as e:
        log.warning("Auto QR generation failed: %s", e)


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
        log.error("No TELEGRAM_BOT_TOKEN_TWITTER")
        return

    async with httpx.AsyncClient() as client:
        # Check login
        if not await _check_login(client):
            log.error("XHS not logged in — attempting auto QR generation")
            await _send_telegram(
                BOT_TOKEN, GROUP_ID, XHS_THREAD,
                f"💉 <b>XHS 医美 Digest — {today}</b>\n{'─'*30}\n\n"
                f"⚠️ <b>XHS not logged in.</b>\nRun /xhslogin to re-authenticate, then /xhs_digest --force."
            )
            # Try to auto-generate QR and send to admin
            await _auto_login_qr()
            return

        cache = _load_cache()
        all_sections = []

        for keyword in KEYWORDS:
            try:
                feeds = await _search_xhs(client, keyword)
                if not feeds:
                    log.warning("No results for '%s'", keyword)
                    all_sections.append(f"🔍 <b>{keyword}</b> (0 results)\n")
                    continue

                picks = _pick_top(feeds, cache, PICKS_PER_KEYWORD)
                if not picks:
                    log.info("No new posts for '%s' (all seen)", keyword)
                    all_sections.append(f"🔍 <b>{keyword}</b> (all seen)\n")
                    continue

                section = f"🔍 <b>{keyword}</b> ({len(picks)} picks)\n\n"
                for i, feed in enumerate(picks, 1):
                    section += f"{i}. {_format_post(feed)}\n\n"
                    note_id = feed.get("note_id") or feed.get("id", "")
                    if note_id:
                        cache.setdefault("seen", []).append(note_id)

                all_sections.append(section)

            except Exception as e:
                log.error("Error processing keyword '%s': %s", keyword, e)
                all_sections.append(f"🔍 <b>{keyword}</b> (❌ error)\n")

    _save_cache(cache)

    header = f"💉 <b>XHS 医美 Digest — {today}</b>\n{'─'*30}\n\n"
    full_text = header + "\n".join(all_sections)

    for chunk in _split_message(full_text, 4000):
        await _send_telegram(BOT_TOKEN, GROUP_ID, XHS_THREAD, chunk)
        await asyncio.sleep(1)

    log.info("Sent XHS digest with %d keyword sections", len(KEYWORDS))

    with open(SENT_FLAG, "a") as f:
        f.write(today + "\n")


async def _send_telegram(token: str, chat_id: int, thread_id: int, text: str):
    from telegram import Bot
    bot = Bot(token=token)
    try:
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.error("Telegram send failed: %s", e)


def _split_message(text: str, limit: int = 4000) -> list:
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
