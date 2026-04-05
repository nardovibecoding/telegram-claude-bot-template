#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""YouTube AI digest: discover AI videos from subscribed channels + keyword search,
get transcripts, summarize, send to AI World "📺 YouTube AI" thread.

Usage:
  python youtube_digest.py              # daily auto
  python youtube_digest.py <video_url>  # specific video

Requires: GROQ_API_KEY, TELEGRAM_BOT_TOKEN_ADMIN, youtube-transcript-api"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from digest_feedback import make_key as _dfb_key, vote_buttons as _dfb_buttons

log = logging.getLogger("youtube_digest")

from llm_client import chat_completion_async
from content_intelligence import ci
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
AI_WORLD_GROUP = -1003892866004
_YOUTUBE_THREAD_CACHE = str(BASE_DIR / ".youtube_ai_world_thread_id")


async def _get_youtube_thread(bot) -> int:
    """Return AI World YouTube thread, creating it on first run."""
    if os.path.exists(_YOUTUBE_THREAD_CACHE):
        with open(_YOUTUBE_THREAD_CACHE) as f:
            return int(f.read().strip())
    topic = await bot.create_forum_topic(chat_id=AI_WORLD_GROUP, name="📺 YouTube AI")
    tid = topic.message_thread_id
    with open(_YOUTUBE_THREAD_CACHE, "w") as f:
        f.write(str(tid))
    return tid
HKT = timezone(timedelta(hours=8))
SENT_FLAG = str(BASE_DIR / ".youtube_digest_sent")
CACHE_FILE = str(BASE_DIR / ".youtube_cache.json")

# YouTube channels: AI interviews + tech leaders + Chinese AI
AI_CHANNELS = {
    # Interview / podcast channels
    "UCnUYZLuoy1rq1aVMwx4piYg": "Lex Fridman",
    "UCHnyfMqiRRG1u-2MsSQLbXA": "Veritasium",
    "UCIjMtz33azEsb81kVAFCMWA": "Naval",
    "UCJIfeSCssxSC_Dhc5s7woww": "No Priors",
    "UCmFb4JXpGqjMlqSVeIBnHtA": "Lenny's Podcast",
    "UC5jyMPQ73AxNB_49DQJ-i8g": "All-In Podcast",
    "UCfE6tVNfF5EsFd5N5-cYi5g": "Latent Space",
    "UCLjRNOknzjAB7HJ4DCMRSOQ": "20VC (Harry Stebbings)",
    # AI tech channels
    "UCMLtBahI5DMrt0NPvDSoIRQ": "Matt Wolfe",
    "UCJ24N4O0bP7LGLBDvye7oC8": "Matt Berman",
    "UCLXo7UDZvByw2ixzpQCufnA": "Wes Roth",
    "UC4JX40jDee_tINbkjycV4Sg": "AI Explained",
    "UCZHmQk67mSJgfCCTn7xBfew": "ByteByteGo",
    "UCbfYPyITQ-7l4upoX8nvctg": "Two Minute Papers",
    # Chinese AI
    "UC7L3YCGxakwlWYT2aCsRq4Q": "林亦LYi",
    "UCii04BCvYIdQvTnce1rgClw": "花儿不哭",
}

# Keywords for YouTube search (via Invidious API)
AI_KEYWORDS = ["AI agent interview 2026", "elon musk AI", "naval ravikant",
               "sam altman interview", "anthropic claude", "AI startup founder",
               "人工智能 访谈", "AI coding assistant"]


def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"seen": []}


def _save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)


async def _fetch(url, timeout=15):
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


async def _fetch_channel_videos(channel_id, channel_name):
    """Fetch latest videos from a YouTube channel via RSS."""
    rss = await _fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    if not rss:
        return []

    videos = []
    try:
        ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
              "media": "http://search.yahoo.com/mrss/"}
        root = ET.fromstring(rss)
        for entry in root.findall("atom:entry", ns)[:3]:
            vid = entry.find("yt:videoId", ns)
            title_el = entry.find("atom:title", ns)
            pub = entry.find("atom:published", ns)
            if vid is not None and title_el is not None:
                video_id = vid.text
                title = title_el.text
                pub_date = pub.text[:10] if pub is not None else ""
                videos.append({
                    "video_id": video_id,
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "channel": channel_name,
                    "pub_date": pub_date,
                })
    except Exception as e:
        log.error("Parse RSS %s: %s", channel_name, e)

    return videos


async def _search_youtube(keyword):
    """Search YouTube via Invidious API for keyword."""
    # Try multiple Invidious instances
    instances = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://invidious.privacyredirect.com",
    ]
    for base in instances:
        raw = await _fetch(f"{base}/api/v1/search?q={keyword}&type=video&sort=upload_date&page=1", 10)
        if not raw:
            continue
        try:
            results = json.loads(raw)
            videos = []
            for item in results[:5]:
                if item.get("type") != "video":
                    continue
                videos.append({
                    "video_id": item.get("videoId", ""),
                    "title": item.get("title", ""),
                    "url": f"https://www.youtube.com/watch?v={item.get('videoId', '')}",
                    "channel": item.get("author", ""),
                    "pub_date": "",
                })
            return videos
        except Exception:
            continue
    return []


async def _get_transcript(video_id):
    """Get transcript via youtube-transcript-api v1.x (no cookies/proxy needed)."""
    loop = asyncio.get_event_loop()

    def _fetch():
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            api = YouTubeTranscriptApi()
            # Try English first, then Chinese
            for lang_codes in [["en"], ["zh-Hans", "zh-Hant", "zh"]]:
                try:
                    transcript = api.fetch(video_id, languages=lang_codes)
                    text = " ".join(s.text for s in transcript.snippets)
                    if len(text) > 100:
                        return text
                except Exception:
                    continue
            # Fallback: get any available transcript
            try:
                transcript_list = api.list(video_id)
                for t in transcript_list:
                    try:
                        transcript = t.fetch()
                        text = " ".join(s.text for s in transcript.snippets)
                        if len(text) > 100:
                            return text
                    except Exception:
                        continue
            except Exception as e:
                log.warning("Transcript list failed for %s: %s", video_id, type(e).__name__)
                return f"BLOCKED:{type(e).__name__}"
        except ImportError:
            log.error("youtube-transcript-api not installed: pip install youtube-transcript-api")
        except Exception as e:
            log.warning("Transcript failed for %s: %s", video_id, e)
        return ""

    try:
        text = await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=30)
        if text:
            log.info("Transcript: %d chars for %s", len(text), video_id)
        return text
    except asyncio.TimeoutError:
        log.warning("Transcript timeout for %s", video_id)
        return ""


async def _summarize(title, channel, transcript, description=""):
    """Summarize transcript (or description fallback) using LLM with fallback chain."""
    content = transcript if transcript and not transcript.startswith("BLOCKED:") else ""
    zh_chars = len(re.findall(r'[一-鿿]', (content or title)[:500]))
    lang = "繁體中文" if zh_chars > 20 else "English"

    if content:
        prompt = (
            f"Summarize this YouTube video transcript in {lang}:\n\n"
            f"Title: {title}\nChannel: {channel}\n\n"
            f"Transcript: {content[:8000]}\n\n"
            f"Requirements:\n"
            f"1. 3-5 key takeaways, each 1-2 sentences\n"
            f"2. One-line verdict: worth watching or skip?\n"
            f"3. Max 400 words total\n"
            f"4. Focus on actionable insights about AI, business, making money, or future predictions"
        )
    else:
        # No transcript — use title + description only
        desc_part = f"\nDescription: {description[:1000]}" if description else ""
        prompt = (
            f"Based on this YouTube video's title and description, write a brief summary in {lang}:\n\n"
            f"Title: {title}\nChannel: {channel}{desc_part}\n\n"
            f"Requirements:\n"
            f"1. 2-3 likely key points based on the title/description\n"
            f"2. One-line verdict: likely worth watching or skip?\n"
            f"3. Max 200 words total\n"
            f"4. Note: (transcript unavailable, based on title/description only)"
        )
    text = await chat_completion_async(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        timeout=45,
    )
    if text.startswith("⚠️"):
        log.error("Summarize failed: %s", text)
        return ""
    return text

async def _score_videos(videos, skip_duration_filter=False):
    """Score videos using only REAL YouTube metrics:
    1. Engagement rate: likes / views (only hard data we have)
    2. Duration filter: 5-60 min only (skip shorts + 3hr lectures)
    3. Keyword relevance: title + description match
    4. Per-channel cap: 1 per channel
    """
    import math
    scored = []

    for v in videos:
        vid = v["video_id"]
        stats = None
        for base in ["https://inv.nadeko.net", "https://invidious.nerdvpn.de"]:
            raw = await _fetch(f"{base}/api/v1/videos/{vid}?fields=viewCount,likeCount,lengthSeconds,description,subCountText", 8)
            if raw:
                try:
                    stats = json.loads(raw)
                    break
                except Exception:
                    continue

        views = 0
        likes = 0
        length_sec = 0
        desc = ""
        subs = 0
        if stats:
            views = stats.get("viewCount", 0) or 0
            likes = stats.get("likeCount", 0) or 0
            length_sec = stats.get("lengthSeconds", 0) or 0
            desc = (stats.get("description", "") or "").lower()
            v["description"] = stats.get("description", "") or ""
            # Parse subscriber count: "1.2M subscribers" → 1200000
            sub_text = stats.get("subCountText", "") or ""
            try:
                num = float(re.search(r'([\d.]+)', sub_text).group(1)) if sub_text else 0
                if "M" in sub_text or "万" in sub_text:
                    subs = int(num * 1_000_000)
                elif "K" in sub_text or "千" in sub_text:
                    subs = int(num * 1_000)
                else:
                    subs = int(num)
            except Exception:
                subs = 0

        # Skip: too short (<10min) or too long (>2hr). 0 = unknown, keep it.
        if not skip_duration_filter and length_sec > 0 and (length_sec < 600 or length_sec > 7200):
            log.debug("Skip %s: duration %ds", v["title"][:30], length_sec)
            continue

        # 1. Engagement rate: likes / views
        if views > 500:
            er = likes / views
        else:
            er = 0

        # 2. Subscriber count — bigger channel = better guests (real data on YT)
        sub_score = math.log10(max(subs, 1)) * 3  # 100K=15, 1M=18, 10M=21

        # 3. Celebrity detection — HIGHEST priority
        title_lower = v["title"].lower()
        celebrities = [
            "elon musk", "musk", "马斯克",
            "naval ravikant", "naval",
            "sam altman", "altman",
            "jensen huang", "黄仁勋",
            "mark zuckerberg", "zuckerberg",
            "jeff bezos", "bezos",
            "bill gates", "gates",
            "warren buffett", "buffett", "巴菲特",
            "peter thiel", "thiel",
            "marc andreessen", "a16z",
            "dario amodei", "amodei",
            "ilya sutskever", "sutskever",
            "demis hassabis", "hassabis",
            "satya nadella", "nadella",
            "sundar pichai", "pichai",
            "tim cook",
            "jack dorsey",
            "brian chesky",
            "patrick collison", "collison",
            "tobi lutke",
            "ray dalio", "dalio", "达利奥",
            "charlie munger", "munger", "芒格",
            "chamath",
            "garry tan",
            "paul graham", "pg",
            "李开复", "kai-fu lee",
            "张一鸣", "黄峥", "雷军", "马化腾", "任正非",
        ]
        celeb_hits = sum(1 for c in celebrities if c in title_lower)
        celeb_score = celeb_hits * 50  # 名人 = 最高權重

        # 4. Keyword relevance — fallback if no celebrity
        keywords = ["ai", "artificial intelligence", "agent", "startup", "business",
                     "money", "invest", "future", "predict", "crypto", "founder",
                     "entrepreneur", "billion", "vc", "build", "scale", "growth",
                     "人工智能", "赚钱", "创业", "未来", "投资", "商业"]
        title_hits = sum(1 for kw in keywords if kw in title_lower)
        desc_hits = sum(1 for kw in keywords if kw in desc[:500])
        relevance = title_hits * 3 + desc_hits

        # Score = ER×1000 + subs + celebrity×20 + relevance×10 + log(views)
        # ER is primary (real engagement data), celebrity name is a bonus not dominant
        # Fame already shows up in views + ER + subs — name list is just a quick boost
        view_base = math.log10(max(views, 1)) * 2
        score = er * 1000 + sub_score + celeb_score * 0.4 + relevance * 10 + view_base

        v["_score"] = round(score, 2)
        v["_views"] = views
        v["_likes"] = likes
        v["_subs"] = subs
        v["_er"] = round(er * 100, 2)
        v["_relevance"] = relevance
        scored.append(v)

        await asyncio.sleep(0.3)

    # No per-channel cap — just rank by score, pick best 1
    scored.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return scored


async def process_video(video, cache):
    """Process single video: transcript → summarize."""
    vid = video["video_id"]
    if vid in cache.get("seen", []):
        return None

    log.info("Processing: %s — %s", video["channel"], video["title"])

    transcript = await _get_transcript(vid)
    blocked = transcript and transcript.startswith("BLOCKED:")
    if not transcript or len(transcript) < 100:
        if blocked:
            log.warning("Transcript blocked (IP ban), using description fallback: %s", video["title"])
        else:
            log.warning("No transcript: %s", video["title"])
    description = video.get("description", "")
    # Skip only if no transcript AND no description AND not IP-blocked (blocked = use title fallback)
    if (not transcript or len(transcript) < 100) and not description and not blocked:
        return None

    summary = await _summarize(video["title"], video["channel"],
                               transcript or "", description)
    if not summary:
        return None

    cache.setdefault("seen", []).append(vid)
    if len(cache["seen"]) > 500:
        cache["seen"] = cache["seen"][-300:]

    return {
        "title": video["title"],
        "channel": video["channel"],
        "url": video["url"],
        "summary": summary,
    }


async def main():
    today = datetime.now(HKT).strftime("%Y-%m-%d")

    # Specific video mode
    if len(sys.argv) > 1 and "youtube" in sys.argv[1].lower() or len(sys.argv) > 1 and "youtu.be" in sys.argv[1].lower():
        url = sys.argv[1]
        vid_match = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url)
        if vid_match:
            cache = _load_cache()
            result = await process_video({
                "video_id": vid_match.group(1),
                "title": url,
                "channel": "manual",
                "url": url,
            }, cache)
            if result:
                _save_cache(cache)
                print(f"\n{result['summary']}")
            else:
                print("Failed to process video")
        return

    # Daily mode
    if os.path.exists(SENT_FLAG):
        with open(SENT_FLAG) as f:
            if today in f.read():
                log.info("Already ran for %s", today)
                return

    cache = _load_cache()

    # 1. Fetch from subscribed channels
    log.info("Fetching from %d channels...", len(AI_CHANNELS))
    channel_tasks = [_fetch_channel_videos(cid, name) for cid, name in AI_CHANNELS.items()]
    channel_results = await asyncio.gather(*channel_tasks, return_exceptions=True)

    all_videos = []
    for r in channel_results:
        if isinstance(r, list):
            all_videos.extend(r)

    # 2. Search for AI keywords
    log.info("Searching %d keywords...", len(AI_KEYWORDS))
    search_tasks = [_search_youtube(kw) for kw in AI_KEYWORDS]
    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    for r in search_results:
        if isinstance(r, list):
            all_videos.extend(r)

    # Dedup by video_id
    seen_ids = set()
    unique = []
    for v in all_videos:
        if v["video_id"] not in seen_ids and v["video_id"] not in cache.get("seen", []):
            seen_ids.add(v["video_id"])
            unique.append(v)

    log.info("Found %d unique new videos", len(unique))

    if not unique:
        log.info("No new videos")
        with open(SENT_FLAG, "a") as f:
            f.write(today + "\n")
        return

    # Score and rank — pick the #1 best video
    scored = await _score_videos(unique)
    if not scored:
        # Fallback: if all filtered by duration, score without duration filter
        log.warning("All videos filtered by duration, retrying without filter...")
        scored = await _score_videos(unique, skip_duration_filter=True)

    if not scored:
        log.info("No scoreable videos")
        with open(SENT_FLAG, "a") as f:
            f.write(today + "\n")
        return

    scored.sort(key=lambda x: x.get("_score", 0), reverse=True)
    log.info("Top video: %s (score: %s)", scored[0]["title"][:50], scored[0].get("_score", 0))

    # Try top scored videos until one has a transcript
    results = []
    for video in scored[:5]:
        result = await process_video(video, cache)
        if result:
            results.append(result)
            break
        await asyncio.sleep(1)

    _save_cache(cache)

    if not results:
        log.warning("No videos successfully processed — all lacked transcripts")
        with open(SENT_FLAG, "a") as f:
            f.write(today + "\n")
        return

    # Send to AI World YouTube thread
    if BOT_TOKEN:
        from telegram import Bot
        bot = Bot(token=BOT_TOKEN)

        header = f"📺 <b>YouTube AI Digest — {today}</b>\n\n"

        for r in results:
            text = (
                f"{header if r == results[0] else ''}"
                f"<b>{r['channel']}</b>\n"
                f"🔗 <a href=\"{r['url']}\">{r['title'][:60]}</a>\n\n"
                f"{r['summary']}\n\n"
                f"{'─' * 30}\n"
            )
            key = _dfb_key(r["url"])
            markup = _dfb_buttons("youtube", key)
            try:
                await bot.send_message(
                    chat_id=AI_WORLD_GROUP, message_thread_id=await _get_youtube_thread(bot),
                    text=text[:4000], parse_mode="HTML", disable_web_page_preview=True,
                    reply_markup=markup,
                )
                await asyncio.sleep(0.5)
            except Exception as e:
                log.error("Send failed: %s", e)

        log.info("Sent %d YouTube summaries", len(results))

    # Mark sent in shared content intelligence DB
    try:
        for r in results:
            url = r.get("url", "")
            if url:
                ci.store_story(r.get("title", ""), url, "YouTube")
                ci.mark_sent_by_urls([url], "youtube")
    except Exception as e:
        log.warning("content_intelligence failed: %s", e)

    with open(SENT_FLAG, "a") as f:
        f.write(today + "\n")


if __name__ == "__main__":
    asyncio.run(main())
