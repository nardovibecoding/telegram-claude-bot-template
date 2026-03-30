# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Shared background task registry for admin bot.

Used by both bridge.py (auto-background) and commands.py (/bg command).
Multiple background tasks can run simultaneously.
Tracks last activity per task for progress monitoring.
"""
import itertools
import time as _time

# Registry: {task_id: {"task", "description", "started", "last_activity", "last_activity_ts", "progress"}}
bg_tasks: dict[str, dict] = {}
_counter = itertools.count(1)

_STUCK_THRESHOLD = 60  # seconds without activity = might be stuck


def next_task_id() -> str:
    """Generate next sequential task ID."""
    return str(next(_counter))


def register_task(task_id: str, description: str, asyncio_task=None) -> dict:
    """Register a new background task."""
    now = _time.monotonic()
    info = {
        "task": asyncio_task,
        "description": description,
        "started": now,
        "last_activity": "starting...",
        "last_activity_ts": now,
        "progress": None,  # e.g. "234/380" for countable tasks
    }
    bg_tasks[task_id] = info
    return info


def update_activity(task_id: str, activity: str, progress: str = None):
    """Update last activity for a task. Call this from the running task."""
    if task_id in bg_tasks:
        bg_tasks[task_id]["last_activity"] = activity
        bg_tasks[task_id]["last_activity_ts"] = _time.monotonic()
        if progress is not None:
            bg_tasks[task_id]["progress"] = progress


def unregister_task(task_id: str):
    """Remove a completed/failed task from registry."""
    bg_tasks.pop(task_id, None)


def get_status_text() -> str:
    """Get formatted status of all running background tasks."""
    if not bg_tasks:
        return "No background tasks running."
    lines = ["<b>Running tasks:</b>\n"]
    now = _time.monotonic()
    for tid, info in list(bg_tasks.items()):
        age = int(now - info["started"])
        mins, secs = divmod(age, 60)
        time_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"

        # Last activity
        activity = info.get("last_activity", "?")[:50]
        activity_age = int(now - info.get("last_activity_ts", now))

        # Progress (if countable)
        progress = info.get("progress", "")
        progress_str = f" {progress}" if progress else ""

        # Stuck warning
        stuck = " \u26a0\ufe0f" if activity_age > _STUCK_THRESHOLD else ""

        # Activity age
        if activity_age < 5:
            ago = "now"
        elif activity_age < 60:
            ago = f"{activity_age}s ago"
        else:
            ago = f"{activity_age // 60}m ago"

        lines.append(
            f"<code>#{tid}</code>{progress_str} ({time_str}){stuck}\n"
            f"  last: {activity} ({ago})"
        )
    return "\n".join(lines)
