#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Standalone X daily digest sender — backup for bot internal scheduler.
Checks flag file to avoid double-sending.

Usage:
  python send_xdigest.py                # all X bots
  python send_xdigest.py twitter xcn    # specific bots only
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

load_dotenv()

BASE_DIR = Path(__file__).parent
PERSONAS_DIR = BASE_DIR / "personas"
MINIMAX_API_KEY = os.environ["MINIMAX_API_KEY"]

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger("send_xdigest")

X_PERSONAS = ["twitter", "xcn", "xai", "xniche"]


async def _send_one(bot, chat_id, thread_id, retries=3, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                read_timeout=30, write_timeout=30, **kwargs,
            )
            return True
        except TelegramError as e:
            err_msg = str(e).lower()
            if "flood control" in err_msg or "retry in" in err_msg:
                # Extract retry seconds from error message
                import re
                m = re.search(r'retry in (\d+)', str(e))
                wait = int(m.group(1)) + 2 if m else 30
                logger.warning("Flood control, waiting %ds: %s", wait, e)
                await asyncio.sleep(wait)
            elif attempt < retries:
                wait = attempt * 3
                logger.warning("Retry %d/%d in %ds: %s", attempt, retries, wait, e)
                await asyncio.sleep(wait)
            else:
                logger.error("Give up after %d attempts: %s", retries, e)
                return False


async def send_for_persona(persona_id):
    flag = BASE_DIR / f".digest_sent_x_{persona_id}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if flag.exists() and flag.read_text().strip() == today:
        logger.info("⏭ %s: already sent today, skipping", persona_id)
        return

    config_path = PERSONAS_DIR / f"{persona_id}.json"
    if not config_path.exists():
        logger.error("No config for %s", persona_id)
        return

    persona = json.loads(config_path.read_text())
    target = persona.get("xcurate_target")
    if not target:
        logger.info("No xcurate_target for %s", persona_id)
        return

    # Use admin bot token — the per-persona X bots no longer run,
    # so the admin bot handles voting callbacks instead.
    token = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN")
    if not token:
        logger.error("No TELEGRAM_BOT_TOKEN_ADMIN for %s", persona_id)
        return

    lang = persona.get("xcurate_lang", "en")
    list_ids = persona.get("xcurate_lists", [])

    logger.info("🚀 %s: generating X digest (lang=%s)…", persona_id, lang)

    from x_curator import generate_daily_digest
    try:
        cards = await generate_daily_digest(MINIMAX_API_KEY, lang=lang, list_ids=list_ids)
    except Exception as e:
        logger.error("❌ %s: digest generation failed: %s", persona_id, e)
        return

    if not cards:
        logger.warning("⚠️ %s: no cards generated", persona_id)
        return

    bot = Bot(token=token)
    chat_id = target["chat_id"]
    thread_id = target.get("thread_id")

    today_fmt = datetime.now(timezone.utc).strftime("%d %b %Y")
    separator = "🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧"
    header = f"🗞 <b>X Daily — {today_fmt}</b>  ({len(cards)} picks)"

    sent = 0
    for msg_kwargs in [
        {"text": separator},
        {"text": header, "parse_mode": "HTML"},
    ] + [
        {"text": c["text"], "reply_markup": c["markup"], "parse_mode": "HTML", "disable_web_page_preview": True}
        for c in cards
    ]:
        ok = await _send_one(bot, chat_id, thread_id, **msg_kwargs)
        if ok:
            sent += 1
        await asyncio.sleep(1.5)  # prevent Telegram flood control

    # Send forecast for EN and AI digests
    try:
        from x_forecast import generate_forecast
        if persona_id in ("twitter", "xai"):
            forecast = await generate_forecast(
                [{"author": c.get("author", ""), "summary": c.get("text", "")} for c in cards],
                lang=lang,
            )
            if forecast:
                ok = await _send_one(bot, chat_id, thread_id, text=forecast, parse_mode="HTML")
                if ok:
                    sent += 1
                await asyncio.sleep(1.5)
    except Exception as e:
        logger.warning("X forecast failed for %s: %s", persona_id, e)

    total = len(cards) + 2
    logger.info("✅ %s: %d/%d sent → chat=%s thread=%s", persona_id, sent, total, chat_id, thread_id)

    # Mark as sent
    flag.write_text(today)


async def prefetch_only():
    """Just prefetch tweets into shared cache, don't send any digests."""
    from x_curator import prefetch_tweets
    logger.info("🔄 Prefetching tweets into shared cache…")
    try:
        await prefetch_tweets()
        logger.info("✅ Prefetch complete")
    except Exception as e:
        logger.error("❌ Prefetch failed: %s", e)


async def main():
    # Handle --prefetch-only flag
    if "--prefetch-only" in sys.argv:
        await prefetch_only()
        return

    only = set(a for a in sys.argv[1:] if not a.startswith("-"))
    personas = [p for p in X_PERSONAS if not only or p in only]

    # Stagger delays (错峰): heavier sources get longer gaps after them
    # EN/AI/CN read from prefetch cache (~2min each), lists fetch 13 lists (~5min)
    stagger = {"twitter": 120, "xcn": 120, "xai": 120, "xniche": 180}

    for persona_id in personas:
        try:
            await send_for_persona(persona_id)
        except Exception as e:
            logger.error("❌ %s: %s", persona_id, e)
        delay = stagger.get(persona_id, 120)
        logger.info("⏳ Waiting %ds before next source (错峰)…", delay)
        await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(main())
