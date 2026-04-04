# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Domain detection, session management, and queue tracking."""
import asyncio
import json
import logging
import os

from .config import (
    ADMIN_USER_ID, GROUP_ID, TEAM_E_GROUP, TEAM_E_THREADS,
    PERSONAL_GROUP, PERSONAL_THREADS, SESSIONS_FILE, DOMAINS_FILE,
)

log = logging.getLogger("admin")

# Per-session locks and queue tracking
_session_locks: dict[str, asyncio.Lock] = {}
_session_queue_depth: dict[str, int] = {}


def _load_sessions():
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE) as f:
                return json.load(f)
    except Exception as e:
        log.warning("Failed to load sessions (%s), starting fresh", e)
    return {}


def _save_sessions(sessions):
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sessions, f, indent=2)
    os.replace(tmp, SESSIONS_FILE)


def _load_domain_groups():
    if os.path.exists(DOMAINS_FILE):
        try:
            with open(DOMAINS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_domain_groups(groups):
    tmp = DOMAINS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(groups, f, indent=2)
    os.replace(tmp, DOMAINS_FILE)


def _detect_domain(chat_id, thread_id):
    """Detect which domain a message belongs to."""
    if chat_id == ADMIN_USER_ID:
        return "news"
    if chat_id == GROUP_ID:
        return "news"
    if chat_id == TEAM_E_GROUP and thread_id in TEAM_E_THREADS:
        return TEAM_E_THREADS[thread_id]
    if chat_id == TEAM_E_GROUP:
        return "email"
    if chat_id == PERSONAL_GROUP and thread_id in PERSONAL_THREADS:
        return PERSONAL_THREADS[thread_id]
    if chat_id == PERSONAL_GROUP:
        return "personal"
    groups = _load_domain_groups()
    chat_str = str(chat_id)
    if chat_str in groups:
        base_domain = groups[chat_str]
        if base_domain == "andrea" and thread_id:
            andrea_topics = {3: "andrea:scout", 27: "andrea:growth", 28: "andrea:critic", 29: "andrea:builder"}
            return andrea_topics.get(thread_id, "andrea")
        if base_domain == "bella" and thread_id:
            bella_topics = {3: "bella:scout", 6: "bella:builder", 7: "bella:growth", 8: "bella:critic"}
            return bella_topics.get(thread_id, "bella")
        return base_domain
    return None


def _session_key(domain, thread_id):
    """Session key = domain:thread_id (per-topic conversations)."""
    if thread_id:
        return f"{domain}:{thread_id}"
    return domain


def get_session_lock(key: str) -> asyncio.Lock:
    """Get or create a session lock for the given key."""
    return _session_locks.setdefault(key, asyncio.Lock())


def get_queue_depth(key: str) -> int:
    return _session_queue_depth.get(key, 0)


def increment_queue_depth(key: str):
    _session_queue_depth[key] = _session_queue_depth.get(key, 0) + 1


def decrement_queue_depth(key: str):
    _session_queue_depth[key] = max(0, _session_queue_depth.get(key, 1) - 1)


def clear_all_locks():
    """Clear all session locks and queue depths (used by /restart admin)."""
    _session_locks.clear()
    _session_queue_depth.clear()
