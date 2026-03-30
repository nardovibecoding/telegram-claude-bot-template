#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Standalone Reddit daily digest sender — backup for bot internal scheduler.
Checks flag file to avoid double-sending.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

load_dotenv()

BASE_DIR = Path(__file__).parent
PERSONAS_DIR = BASE_DIR / "personas"

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger("send_reddit_digest")


async def _send_one(bot, chat_id, thread_id, retries=3, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                read_timeout=30, write_timeout=30, **kwargs,
            )
            return True
        except TelegramError as e:
            if attempt < retries:
                wait = attempt * 3
                logger.warning("Retry %d/%d in %ds: %s", attempt, retries, wait, e)
                await asyncio.sleep(wait)
            else:
                logger.error("Give up after %d attempts: %s", retries, e)
                return False


async def main():
    flag = BASE_DIR / ".digest_sent_reddit_reddit"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if flag.exists() and flag.read_text().strip() == today:
        logger.info("⏭ Reddit digest already sent today, skipping")
        return

    config_path = PERSONAS_DIR / "reddit.json"
    persona = json.loads(config_path.read_text())
    target = persona.get("reddit_target")
    if not target:
        logger.error("No reddit_target in config")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN_REDDIT") \
         or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No token for reddit bot")
        return

    subreddits = persona.get("reddit_subreddits", [])
    if not subreddits:
        logger.error("No reddit_subreddits in config")
        return

    api_key = os.environ.get("MINIMAX_API_KEY", "")

    logger.info("🚀 Generating Reddit digest… (%d subreddits)", len(subreddits))

    from reddit_digest import generate_reddit_digest
    try:
        messages = generate_reddit_digest(subreddits, api_key=api_key)
    except Exception as e:
        logger.error("❌ Reddit digest failed: %s", e)
        return

    if not messages:
        logger.warning("⚠️ No Reddit posts generated")
        return

    bot = Bot(token=token)
    chat_id = target["chat_id"]
    thread_id = target.get("thread_id")

    # Send header
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await _send_one(
        bot, chat_id, thread_id,
        text=f"📰 <b>Reddit Digest — {today}</b>  ({len(messages)} posts)",
        parse_mode="HTML",
    )
    await asyncio.sleep(0.5)

    sent = 0
    for msg in messages:
        if isinstance(msg, dict):
            text = msg["text"]
            markup = msg.get("reply_markup")
        else:
            text = msg
            markup = None
        ok = await _send_one(bot, chat_id, thread_id, text=text, parse_mode="HTML",
                             disable_web_page_preview=True, reply_markup=markup)
        if ok:
            sent += 1
        await asyncio.sleep(0.5)

    logger.info("✅ Reddit digest: %d/%d sent → chat=%s thread=%s", sent, len(messages), chat_id, thread_id)

    # Mark as sent
    flag.write_text(today)


if __name__ == "__main__":
    asyncio.run(main())
