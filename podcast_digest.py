#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Podcast digest: fetch episodes from RSSHub (小宇宙) + Podscan,
transcribe via Groq Whisper, summarize via AI, send to Telegram.

Usage:
  python podcast_digest.py                 # daily auto (all subscribed podcasts)
  python podcast_digest.py <podcast_id>    # specific 小宇宙 podcast
  python podcast_digest.py --search "AI agent"  # search Podscan

Requires: GROQ_API_KEY, TELEGRAM_BOT_TOKEN_ADMIN in .env
RSSHub: http://localhost:1200 on VPS"""

import asyncio
import json
import logging
import os
import re
import sys
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from digest_feedback import make_key as _dfb_key, vote_buttons as _dfb_buttons

log = logging.getLogger("podcast_digest")

from llm_client import chat_completion_async
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
AI_WORLD_GROUP = -1003892866004
_CN_THREAD_CACHE = str(BASE_DIR / ".podcast_cn_ai_world_thread_id")
_EN_THREAD_CACHE = str(BASE_DIR / ".podcast_en_ai_world_thread_id")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
HKT = timezone(timedelta(hours=8))
RSSHUB_BASE = "http://localhost:1200"
SENT_FLAG_CN = str(BASE_DIR / ".podcast_digest_sent")
SENT_FLAG_EN = str(BASE_DIR / ".podcast_en_digest_sent")
CACHE_FILE = str(BASE_DIR / ".podcast_cache.json")


async def _get_podcast_thread(bot, en_mode: bool) -> int:
    """Return AI World podcast thread, creating it on first run."""
    cache = _EN_THREAD_CACHE if en_mode else _CN_THREAD_CACHE
    if os.path.exists(cache):
        with open(cache) as f:
            return int(f.read().strip())
    name = "🎙 Podcasts" if en_mode else "🎙 小宇宙"
    topic = await bot.create_forum_topic(chat_id=AI_WORLD_GROUP, name=name)
    tid = topic.message_thread_id
    with open(cache, "w") as f:
        f.write(str(tid))
    return tid

# 小宇宙 podcasts (Chinese)
CN_PODCASTS = {
    "6013f9f58e2f7ee375cf4216": "知行小酒馆",
    "61791d921989541784257779": "硅谷101",
    "6175dbfb21e1f50d0b2faeca": "42章经",
    "62afce6c7cb4e50e36c50544": "张小珺Jùn",
    "6448e5c02a4a7a7e18e02a70": "AI局内人",
}

# English podcasts (RSS feeds — AI, business, celebrity interviews)
EN_PODCASTS = {
    "https://feeds.simplecast.com/54nAGcIl": "Lex Fridman",
    "https://feeds.megaphone.fm/all-in": "All-In Podcast",
    "https://api.substack.com/feed/podcast/11524.rss": "Lenny's Podcast",
    "https://feeds.transistor.fm/no-priors": "No Priors",
    "https://feeds.simplecast.com/l2i9YnTd": "Latent Space",
    "https://feeds.megaphone.fm/20vc": "20VC Harry Stebbings",
    "https://feeds.simplecast.com/JGE3yC0V": "My First Million",
    "https://entrepreneurshandbook.co/feed": "Naval Podcast",
}

MAX_AUDIO_DURATION = 7200  # 2 hours


def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"seen": []}


def _save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)


async def _fetch(url, timeout=30):
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                             headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status == 200:
                    return await r.text()
    except Exception as e:
        log.warning("Fetch %s: %s", url, e)
    return ""


async def _download_audio(url):
    """Download audio and re-encode to mono 24kbps for Groq Whisper 25MB limit.

    Strategy: download full file, then re-encode with ffmpeg to mono 24kbps.
    A 120-min podcast at 24kbps mono = ~21MB, well under the 25MB limit.
    Falls back to 24MB truncation if ffmpeg is not available.
    """
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=300),
                             headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status != 200:
                    return None

                tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                async for chunk in r.content.iter_chunked(8192):
                    tmp.write(chunk)
                tmp.close()
                raw_size = os.path.getsize(tmp.name)
                log.info("Downloaded %.1fMB audio", raw_size / 1024 / 1024)

                # If already under 25MB, no re-encoding needed
                if raw_size <= 25 * 1024 * 1024:
                    return tmp.name

                # Try re-encoding with ffmpeg to mono 24kbps
                ffmpeg_bin = shutil.which("ffmpeg") or os.path.expanduser("~/bin/ffmpeg")
                if not os.path.isfile(ffmpeg_bin):
                    # Fallback: truncate to 24MB (old behavior)
                    log.warning("ffmpeg not found, truncating to 24MB")
                    with open(tmp.name, "r+b") as f:
                        f.truncate(24 * 1024 * 1024)
                    return tmp.name

                encoded = tmp.name.replace(".mp3", "_enc.mp3")
                try:
                    proc = await asyncio.to_thread(
                        subprocess.run,
                        [ffmpeg_bin, "-i", tmp.name, "-ac", "1", "-b:a", "24k",
                         "-y", encoded],
                        capture_output=True, timeout=180,
                    )
                    if proc.returncode == 0 and os.path.exists(encoded):
                        enc_size = os.path.getsize(encoded)
                        log.info("Re-encoded: %.1fMB -> %.1fMB (mono 24kbps)",
                                 raw_size / 1024 / 1024, enc_size / 1024 / 1024)
                        os.unlink(tmp.name)
                        return encoded
                    else:
                        log.warning("ffmpeg failed (rc=%d), truncating to 24MB", proc.returncode)
                        log.warning("ffmpeg stderr: %s", proc.stderr[:200] if proc.stderr else "")
                        with open(tmp.name, "r+b") as f:
                            f.truncate(24 * 1024 * 1024)
                        return tmp.name
                except subprocess.TimeoutExpired:
                    log.warning("ffmpeg timed out (180s), truncating to 24MB")
                    with open(tmp.name, "r+b") as f:
                        f.truncate(24 * 1024 * 1024)
                    try:
                        os.unlink(encoded)
                    except OSError:
                        pass
                    return tmp.name
    except Exception as e:
        log.warning("Download failed: %s", e)
    return None


async def _transcribe_groq(audio_path, lang="zh"):
    """Transcribe audio using Groq Whisper API."""
    import aiohttp
    if not GROQ_API_KEY:
        log.error("No GROQ_API_KEY")
        return ""

    try:
        data = aiohttp.FormData()
        with open(audio_path, "rb") as audio_file:
            data.add_field("file", audio_file,
                           filename="audio.mp3", content_type="audio/mpeg")
            data.add_field("model", "whisper-large-v3-turbo")
            data.add_field("language", lang)

            async with aiohttp.ClientSession() as s:
                async with s.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as r:
                    if r.status == 200:
                        result = await r.json()
                        return result.get("text", "")
                    else:
                        err = await r.text()
                        log.error("Groq error %d: %s", r.status, err[:200])
    except Exception as e:
        log.error("Transcribe failed: %s", e)
    return ""


async def _summarize(title, transcript, en_mode=False):
    """Summarize transcript using LLM with fallback chain."""
    if en_mode:
        prompt = (
            f"This is a podcast transcript. Summarize the key points in English:\n\n"
            f"Title: {title}\n\n"
            f"Transcript: {transcript[:8000]}\n\n"
            f"Requirements:\n"
            f"1. 3-5 key takeaways, each 1-2 sentences\n"
            f"2. One-line verdict: worth listening or skip?\n"
            f"3. Focus on AI, business, making money, and future predictions\n"
            f"4. Keep it concise, max 500 words"
        )
    else:
        prompt = (
            f"這是一集播客的轉錄文字。請用繁體中文總結重點：\n\n"
            f"標題：{title}\n\n"
            f"轉錄：{transcript[:8000]}\n\n"
            f"要求：\n"
            f"1. 用 3-5 個要點總結核心內容\n"
            f"2. 每個要點 1-2 句\n"
            f"3. 最後一句話總結：這集值不值得聽\n"
            f"4. 重點講 AI、搞錢做生意、未來預測相關嘞 insight\n"
            f"5. 保持簡潔，唔好超過 500 字"
        )
    text = await chat_completion_async(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        timeout=45,
    )
    if text.startswith("⚠️"):
        log.error("Summarize failed: %s", text)
        return ""
    return text

async def _fetch_xiaoyuzhou_episodes(podcast_id, podcast_name):
    """Fetch latest episodes from 小宇宙 via RSSHub."""
    rss = await _fetch(f"{RSSHUB_BASE}/xiaoyuzhou/podcast/{podcast_id}")
    if not rss:
        return []

    episodes = []
    try:
        root = ET.fromstring(rss)
        for item in root.findall(".//item")[:3]:  # Latest 3
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            # Get audio URL from enclosure
            enc = item.find("enclosure")
            audio_url = enc.get("url", "") if enc is not None else ""
            pub_date = item.findtext("pubDate", "")

            if title and audio_url:
                episodes.append({
                    "title": title,
                    "link": link,
                    "audio_url": audio_url,
                    "pub_date": pub_date,
                    "podcast": podcast_name,
                    "source": "小宇宙",
                })
    except Exception as e:
        log.error("Parse RSS for %s: %s", podcast_name, e)

    return episodes


async def _fetch_en_podcast_episodes(rss_url, podcast_name):
    """Fetch latest episodes from English podcast via direct RSS."""
    rss = await _fetch(rss_url)
    if not rss:
        return []

    episodes = []
    try:
        root = ET.fromstring(rss)
        for item in root.findall(".//item")[:3]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            enc = item.find("enclosure")
            audio_url = enc.get("url", "") if enc is not None else ""
            pub_date = item.findtext("pubDate", "")

            if title and audio_url:
                episodes.append({
                    "title": title,
                    "link": link,
                    "audio_url": audio_url,
                    "pub_date": pub_date,
                    "podcast": podcast_name,
                    "source": "Podcast",
                })
    except Exception as e:
        log.error("Parse RSS for %s: %s", podcast_name, e)

    return episodes


async def process_episode(ep, cache, en_mode=False):
    """Process a single episode: download → transcribe → summarize."""
    ep_id = ep["audio_url"][:100]
    if ep_id in cache.get("seen", []):
        return None

    log.info("Processing: %s — %s", ep["podcast"], ep["title"])

    # Download
    audio_path = await _download_audio(ep["audio_url"])
    if not audio_path:
        log.warning("Skipped (download failed): %s", ep["title"])
        return None

    try:
        # Transcribe
        transcript = await _transcribe_groq(audio_path, lang="en" if en_mode else "zh")
        if not transcript or len(transcript) < 50:
            log.warning("Skipped (empty transcript): %s", ep["title"])
            return None

        # Summarize
        summary = await _summarize(ep["title"], transcript, en_mode=en_mode)
        if not summary:
            return None

        # Mark as seen
        cache.setdefault("seen", []).append(ep_id)
        if len(cache["seen"]) > 200:
            cache["seen"] = cache["seen"][-100:]

        return {
            "podcast": ep["podcast"],
            "title": ep["title"],
            "link": ep["link"],
            "summary": summary,
            "source": ep["source"],
            "transcript_len": len(transcript),
        }
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


async def main():
    today = datetime.now(HKT).strftime("%Y-%m-%d")

    # Mode: --en for English, default for Chinese (小宇宙)
    en_mode = "--en" in sys.argv
    specific_id = None
    for arg in sys.argv[1:]:
        if arg != "--en":
            specific_id = arg

    sent_flag = SENT_FLAG_EN if en_mode else SENT_FLAG_CN

    # Check if already ran today
    if not specific_id:
        if os.path.exists(sent_flag):
            with open(sent_flag) as f:
                if today in f.read():
                    log.info("Already ran for %s (%s)", today, "EN" if en_mode else "CN")
                    return

    cache = _load_cache()

    # Fetch episodes
    all_episodes = []
    if specific_id:
        name = CN_PODCASTS.get(specific_id, specific_id)
        all_episodes = await _fetch_xiaoyuzhou_episodes(specific_id, name)
    elif en_mode:
        # English podcasts via direct RSS
        log.info("Fetching %d English podcasts...", len(EN_PODCASTS))
        tasks = [_fetch_en_podcast_episodes(url, name)
                 for url, name in EN_PODCASTS.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_episodes.extend(r)
    else:
        # Chinese podcasts via RSSHub (小宇宙)
        log.info("Fetching %d Chinese podcasts...", len(CN_PODCASTS))
        tasks = [_fetch_xiaoyuzhou_episodes(pid, name)
                 for pid, name in CN_PODCASTS.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_episodes.extend(r)

    if not all_episodes:
        log.info("No episodes found")
        return

    # Filter unseen
    unseen = [ep for ep in all_episodes
              if ep["audio_url"][:100] not in cache.get("seen", [])]

    if not unseen:
        log.info("No new episodes")
        if not specific_id:
            with open(sent_flag, "a") as f:
                f.write(today + "\n")
        return

    log.info("Found %d new episodes, scoring...", len(unseen))

    # Score episodes: celebrity > keyword relevance
    celebrities = [
        "elon musk", "musk", "马斯克", "naval ravikant", "naval",
        "sam altman", "altman", "jensen huang", "黄仁勋",
        "zuckerberg", "bezos", "bill gates", "buffett", "巴菲特",
        "peter thiel", "marc andreessen", "a16z", "dario amodei",
        "ilya sutskever", "demis hassabis", "satya nadella",
        "ray dalio", "达利奥", "charlie munger", "芒格",
        "chamath", "garry tan", "paul graham",
        "李开复", "张一鸣", "黄峥", "雷军", "马化腾", "任正非",
    ]
    keywords = ["ai", "人工智能", "startup", "创业", "赚钱", "商业",
                "business", "money", "invest", "future", "未来", "投资"]

    for ep in unseen:
        title_lower = ep["title"].lower()
        celeb = sum(50 for c in celebrities if c in title_lower)
        kw = sum(5 for k in keywords if k in title_lower)
        ep["_score"] = celeb + kw

    unseen.sort(key=lambda x: x.get("_score", 0), reverse=True)
    best = unseen[0]
    log.info("Best: %s (score: %d)", best["title"][:50], best.get("_score", 0))

    results = []
    for candidate in unseen[:3]:
        result = await process_episode(candidate, cache, en_mode=en_mode)
        if result:
            results.append(result)
            break
        else:
            # Mark as seen so it's not retried next run (avoids infinite loop on bad episodes)
            ep_id = candidate["audio_url"][:100]
            cache.setdefault("seen", []).append(ep_id)
            log.info("Marking as seen (failed): %s", candidate["title"][:50])

    _save_cache(cache)

    if not results:
        log.info("No episodes successfully processed")
        if not specific_id:
            with open(sent_flag, "a") as f:
                f.write(today + "\n")
        return

    # Send to Telegram
    if BOT_TOKEN:
        from telegram import Bot
        bot = Bot(token=BOT_TOKEN)
        thread_id = await _get_podcast_thread(bot, en_mode)

        header = f"🎙 <b>Podcast Digest — {today}</b>\n\n"
        parts = [header]

        for r in results:
            link_text = f"🔗 <a href=\"{r['link']}\">{r['title'][:50]}</a>" if r['link'] else r['title'][:50]
            part = (
                f"<b>{r['podcast']}</b>\n"
                f"{link_text}\n\n"
                f"{r['summary']}\n\n"
                f"{'─' * 30}\n\n"
            )
            parts.append(part)

        # Send to correct thread based on mode
        send_kwargs = {
            "chat_id": AI_WORLD_GROUP,
            "message_thread_id": thread_id,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        # Send header first
        try:
            await bot.send_message(text=parts[0][:4000], **send_kwargs)
        except Exception as e:
            log.error("Send header failed: %s", e)

        # Send each podcast summary with vote buttons
        for part in parts[1:]:
            key = _dfb_key(part[:100])
            markup = _dfb_buttons("podcast", key)
            try:
                await bot.send_message(
                    text=part[:4000], reply_markup=markup, **send_kwargs,
                )
                await asyncio.sleep(0.5)
            except Exception as e:
                log.error("Send failed: %s", e)

        log.info("Sent %d podcast summaries", len(results))

    if not specific_id:
        with open(sent_flag, "a") as f:
            f.write(today + "\n")


if __name__ == "__main__":
    asyncio.run(main())
