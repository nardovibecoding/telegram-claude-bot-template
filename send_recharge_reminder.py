#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Send monthly recharge reminder for MiniMax & Claude subscriptions."""

import asyncio
import calendar
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from telegram import Bot

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN")
GROUP_ID = int(os.environ.get("PERSONAL_GROUP_ID", "0"))
THREAD_ID = 120  # https://t.me/c/3827304557/120
FLAG_FILE = "/tmp/recharge_reminder_sent"


async def send_reminder():
    if not TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN_ADMIN not set")
        sys.exit(1)

    now = datetime.now()
    day_key = now.strftime("%Y-%m-%d")

    # Idempotent: skip if already sent today
    if os.path.exists(FLAG_FILE):
        with open(FLAG_FILE) as f:
            sent_days = f.read().strip().split("\n")
            if day_key in sent_days:
                print(f"SKIP — already sent for {day_key}")
                return

    # On the 28th, only send if this month has no 30th (i.e., February)
    last_day = calendar.monthrange(now.year, now.month)[1]
    if now.day == 28 and last_day >= 30:
        print(f"SKIP — 28th but month has {last_day} days, will send on 30th instead")
        return

    bot = Bot(token=TOKEN)
    today = now.strftime("%Y-%m-%d")

    if now.day == 14:
        text = (
            f"💳 <b>Monthly Payment Reminder</b>\n"
            f"📅 {today}\n\n"
            f"🖥️ <b>YOUR_VPS_PROVIDER VPS</b> — renew server payment\n\n"
            f"⏰ Due soon. Don't let the VPS go offline!"
        )
    else:
        text = (
            f"💳 <b>Monthly Subscription Recharge Reminder</b>\n"
            f"📅 {today}\n\n"
            f"1️⃣ <b>MiniMax</b> — recharge M2.5 API credits\n"
            f"2️⃣ <b>Claude / Anthropic</b> — renew API subscription\n\n"
            f"⏰ Both due within the next 1-2 days. Don't let bots go offline!"
        )

    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=GROUP_ID,
                text=text,
                message_thread_id=THREAD_ID,
                parse_mode="HTML",
                read_timeout=30,
                write_timeout=30,
            )
            print(f"OK — reminder sent to thread {THREAD_ID}")
            # Write flag so we don't double-send
            with open(FLAG_FILE, "a") as f:
                f.write(day_key + "\n")
            return
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(5)

    print("FAILED — all 3 attempts failed")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(send_reminder())
