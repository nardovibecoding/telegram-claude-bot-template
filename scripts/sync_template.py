#!/usr/bin/env python3
# Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 — see LICENSE
"""Sync telegram-claude-bot → telegram-claude-bot-template (sanitized public copy).

The template is a full sanitized copy of the private bot — all functions,
no private details (tokens, IPs, emails, credentials, personal bots).

Usage:
    python scripts/sync_template.py              # check staleness
    python scripts/sync_template.py --sync       # full sync + push
    python scripts/sync_template.py --dry        # preview without pushing
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sanitize import sanitize as _sanitize, check_privacy as _check_privacy_raw

PRIVATE_ROOT = Path(__file__).parent.parent  # telegram-claude-bot/
TEMPLATE_DIR = Path.home() / "telegram-claude-bot-template"
_GH_USER = "<github-user>"
_REPO = "telegram-claude-bot-template"

# ── Files/dirs to skip entirely ─────────────────────────────────────────
SKIP_NAMES = {
    # Credentials & secrets
    "gmail_credentials.json", "gmail_token.json",
    "drive_token_bernard.json", "drive_token_stevie.json",
    "twitter_cookies.json", "youtube_cookies.txt",
    ".env", ".env.example",
    # Private state
    "memory", "outreach", "content_drafts", "plans",
    "outreach_report_latest.txt", "outreach_session.session",
    "claude_sessions.json",
    # Private one-off bots/services
    "edwin_bot.py.bak", "edwin_claude_rules.md", "edwin_claude.md",
    "edwin_memory", "edwin_reminder.py", "edwin_reminder.service",
    "edwin-claude", "father_config.json", "father_reminders.json",
    "luma_profile.json",
    # Runtime artifacts
    "venv", "__pycache__", "*.db", "*.pyc",
    "bookmarks.db",
    # Build/test artifacts
    "ab_test_effort.py", "ab_test_effort_v2.py", "ab_test_results.json",
    "findings.md", "progress.md", "task_plan.md", "test_abc.py",
    # Logs
    "logs",
    # Other private
    "TERMINAL_MEMORY.md", "ADMIN_HANDBOOK.md",
}

SKIP_SUFFIXES = {".db", ".pyc", ".session", ".log"}

# ── Dirs to copy (whitelisted) ───────────────────────────────────────────
COPY_DIRS = {
    "admin_bot", "docs", "hooks", "personas", "scripts",
    "skills", "tests", "claude-mcp-proxy", "claude-skill-loader",
    "admin_bot",
}

# ── Top-level .py files to copy ─────────────────────────────────────────
COPY_PY_FILES = {
    "admin_bot.py", "auto_healer.py", "bookmark_db.py", "bot_base.py",
    "cache_cleanup.py", "camofox_client.py", "china_trends.py",
    "content_broadcaster.py", "conversation_compressor.py",
    "conversation_logger.py", "cost_tracker.py", "crypto_news.py",
    "debate_council.py", "digest_feedback.py", "digest_ui.py",
    "douyin_digest.py", "evolution_database.json", "evolution_feed.py",
    "face_agent.py", "fetch_watchdog.py", "gap_detector.py",
    "gpt_critic.py", "llm_client.py", "memory.py", "morning_report.py",
    "multi_model_reviewer.py", "news.py", "podcast_digest.py",
    "reddit_digest.py", "refresh_cookies.py", "run_bot.py",
    "run_watchdog.py", "sanitizer.py", "send_digest.py",
    "skill_library.py", "speak_hook.py", "stablecoin_yields.py",
    "status_monitor.py", "twitter_feed.py", "utils.py",
    "voice_daemon.py", "x_curator.py", "x_feedback.py",
    "x_forecast.py", "xhs_digest.py", "youtube_digest.py",
    "pidlock.py", "gap_detector.py", "andrea_scout.py",
    "ai_learning_digest.py",
}

# ── Top-level misc files to copy ─────────────────────────────────────────
COPY_MISC = {
    "requirements.txt", "start_all.sh", "LICENSE", "NOTICE",
    "settings.template.json", "vps_setup.sh",
    "adminbot-restart.service", "adminbot-watcher.path",
    "personabots-restart.service", "personabots-watcher.path",
    "domain_groups.json",
}

# ── Sanitization (shared logic in sanitize.py) ──────────────────────────

def _check_privacy(content: str, filename: str) -> list[str]:
    return [f"  {v}" for v in _check_privacy_raw(content, filename)]


def _should_skip(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return True
    if path.suffix in SKIP_SUFFIXES:
        return True
    if path.name.startswith("memory_") and path.suffix == ".db":
        return True
    return False


def _copy_file(src: Path, dst: Path, dry_run: bool) -> tuple[bool, str]:
    """Copy + sanitize a file. Returns (copied, message)."""
    if _should_skip(src):
        return False, f"SKIP {src.name}"

    try:
        content = src.read_text(errors="replace")
    except Exception:
        return False, f"SKIP {src.name} (binary)"

    sanitized = _sanitize(content)
    violations = _check_privacy(sanitized, src.name)
    if violations:
        return False, f"BLOCKED {src.name} — {violations[0]}"

    if dst.exists() and dst.read_text(errors="replace") == sanitized:
        return False, ""  # unchanged

    if dry_run:
        return True, f"WOULD COPY {src.relative_to(PRIVATE_ROOT)}"

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(sanitized)
    if os.access(src, os.X_OK):
        os.chmod(dst, 0o755)
    return True, f"COPY {src.relative_to(PRIVATE_ROOT)}"


def _sync_dir(src_dir: Path, dst_dir: Path, dry_run: bool) -> int:
    """Recursively sync a directory. Returns count of copied files."""
    if not src_dir.exists():
        return 0
    copied = 0
    for src in src_dir.rglob("*"):
        if src.is_dir():
            continue
        if _should_skip(src):
            continue
        if "__pycache__" in str(src):
            continue
        rel = src.relative_to(src_dir)
        dst = dst_dir / rel
        ok, msg = _copy_file(src, dst, dry_run)
        if msg:
            print(f"    {msg}")
        if ok:
            copied += 1
    return copied


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)


def check_staleness():
    if not TEMPLATE_DIR.exists():
        print(f"  Template not cloned at {TEMPLATE_DIR}")
        return
    stale = []
    for fname in COPY_PY_FILES | COPY_MISC:
        src = PRIVATE_ROOT / fname
        dst = TEMPLATE_DIR / fname
        if not src.exists():
            continue
        if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
            stale.append(fname)
    if stale:
        print(f"  telegram-claude-bot-template: {len(stale)} files stale")
        for f in stale[:5]:
            print(f"    {f}")
        if len(stale) > 5:
            print(f"    ... +{len(stale)-5} more")
    else:
        print("  telegram-claude-bot-template: up to date")


def sync(dry_run: bool = False):
    if not TEMPLATE_DIR.exists():
        print(f"  Cloning {_REPO}...")
        subprocess.run(
            ["gh", "repo", "clone", f"{_GH_USER}/{_REPO}", str(TEMPLATE_DIR)],
            check=True)

    run(["git", "-C", str(TEMPLATE_DIR), "pull", "--ff-only"])

    copied = 0

    # Top-level .py files
    for fname in sorted(COPY_PY_FILES):
        src = PRIVATE_ROOT / fname
        dst = TEMPLATE_DIR / fname
        ok, msg = _copy_file(src, dst, dry_run)
        if msg:
            print(f"    {msg}")
        if ok:
            copied += 1

    # Top-level misc files
    for fname in sorted(COPY_MISC):
        src = PRIVATE_ROOT / fname
        dst = TEMPLATE_DIR / fname
        ok, msg = _copy_file(src, dst, dry_run)
        if msg:
            print(f"    {msg}")
        if ok:
            copied += 1

    # Directories
    for dirname in sorted(COPY_DIRS):
        src_dir = PRIVATE_ROOT / dirname
        dst_dir = TEMPLATE_DIR / dirname
        n = _sync_dir(src_dir, dst_dir, dry_run)
        if n:
            print(f"    {'WOULD COPY' if dry_run else 'COPIED'} {dirname}/ ({n} files)")
        copied += n

    if copied == 0:
        print("  telegram-claude-bot-template: nothing to sync")
        return

    if dry_run:
        print(f"  telegram-claude-bot-template: {copied} files would be synced")
        return

    # Commit + push
    run(["git", "-C", str(TEMPLATE_DIR), "add", "-A"])
    result = run(["git", "-C", str(TEMPLATE_DIR), "diff", "--cached", "--quiet"])
    if result.returncode == 0:
        print("  telegram-claude-bot-template: no changes to commit")
    else:
        run(["git", "-C", str(TEMPLATE_DIR), "commit", "-m",
             f"sync: {copied} files updated from telegram-claude-bot"],
            check=True)
        run(["git", "-C", str(TEMPLATE_DIR), "push"], check=True)
        print(f"  telegram-claude-bot-template: pushed ({copied} files)")


def main():
    parser = argparse.ArgumentParser(
        description="Sync telegram-claude-bot → template (sanitized)")
    parser.add_argument("--sync", action="store_true", help="Full sync + push")
    parser.add_argument("--dry", action="store_true", help="Preview only")
    args = parser.parse_args()

    if not args.sync:
        print("Checking template staleness...\n")
        check_staleness()
        print("\nRun with --sync to update (or --dry to preview)")
        return

    print("\nSyncing telegram-claude-bot-template...")
    sync(dry_run=args.dry)


if __name__ == "__main__":
    main()
