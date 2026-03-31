#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Agent handoff context — file-based state sharing between team agents.

Agents save their output after each run. Other agents on the same team
see that context prepended to their prompt. 7-day TTL, 4000 char cap.

Usage:
    # In your team config, map domains to (team, role):
    TEAM_DOMAINS = {
        "team_a:scout": ("team_a", "scout"),
        "team_a:builder": ("team_a", "builder"),
        ...
    }

    # Save after agent produces output:
    save_handoff("team_a", "scout", result_text)

    # Load before sending prompt to agent:
    handoffs = load_handoffs("team_a", exclude_role="builder")

    # Clear on phase reset:
    clear_handoffs("team_a")
"""

import json
import logging
import os
import time
from pathlib import Path

from .config import PROJECT_DIR

logger = logging.getLogger("handoff")

HANDOFF_DIR = Path(PROJECT_DIR) / ".handoffs"
MAX_CONTENT_LEN = 4000
TTL_SECONDS = 7 * 24 * 3600  # 7 days

# Map domain → (team, role) for lookup.
# Add your own teams here (e.g. "team_b:scout": ("team_b", "scout")).
TEAM_DOMAINS = {
    "team_a:scout": ("team_a", "scout"),
    "team_a:builder": ("team_a", "builder"),
    "team_a:growth": ("team_a", "growth"),
    "team_a:critic": ("team_a", "critic"),
}


def save_handoff(team: str, role: str, content: str, summary: str = "") -> None:
    """Save agent output as handoff context for other team agents."""
    HANDOFF_DIR.mkdir(exist_ok=True)

    if not summary:
        summary = content[:300]

    data = {
        "team": team,
        "role": role,
        "timestamp": time.time(),
        "summary": summary[:MAX_CONTENT_LEN],
        "content": content[:MAX_CONTENT_LEN],
    }

    target = HANDOFF_DIR / f"{team}_{role}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, target)
    logger.info("Saved handoff: %s:%s (%d chars)", team, role, len(content))


def load_handoffs(team: str, exclude_role: str = "") -> list[dict]:
    """Load all handoff context for a team, excluding the requesting agent's own output.
    Returns list of dicts with role, timestamp, summary, content. Skips expired (>7d)."""
    if not HANDOFF_DIR.exists():
        return []

    now = time.time()
    handoffs = []

    for path in HANDOFF_DIR.glob(f"{team}_*.json"):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if data.get("role") == exclude_role:
            continue

        age = now - data.get("timestamp", 0)
        if age > TTL_SECONDS:
            path.unlink(missing_ok=True)
            continue

        handoffs.append(data)

    # Sort by timestamp (newest first)
    handoffs.sort(key=lambda h: h.get("timestamp", 0), reverse=True)
    return handoffs


def clear_handoffs(team: str) -> int:
    """Delete all handoff files for a team. Returns count deleted."""
    if not HANDOFF_DIR.exists():
        return 0

    count = 0
    for path in HANDOFF_DIR.glob(f"{team}_*.json"):
        path.unlink(missing_ok=True)
        count += 1

    logger.info("Cleared %d handoffs for team %s", count, team)
    return count
