# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Cognitive intelligence layer for admin_bot.

Features:
1. Goal persistence — extracts open tasks, proactive follow-up after 48h
2. Episodic memory with FTS — topic-relevant retrieval, not just "last N"
3. Preference learning — captures corrections, injects into prompt
4. Goal auto-resolution — matches completed tasks to open goals

All post-response processing is async + fire-and-forget.
Uses Haiku for extraction (~$0.001/day).
"""
import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic

log = logging.getLogger("cognitive")

DB_PATH = Path(__file__).parent.parent / ".cognitive.db"
HKT = timezone(timedelta(hours=8))

# Correction signals → preference learning
_CORRECTION_SIGNALS = (
    "no ", "don't ", "stop ", "wrong", "not like that", "i said",
    "that's not", "you keep", "why did you",
    "不对", "别", "错了", "不是这样", "唔好", "唔係", "你又", "我讲咗",
)


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            source_msg TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            last_reminded TEXT
        );
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT NOT NULL,
            tags TEXT,
            created_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
            USING fts5(summary, content=episodes, content_rowid=id);
        CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule TEXT NOT NULL UNIQUE,
            source_msg TEXT,
            created_at TEXT NOT NULL,
            applied_count INTEGER DEFAULT 0
        );
        CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
            INSERT INTO episodes_fts(rowid, summary) VALUES (new.id, new.summary);
        END;
    """)
    conn.commit()
    conn.close()


_init_db()


# ─── Post-response processing ──────────────────────────────────────────────

async def process_after_response(
    user_msg: str, bot_reply: str, is_correction: bool = False
) -> None:
    """Extract goals + save episode + learn preferences. Fire-and-forget."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return  # Skip cognitive processing — no API key configured
    try:
        client = anthropic.AsyncAnthropic()

        # Build extraction prompt — add preference task if correction detected
        pref_instruction = (
            "\nPREF: extract one concrete preference rule from this correction "
            "(e.g. 'Owner prefers X over Y'). Write 'none' if no clear preference."
            if is_correction else ""
        )

        result = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=(
                "Extract from this conversation.\n"
                "GOALS: open tasks Owner mentioned that aren't done yet "
                "(max 2, concrete; 'none' if nothing pending).\n"
                "EPISODE: one-line summary of what happened.\n"
                "RESOLVED: comma-separated short phrases from the bot reply that "
                "suggest a task was COMPLETED (e.g. 'fixed', 'deployed', 'done'); "
                "'none' if no completion signal." +
                pref_instruction +
                "\nFormat exactly:\n"
                "GOALS: <none|task1; task2>\n"
                "EPISODE: <one line>\n"
                "RESOLVED: <none|phrase1, phrase2>" +
                ("\nPREF: <none|rule>" if is_correction else "")
            ),
            messages=[{
                "role": "user",
                "content": f"Owner: {user_msg[:400]}\nBot: {bot_reply[:400]}"
            }]
        )

        text = result.content[0].text
        parsed = {}
        for line in text.splitlines():
            for key in ("GOALS", "EPISODE", "RESOLVED", "PREF"):
                if line.startswith(f"{key}:"):
                    parsed[key] = line[len(key) + 1:].strip()

        now = datetime.now(HKT).isoformat()
        conn = sqlite3.connect(DB_PATH)
        try:
            # ① Store new goals
            goals_raw = parsed.get("GOALS", "none")
            if goals_raw.lower() != "none":
                for goal in goals_raw.split(";"):
                    goal = goal.strip()
                    if goal and len(goal) > 5:
                        existing = [r[0].lower() for r in
                                    conn.execute("SELECT text FROM goals WHERE resolved_at IS NULL").fetchall()]
                        if not any(goal.lower()[:30] in t for t in existing):
                            conn.execute(
                                "INSERT INTO goals (text, source_msg, created_at) VALUES (?,?,?)",
                                (goal, user_msg[:200], now)
                            )
                            log.info("cognitive: goal stored: %s", goal)

            # ② Auto-resolve goals matched by completion signals
            resolved_raw = parsed.get("RESOLVED", "none")
            if resolved_raw.lower() != "none":
                open_goals = conn.execute(
                    "SELECT id, text FROM goals WHERE resolved_at IS NULL"
                ).fetchall()
                for signal in resolved_raw.split(","):
                    signal = signal.strip().lower()
                    if not signal:
                        continue
                    for gid, gtext in open_goals:
                        goal_words = set(gtext.lower().split())
                        signal_words = set(signal.split())
                        if signal_words & goal_words:
                            conn.execute(
                                "UPDATE goals SET resolved_at=? WHERE id=?", (now, gid)
                            )
                            log.info("cognitive: auto-resolved goal #%d: %s", gid, gtext)

            # ③ Save episode (FTS trigger handles index)
            episode_raw = parsed.get("EPISODE", "")
            if episode_raw:
                conn.execute(
                    "INSERT INTO episodes (summary, created_at) VALUES (?,?)",
                    (episode_raw, now)
                )

            # ④ Save preference rule (if correction)
            pref_raw = parsed.get("PREF", "none")
            if is_correction and pref_raw and pref_raw.lower() != "none":
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO preferences (rule, source_msg, created_at) "
                        "VALUES (?,?,?)",
                        (pref_raw, user_msg[:200], now)
                    )
                    log.info("cognitive: preference learned: %s", pref_raw)
                except Exception:
                    pass

            # ⑤ Prune old episodes (keep last 500)
            count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            if count > 500:
                conn.execute(
                    "DELETE FROM episodes WHERE id NOT IN "
                    "(SELECT id FROM episodes ORDER BY created_at DESC LIMIT 500)"
                )

            conn.commit()
        finally:
            conn.close()

    except Exception as e:
        log.warning("cognitive: post-processing error: %s", e)


def _is_correction(user_msg: str) -> bool:
    """Detect correction signals in user message."""
    low = user_msg.lower()
    return any(sig in low for sig in _CORRECTION_SIGNALS)


# ─── Prompt injection ──────────────────────────────────────────────────────

def get_open_goals_text() -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT text FROM goals WHERE resolved_at IS NULL "
            "ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        return "Open goals: " + " | ".join(r[0] for r in rows)
    except Exception:
        return ""


def get_relevant_episodes(query: str, n: int = 2) -> str:
    """FTS-based retrieval: episodes relevant to current query topic."""
    try:
        conn = sqlite3.connect(DB_PATH)
        # Try FTS first
        keywords = " OR ".join(
            w for w in query.split() if len(w) > 3
        )[:200]
        rows = []
        if keywords:
            try:
                rows = conn.execute(
                    "SELECT e.summary, e.created_at FROM episodes e "
                    "JOIN episodes_fts fts ON e.id = fts.rowid "
                    "WHERE episodes_fts MATCH ? "
                    "ORDER BY e.created_at DESC LIMIT ?",
                    (keywords, n)
                ).fetchall()
            except Exception:
                pass
        # Fall back to recency if FTS returns nothing
        if not rows:
            rows = conn.execute(
                "SELECT summary, created_at FROM episodes "
                "ORDER BY created_at DESC LIMIT ?", (n,)
            ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = [f"- {r[0]} ({r[1][:10]})" for r in rows]
        return "Recent interactions:\n" + "\n".join(lines)
    except Exception:
        return ""


def get_preferences_text() -> str:
    """Inject learned preferences into prompt."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT rule FROM preferences ORDER BY created_at DESC LIMIT 8"
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        return "Learned preferences: " + " | ".join(r[0] for r in rows)
    except Exception:
        return ""


# ─── Goal management (for /goals command) ──────────────────────────────────

def list_open_goals() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, text, created_at FROM goals WHERE resolved_at IS NULL "
        "ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "text": r[1], "created_at": r[2]} for r in rows]


def resolve_goal(goal_id: int) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE goals SET resolved_at=? WHERE id=?",
            (datetime.now(HKT).isoformat(), goal_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# ─── Proactive stale goal reminders ────────────────────────────────────────

async def check_stale_goals(bot, chat_id: int, thread_id: int | None) -> None:
    """Called by scheduler — reminds Owner of goals untouched for 48h."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cutoff = (datetime.now(HKT) - timedelta(hours=48)).isoformat()
        rows = conn.execute(
            "SELECT id, text, created_at FROM goals "
            "WHERE resolved_at IS NULL "
            "AND (last_reminded IS NULL OR last_reminded < ?) "
            "AND created_at < ?",
            (cutoff, cutoff)
        ).fetchall()

        if not rows:
            conn.close()
            return

        now = datetime.now(HKT).isoformat()
        lines = [f"• #{r[0]} {r[1]} (since {r[2][:10]})" for r in rows]
        msg = (
            "🧠 Unresolved goals:\n" + "\n".join(lines) +
            "\n\nUse /goals done <id> to resolve."
        )

        await bot.send_message(
            chat_id=chat_id,
            text=msg,
            message_thread_id=thread_id
        )

        for r in rows:
            conn.execute(
                "UPDATE goals SET last_reminded=? WHERE id=?", (now, r[0])
            )
        conn.commit()
        conn.close()
        log.info("cognitive: reminded %d stale goals", len(rows))

    except Exception as e:
        log.warning("cognitive: stale goal check failed: %s", e)
