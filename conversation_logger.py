# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Shared JSONL conversation logger for all Telegram bots."""
import json
import re
import os
import glob
import time
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Cleanup throttle: only run once per day
_last_cleanup_date: str = ""


def log_message(bot_id: str, user_id: int, role: str, text: str,
                msg_type: str = "text", model: str = None, extra: dict = None):
    """Append a conversation entry to bot-specific JSONL file.

    Args:
        bot_id: which bot (father, admin, daliu, sbf, etc.)
        user_id: telegram user ID
        role: 'user' or 'assistant'
        text: message content
        msg_type: 'text', 'voice', 'photo', 'command'
        model: which model responded (sonnet, minimax, etc.)
        extra: any additional data (transcript, attachment paths, etc.)
    """
    entry = {
        "ts": datetime.now(HKT).isoformat(),
        "bot": bot_id,
        "user_id": user_id,
        "role": role,
        "text": text[:2000],  # Cap at 2000 chars per entry
        "type": msg_type,
    }
    if model:
        entry["model"] = model
    if extra:
        entry.update(extra)

    # One file per bot per month: father_2026-03.jsonl
    month = datetime.now(HKT).strftime("%Y-%m")
    filepath = os.path.join(LOG_DIR, f"{bot_id}_{month}.jsonl")

    try:
        with open(filepath, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Never let logging break the bot

    # Auto-rotate old files (once per day)
    _maybe_cleanup()


# Keywords that mark messages worth archiving (health, finance, emergency)
_IMPORTANT_KEYWORDS = re.compile(
    r"医院|急救|转账|投资|合同|emergency|health|药|draft|email|"
    r"hospital|doctor|insurance|passport|visa|bank|payment",
    re.IGNORECASE,
)


def _maybe_cleanup():
    """Smart rotate: entries older than 90 days.
    
    Important entries (matching keywords) move to {bot_id}_archive.jsonl.
    Everything else is deleted. Runs at most once per day.
    """
    global _last_cleanup_date
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    if _last_cleanup_date == today:
        return
    _last_cleanup_date = today

    cutoff = time.time() - (90 * 86400)
    try:
        for filepath in glob.glob(os.path.join(LOG_DIR, "*.jsonl")):
            # Never touch archive files
            if "_archive" in filepath:
                continue
            if os.path.getmtime(filepath) < cutoff:
                # Extract bot_id from filename: {bot_id}_{YYYY-MM}.jsonl
                fname = os.path.basename(filepath)
                bot_id = fname.rsplit("_", 1)[0]
                archive_path = os.path.join(LOG_DIR, f"{bot_id}_archive.jsonl")
                
                # Scan for important entries before deleting
                important = []
                try:
                    with open(filepath) as f:
                        for line in f:
                            try:
                                entry = json.loads(line)
                                text = entry.get("text", "")
                                if _IMPORTANT_KEYWORDS.search(text):
                                    important.append(line)
                            except (json.JSONDecodeError, KeyError):
                                continue
                except OSError:
                    pass
                
                # Append important entries to archive
                if important:
                    try:
                        with open(archive_path, "a") as af:
                            for line in important:
                                af.write(line)
                    except OSError:
                        pass
                
                # Delete the old file
                os.unlink(filepath)
    except Exception:
        pass


def get_recent_logs(bot_id: str, days: int = 7, limit: int = 100) -> list:
    """Read recent conversation entries for a bot."""
    files = sorted(glob.glob(os.path.join(LOG_DIR, f"{bot_id}_*.jsonl")))
    if not files:
        return []

    cutoff = datetime.now(HKT) - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()
    entries = []
    for filepath in files[-2:]:  # Last 2 months max
        try:
            with open(filepath) as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", "") >= cutoff_iso:
                            entries.append(entry)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            continue

    return entries[-limit:]
