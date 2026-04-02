#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Standalone watchdog runner — sends alert to Telegram if sources are failing.
Run via cron 20 min before digest time.

Crontab: 40 2 * * * cd ~/telegram-claude-bot-template && source venv/bin/activate && source .env && python run_watchdog.py >> /tmp/watchdog.log 2>&1
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("run_watchdog")

# Same values as admin_bot/config.py
PERSONAL_GROUP = int(os.environ.get("PERSONAL_GROUP_ID", "0"))
HEARTBEAT_THREAD = 152


async def main():
    from fetch_watchdog import (
        run_all_probes, auto_fix, record_run, format_report,
        WARNING_FAIL_PCT,
    )

    logger.info("Fetch watchdog starting...")
    results = await run_all_probes()
    actions = await auto_fix(results)
    analysis = record_run(results)

    ok = sum(1 for r in results if r.ok)
    total = len(results)
    fail_pct = (total - ok) / total * 100 if total else 0

    logger.info("Results: %d/%d OK (%.0f%% fail)", ok, total, fail_pct)

    # Log all failures
    for r in results:
        if not r.ok:
            logger.warning("FAIL: %s (%s) — %s", r.name, r.category, r.error)

    # Log auto-fixes
    for a in actions:
        logger.info("Auto-fix: %s", a)

    # Send Telegram alert if failures warrant it
    if fail_pct >= WARNING_FAIL_PCT or analysis.get("persistent_failures"):
        token = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN")
        if not token:
            logger.error("Missing TELEGRAM_BOT_TOKEN_ADMIN for alert")
            return

        report = format_report(results, analysis, actions)

        from telegram import Bot
        async with Bot(token=token) as bot:
            await bot.send_message(
                chat_id=PERSONAL_GROUP,
                message_thread_id=HEARTBEAT_THREAD,
                text=report[:4000],
                parse_mode="HTML",
            )
            logger.info("Alert sent to Telegram")
    else:
        logger.info("All healthy, no alert needed")


if __name__ == "__main__":
    asyncio.run(main())
