#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Clean stale caches + check cookie freshness. Run via cron every 6h."""
import glob
import json
import os
import shutil
import time

BASE = os.path.expanduser("~/telegram-claude-bot")
now = time.time()
H12 = 43200
H48 = 172800
D7 = 604800
D30 = 2592000


# -- PROTECTED: these files must NEVER be deleted by this script -------------
# Add persistent config/state files here. clear_if_stale() will refuse to
# delete them even if accidentally added to a TTL list.
PROTECTED = {
    # -- Digest routing -------------------------------------------
    "topic_cache.json",
    # -- Subscribers (created on first /subscribe) ----------------
    "subscribers_daliu.json",   "subscribers_sbf.json",
    "subscribers_reddit.json",  "subscribers_twitter.json",
    "subscribers_xcn.json",     "subscribers_xai.json",
    "subscribers_xniche.json",
    # -- Persona configs ------------------------------------------
    "personas/daliu.json",      "personas/sbf.json",
    "personas/reddit.json",     "personas/twitter.json",
    "personas/xcn.json",        "personas/xai.json",
    "personas/xniche.json",
    # -- Bot state ------------------------------------------------
    "evolution_database.json",
    "skill_library.json",
    "claude_sessions.json",
    "edwin_sessions.json",
    "father_config.json",
    "father_reminders.json",
    "edwin_memory/reminders.json",
    # -- Curation / learned data ----------------------------------
    "x_votes.json",
    "x_list.json",
    "xlist_config.json",
    "domain_groups.json",
    "outreach/template_weights.json",
    # -- Auth / credentials ---------------------------------------
    "twitter_cookies.json",
    "youtube_cookies.txt",
    "gmail_credentials.json",
    "gmail_token.json",
    # -- System config --------------------------------------------
    "hooks/platform_filter.json",
    "luma_profile.json",
}


def age_h(path):
    """Return file age in hours."""
    return (now - os.path.getmtime(path)) / 3600 if os.path.exists(path) else 0


def clear_if_stale(filename, max_age):
    """Delete file if older than max_age seconds."""
    p = os.path.join(BASE, filename)
    if filename in PROTECTED:
        print(f"SKIP (protected): {filename}")
        return
    if os.path.exists(p) and now - os.path.getmtime(p) > max_age:
        os.unlink(p)
        print(f"Cleared {filename} ({age_h(p):.0f}h old)" if os.path.exists(p) else f"Cleared {filename}")


def trim_json_list(filename, max_entries):
    """Keep only the last max_entries in a JSON list file."""
    p = os.path.join(BASE, filename)
    if not os.path.exists(p):
        return
    try:
        data = json.loads(open(p).read())
        if isinstance(data, list) and len(data) > max_entries:
            open(p, "w").write(json.dumps(data[-max_entries:], indent=2))
            print(f"Trimmed {filename}: {len(data)} -> {max_entries} entries")
    except (json.JSONDecodeError, OSError):
        pass


def trim_json_dict(filename, max_entries):
    """Keep only the last max_entries keys in a JSON dict file."""
    p = os.path.join(BASE, filename)
    if not os.path.exists(p):
        return
    try:
        data = json.loads(open(p).read())
        if isinstance(data, dict) and len(data) > max_entries:
            keys = list(data.keys())[-max_entries:]
            open(p, "w").write(json.dumps({k: data[k] for k in keys}, indent=2))
            print(f"Trimmed {filename}: {len(data)} -> {max_entries} keys")
    except (json.JSONDecodeError, OSError):
        pass


# ── Digest caches (12h TTL) ────���─────────────────────────
for f in [".reddit_cache.json", ".xcurate_prefetch.json", ".china_trends_cache.json",
          ".youtube_cache.json", ".podcast_cache.json",
          ".ai_digest_cache.json",
          ]:
    clear_if_stale(f, H12)

# ── Stale state files (48h TTL) ──────────────────────────
for f in [".pending_mutations.json"]:
    clear_if_stale(f, H48)

# ── Old content caches (7d TTL) ──────────────────────────
for f in ["digest_content_cache.json", ".morning_report_history.json"]:
    clear_if_stale(f, D7)

# ── Unbounded history — trim, don't delete ───────────────
trim_json_list(".fetch_watchdog_history.json", 100)
trim_json_list(".andrea_scout_seen.json", 500)  # dedup list — trim, 7d window kept by mtime guard
trim_json_list("ab_test_results.json", 500)  # accumulated eval data — trim, never delete
trim_json_list(".healer_history.json", 100)
trim_json_dict(".command_usage.json", 200)
trim_json_dict(".healer_alerted.json", 50)

# ── Daily review files older than 7 days ─────────────────
for f in glob.glob(os.path.join(BASE, ".daily_review_*.md")):
    if now - os.path.getmtime(f) > D7:
        os.unlink(f)
        print(f"Cleared {os.path.basename(f)}")

# ── Sent sentinel files — prune lines older than 30 days ─
for f in glob.glob(os.path.join(BASE, ".*_sent*")) + glob.glob(os.path.join(BASE, ".digest_sent_*")):
    if not os.path.isfile(f):
        continue
    try:
        lines = open(f).readlines()
        if len(lines) > 60:
            open(f, "w").writelines(lines[-30:])
            print(f"Pruned {os.path.basename(f)}: {len(lines)} -> 30 lines")
    except OSError:
        pass

# ── Tool caches (safe to nuke) ───────────────────────────
for d in [".ruff_cache", ".firecrawl"]:
    p = os.path.join(BASE, d)
    if os.path.isdir(p) and now - os.path.getmtime(p) > D7:
        shutil.rmtree(p, ignore_errors=True)
        print(f"Cleared {d}/")

# ── Playwright browser cache (keep profile, clear cache) ─
pw_cache = os.path.join(BASE, ".playwright_profile", "Default", "Cache")
if os.path.isdir(pw_cache):
    size_mb = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fns in os.walk(pw_cache) for f in fns
    ) / (1024 * 1024)
    if size_mb > 100:
        shutil.rmtree(pw_cache, ignore_errors=True)
        print(f"Cleared playwright cache ({size_mb:.0f}MB)")

# ── /tmp cleanup ─────────────────────────────────────────
for pattern in ["/tmp/playwright-artifacts-*", "/tmp/tg_photo_*.jpg", "/tmp/tmp*.jpg", "/tmp/tmp*.mp3"]:
    for f in glob.glob(pattern):
        try:
            age = now - os.path.getmtime(f)
            if age > H48:
                if os.path.isdir(f):
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    os.unlink(f)
                print(f"Cleared {os.path.basename(f)}")
        except OSError:
            pass

# ── Cookie freshness check ───────────────────────────────
ck = os.path.join(BASE, "twitter_cookies.json")
if os.path.exists(ck) and now - os.path.getmtime(ck) > 72000:
    open(os.path.join(BASE, ".cookies_need_refresh"), "w").write("stale")
    print("Flagged cookies for refresh")
