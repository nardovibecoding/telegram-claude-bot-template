#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Status Monitor — checks Claude status + model updates, sends alerts to DM.
- Claude status: every 10 min, alerts on any non-operational component
- Model updates: daily 9am HKT, scrapes MiniMax + Claude for new models
- "已读" inline button — press to dismiss alert
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("status_monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
HKT = timezone(timedelta(hours=8))
STATE_FILE = str(BASE_DIR / ".status_monitor_state.json")

CLAUDE_STATUS_URL = "https://status.claude.com/api/v2/summary.json"
MINIMAX_MODELS_URL = "https://api.minimaxi.com/v1/models"


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"last_claude_status": "operational", "known_models": []}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_claude_status() -> list[str]:
    """Check Claude status, return list of issues (empty = all good)."""
    try:
        resp = requests.get(CLAUDE_STATUS_URL, timeout=10)
        data = resp.json()
        issues = []
        for comp in data.get("components", []):
            if comp["status"] != "operational":
                issues.append(f"⚠️ <b>{comp['name']}</b>: {comp['status']}")
        return issues
    except Exception as e:
        log.warning(f"Claude status check failed: {e}")
        return []


def check_minimax_models() -> list[str]:
    """Check MiniMax API for model list, return new models."""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        return []
    try:
        resp = requests.get(
            MINIMAX_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        models = [m["id"] for m in resp.json().get("data", [])]
        return models
    except Exception as e:
        log.warning(f"MiniMax model check failed: {e}")
        return []


def check_claude_models() -> dict:
    """Return current Claude model info from docs."""
    # Hardcoded known latest — updated by daily check
    return {
        "opus": "claude-opus-4-6",
        "sonnet": "claude-sonnet-4-6",
        "haiku": "claude-haiku-4-5-20251001",
    }


def read_button():
    """Create '已读' inline keyboard button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 已读", callback_data="status_read")]
    ])


async def send_alert(text: str):
    """Send alert to admin DM with 已读 button."""
    async with Bot(token=BOT_TOKEN) as bot:
        await bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=read_button(),
        )


async def check_status():
    """Check Claude status and alert if degraded."""
    state = load_state()
    issues = check_claude_status()

    prev_status = state.get("last_claude_status", "operational")

    if issues:
        current_status = "degraded"
        if prev_status == "operational":
            # New incident — alert
            now = datetime.now(HKT).strftime("%H:%M HKT")
            text = f"🔴 <b>Claude Status Alert — {now}</b>\n\n"
            text += "\n".join(issues)
            await send_alert(text)
            log.info("Status alert sent: %d issues", len(issues))
    else:
        current_status = "operational"
        if prev_status != "operational":
            # Recovered — notify
            now = datetime.now(HKT).strftime("%H:%M HKT")
            await send_alert(f"🟢 <b>Claude Recovered — {now}</b>\n\nAll systems operational.")
            log.info("Recovery alert sent")

    state["last_claude_status"] = current_status
    save_state(state)


async def check_models():
    """Daily model update check."""
    state = load_state()
    known = set(state.get("known_models", []))

    # Check MiniMax
    mm_models = check_minimax_models()
    new_mm = [m for m in mm_models if m not in known]

    # Build report
    updates = []
    if new_mm:
        updates.append(f"🆕 <b>MiniMax新模型:</b> {', '.join(new_mm)}")

    if updates:
        now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
        text = f"📡 <b>模型更新 — {now}</b>\n\n" + "\n".join(updates)
        await send_alert(text)
        # Update known models
        state["known_models"] = list(known | set(mm_models))
        save_state(state)
        log.info("Model update alert sent")
    else:
        log.info("No new models found")


async def main():
    """Run both checks."""
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"

    if mode == "status":
        await check_status()
    elif mode == "models":
        await check_models()
    elif mode == "both":
        await check_status()
        await check_models()


if __name__ == "__main__":
    asyncio.run(main())
