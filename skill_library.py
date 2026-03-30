# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Skill Library — catalog of discovered skills/tools from daily crawls.

Functions:
- add_skill(entry) — add a discovered skill to the library
- get_skills(category=None, status=None, platform=None) — query skills
- update_skill(name, **fields) — update a skill's status/metadata
- get_stats() — summary stats for TG display
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("skill_library")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "skill_library.json"
HKT = timezone(timedelta(hours=8))

VALID_SOURCES = {
    "github", "openclaw", "smithery", "npm", "pypi",
    "awesome-list", "anthropic", "instreet",
}
VALID_PLATFORMS = {"claude", "openclaw", "both"}
VALID_CATEGORIES = {
    "memory", "crawl", "evolution", "security", "office",
    "dev-tools", "content", "automation", "voice", "agent",
}
VALID_STATUSES = {"discovered", "evaluated", "installed", "extracted", "skipped"}


def _load_db() -> list[dict]:
    try:
        with open(DB_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _save_db(db: list[dict]):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def _is_duplicate(db: list[dict], name: str, source_url: str) -> bool:
    """Check if skill already exists by name or source_url."""
    for entry in db:
        if entry.get("name", "").lower() == name.lower():
            return True
        if source_url and entry.get("source_url") and entry["source_url"] == source_url:
            return True
    return False


def add_skill(entry: dict) -> bool:
    """Add a discovered skill to the library.

    Required: name, source_url, description, source
    Returns True if added, False if duplicate.
    """
    name = entry.get("name", "").strip()
    source_url = entry.get("source_url", "").strip()
    if not name:
        log.warning("add_skill: missing name")
        return False

    db = _load_db()
    if _is_duplicate(db, name, source_url):
        return False

    now = datetime.now(HKT).isoformat()
    record = {
        "name": name,
        "source_url": source_url,
        "description": entry.get("description", "")[:300],
        "source": entry.get("source", "github"),
        "platform": entry.get("platform", "both"),
        "category": entry.get("category", ""),
        "status": "discovered",
        "overlap_pct": 0,
        "discovered_date": now,
        "evaluated_date": "",
        "notes": entry.get("notes", ""),
    }
    db.append(record)
    _save_db(db)
    log.info("Added skill: %s", name)
    return True


def get_skills(
    category: str | None = None,
    status: str | None = None,
    platform: str | None = None,
) -> list[dict]:
    """Query skills with optional filters."""
    db = _load_db()
    results = db
    if category:
        results = [s for s in results if s.get("category") == category]
    if status:
        results = [s for s in results if s.get("status") == status]
    if platform:
        results = [s for s in results if s.get("platform") == platform]
    return results


def update_skill(name: str, **fields) -> bool:
    """Update a skill's fields by name. Returns True if found and updated."""
    db = _load_db()
    for entry in db:
        if entry.get("name", "").lower() == name.lower():
            for k, v in fields.items():
                entry[k] = v
            if "status" in fields and fields["status"] == "evaluated":
                entry["evaluated_date"] = datetime.now(HKT).isoformat()
            _save_db(db)
            return True
    return False


def get_stats() -> dict:
    """Summary stats for TG display."""
    db = _load_db()
    total = len(db)

    by_status = {}
    for s in db:
        st = s.get("status", "discovered")
        by_status[st] = by_status.get(st, 0) + 1

    by_category = {}
    for s in db:
        cat = s.get("category") or "uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1

    by_source = {}
    for s in db:
        src = s.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1

    # Recent discoveries (last 7 days)
    now = datetime.now(HKT)
    recent = []
    for s in db:
        try:
            d = datetime.fromisoformat(s.get("discovered_date", ""))
            if (now - d).days <= 7:
                recent.append(s)
        except Exception:
            pass

    return {
        "total": total,
        "by_status": by_status,
        "by_category": by_category,
        "by_source": by_source,
        "recent": recent,
    }
