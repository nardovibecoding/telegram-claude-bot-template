#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Standalone daily digest sender — run via OS cron, independent of bot uptime.

Crontab entry (12:00 HKT = 04:00 UTC):
  0 4 * * * cd ~/telegram-claude-bot && \
            ~/telegram-claude-bot/venv/bin/python send_digest.py \
            >> ~/telegram-claude-bot/logs/digest.log 2>&1
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError, RetryAfter

from news import generate_full_digest
from crypto_news import generate_crypto_digest

load_dotenv()

BASE_DIR     = Path(__file__).parent
PERSONAS_DIR = BASE_DIR / "personas"
TOPIC_CACHE  = BASE_DIR / "topic_cache.json"

# LLM calls route through llm_client.py (Kimi → fallback chain)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("send_digest")


def _load_topic_cache() -> dict:
    if TOPIC_CACHE.exists():
        try:
            return json.loads(TOPIC_CACHE.read_text())
        except Exception:
            pass
    return {}


def _get_targets(persona_id: str, topic_names: set[str], topic_cache: dict) -> list[tuple[int, int | None]]:
    """Return (chat_id, thread_id) pairs for a persona."""
    targets: list[tuple[int, int | None]] = []

    # Forum topic threads whose name matches this persona's topic_names
    for group_id_str, threads in topic_cache.items():
        for thread_id_str, name in threads.items():
            if name.lower() in topic_names:
                targets.append((int(group_id_str), int(thread_id_str)))

    # Private-chat subscribers
    subs_file = BASE_DIR / f"subscribers_{persona_id}.json"
    if subs_file.exists():
        try:
            for chat_id in json.loads(subs_file.read_text()):
                targets.append((int(chat_id), None))
        except Exception as e:
            logger.warning("Could not load subscribers for %s: %s", persona_id, e)

    return targets


async def _send_one(bot: Bot, chat_id: int, thread_id: int | None, text: str, parse_mode: str | None, reply_markup=None, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        try:
            await bot.send_message(
                chat_id=chat_id, text=text,
                message_thread_id=thread_id,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                read_timeout=30, write_timeout=30,
            )
            return True
        except RetryAfter as e:
            if attempt < retries:
                wait = e.retry_after + 1
                logger.warning("Retry %d/%d in %ds flood control (chat=%s): %s", attempt, retries, wait, chat_id, e)
                await asyncio.sleep(wait)
            else:
                logger.error("Give up after %d attempts (chat=%s): %s", retries, chat_id, e)
                return False
        except TelegramError as e:
            if attempt < retries:
                wait = min(2 ** attempt * 5, 60)
                logger.warning("Retry %d/%d in %ds (chat=%s): %s", attempt, retries, wait, chat_id, e)
                await asyncio.sleep(wait)
            else:
                logger.error("Give up after %d attempts (chat=%s): %s", retries, chat_id, e)
                return False


async def _send(bot: Bot, messages: list[dict | str], targets: list[tuple[int, int | None]], persona_id: str = "", parse_mode: str | None = None) -> None:
    for chat_id, thread_id in targets:
        sent = 0
        failed = 0
        for msg in messages:
            if isinstance(msg, dict):
                text   = msg["text"]
                pm     = msg.get("parse_mode", parse_mode)
                markup = msg.get("reply_markup")
            else:
                text   = msg
                pm     = parse_mode
                markup = None
            ok = await _send_one(bot, chat_id, thread_id, text, pm, markup)
            if ok:
                sent += 1
            else:
                failed += 1
            await asyncio.sleep(0.5)

        total = len(messages)
        if failed == 0:
            logger.info("✅ %s: %d/%d sent OK → chat=%s thread=%s", persona_id, sent, total, chat_id, thread_id)
        else:
            logger.warning("⚠️ %s: %d/%d sent, %d FAILED → chat=%s thread=%s", persona_id, sent, total, failed, chat_id, thread_id)


async def main() -> None:
    import sys
    only_personas = set(sys.argv[1:]) if len(sys.argv) > 1 else None

    topic_cache = _load_topic_cache()

    # Cache generated digests by news_module — avoid regenerating for the same content
    digest_cache: dict[str, list[str]] = {}

    for config_path in sorted(PERSONAS_DIR.glob("*.json")):
        persona_id = config_path.stem
        persona    = json.loads(config_path.read_text())

        if only_personas and persona_id not in only_personas:
            continue

        if not persona.get("digest_enabled", True):
            logger.info("Skipping %s (digest_enabled=False)", persona_id)
            continue

        # Flag file — same file bot_base.py uses — skip if already sent today
        flag_file = BASE_DIR / f".digest_sent_news_{persona_id}"
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if flag_file.exists() and flag_file.read_text().strip() == today_str:
            logger.info("⏭ %s: already sent today (flag file), skipping", persona_id)
            continue

        topic_names = {n.lower() for n in persona.get("topic_names", [])}
        targets     = _get_targets(persona_id, topic_names, topic_cache)

        if not targets:
            logger.info("No targets for %s — skipping", persona_id)
            continue

        # Resolve bot token
        token = os.environ.get(f"TELEGRAM_BOT_TOKEN_{persona_id.upper()}") \
             or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            logger.error("No token for %s — skipping", persona_id)
            continue

        # Generate digest once per news_module, reuse if already done
        news_module = persona.get("news_module", "standard")
        if news_module not in digest_cache:
            logger.info("Generating digest: news_module=%s", news_module)
            try:
                if news_module == "crypto_news":
                    digest_cache[news_module] = await generate_crypto_digest("")
                else:
                    digest_cache[news_module] = await generate_full_digest("")
            except Exception as e:
                logger.error("Digest generation failed (%s): %s", news_module, e)
                continue

        messages = digest_cache[news_module]
        logger.info("Sending %s digest → %d target(s) for persona=%s", news_module, len(targets), persona_id)
        async with Bot(token=token) as bot:
            await _send(bot, messages, targets, persona_id=persona_id)

        # Write flag file after successful send
        flag_file.write_text(today_str)
        logger.info("✅ %s: flag file written for %s", persona_id, today_str)


if __name__ == "__main__":
    asyncio.run(main())
