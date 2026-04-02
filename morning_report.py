#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Morning Report — self-healing daily health check.

Runs at 06:00 HKT daily (22:00 UTC). Collects system health, auto-fixes
known issues, git commits patches, and sends a consolidated report.

Flow:
  1. Collect health data (bots, digests, sources, system, logs)
  2. Auto-fix known patterns (no LLM needed)
  3. Git commit if files changed
  4. If anomalies remain → LLM interprets why + suggests action
  5. Send single consolidated TG report

80% of mornings = free template. 20% = LLM explains the anomaly.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("morning_report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

HKT = timezone(timedelta(hours=8))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
CHAT_ID = int(os.environ.get("PERSONAL_GROUP_ID", "0"))  # PERSONAL_GROUP
THREAD_ID = int(os.environ.get("HEARTBEAT_THREAD_ID", "0"))
HISTORY_FILE = BASE_DIR / ".morning_report_history.json"


# ── Data classes ──────────────────────────────────────────────────────


@dataclass
class HealthCheck:
    name: str
    category: str  # bots, digests, sources, system, cookies, logs
    ok: bool
    detail: str = ""


@dataclass
class FixResult:
    action: str
    success: bool
    files_changed: list = field(default_factory=list)
    detail: str = ""


# ── Phase 1: Collect Health Data ──────────────────────────────────────


async def check_bots() -> list[HealthCheck]:
    """Check all bot processes are running."""
    checks = []
    for name, pattern in [
        ("admin", "admin_bot"),
        ("bot1", "run_bot.py bot1"),
        ("bot2", "run_bot.py bot2"),
    ]:
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", pattern,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        ok = bool(stdout.decode().strip())
        checks.append(HealthCheck(name, "bots", ok, "" if ok else "not running"))

    # systemd service
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "telegram-bots"],
            capture_output=True, text=True, timeout=5,
        )
        svc_ok = r.stdout.strip() == "active"
        checks.append(HealthCheck("systemd", "bots", svc_ok,
                                  "" if svc_ok else r.stdout.strip()))
    except Exception as e:
        checks.append(HealthCheck("systemd", "bots", False, str(e)[:80]))

    return checks


async def check_digests() -> list[HealthCheck]:
    """Check yesterday's digests were delivered (via flag files)."""
    checks = []
    yesterday = (datetime.now(HKT) - timedelta(days=1)).strftime("%Y-%m-%d")

    flags = {
        "news(B1)": ".digest_sent_news_bot1",
        "news(B2)": ".digest_sent_news_bot2",
        "X:twitter": ".digest_sent_x_twitter",
        "X:xcn": ".digest_sent_x_xcn",
        "X:xai": ".digest_sent_x_xai",
        "X:xniche": ".digest_sent_x_xniche",
        "reddit": ".digest_sent_reddit_reddit",
        "youtube": ".youtube_digest_sent",
        "podcast_cn": ".podcast_digest_sent",
        "podcast_en": ".podcast_en_digest_sent",
        "evolution": ".evolution_feed_sent",
    }

    for label, flag_name in flags.items():
        flag_path = BASE_DIR / flag_name
        if flag_path.exists():
            content = flag_path.read_text().strip()
            ok = yesterday in content
            checks.append(HealthCheck(label, "digests", ok,
                                      "" if ok else f"last: {content[:10]}"))
        else:
            checks.append(HealthCheck(label, "digests", False, "no flag file"))

    return checks


async def check_system() -> list[HealthCheck]:
    """Check disk, memory, uptime."""
    checks = []

    # Disk
    try:
        proc = await asyncio.create_subprocess_exec(
            "df", "--output=pcent", "/",
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        pct = int(stdout.decode().strip().split("\n")[-1].strip().rstrip("%"))
        checks.append(HealthCheck("disk", "system", pct < 80, f"{pct}%"))
    except Exception as e:
        checks.append(HealthCheck("disk", "system", False, str(e)[:60]))

    # Memory
    try:
        proc = await asyncio.create_subprocess_exec(
            "free", "-m", stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        parts = stdout.decode().strip().split("\n")[1].split()
        mem_pct = int(int(parts[2]) / int(parts[1]) * 100)
        checks.append(HealthCheck("memory", "system", mem_pct < 90, f"{mem_pct}%"))
    except Exception as e:
        checks.append(HealthCheck("memory", "system", False, str(e)[:60]))

    # Uptime
    try:
        proc = await asyncio.create_subprocess_exec(
            "uptime", "-p", stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        uptime_str = stdout.decode().strip().replace("up ", "")
        checks.append(HealthCheck("uptime", "system", True, uptime_str))
    except Exception:
        checks.append(HealthCheck("uptime", "system", True, "?"))

    return checks


async def check_cookies() -> list[HealthCheck]:
    """Check Twitter cookie freshness."""
    checks = []
    cookie_path = BASE_DIR / "twitter_cookies.json"
    if cookie_path.exists():
        age_h = (time.time() - cookie_path.stat().st_mtime) / 3600
        ok = age_h < 36
        checks.append(HealthCheck("twitter", "cookies", ok, f"{int(age_h)}h"))
    else:
        checks.append(HealthCheck("twitter", "cookies", False, "missing"))
    return checks


async def check_services() -> list[HealthCheck]:
    """Check MCP services."""
    checks = []
    for name, port in [("xhs_mcp", 18060), ("douyin_mcp", 18070)]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-sf", "--max-time", "5", f"http://localhost:{port}/health",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            ok = proc.returncode == 0
            checks.append(HealthCheck(name, "services", ok,
                                      "" if ok else "not responding"))
        except Exception:
            checks.append(HealthCheck(name, "services", False, "check failed"))
    return checks


async def check_logs() -> list[HealthCheck]:
    """Check recent error rate in start_all.log."""
    checks = []
    try:
        with open("/tmp/start_all.log") as f:
            lines = f.readlines()[-500:]
        error_count = sum(1 for line in lines if "ERROR" in line)
        ok = error_count < 20
        detail = f"{error_count} in last 500 lines"
        checks.append(HealthCheck("errors", "logs", ok, detail))
    except Exception:
        checks.append(HealthCheck("errors", "logs", True, "log unreadable"))
    return checks


async def check_healer_history() -> list[HealthCheck]:
    """Check if auto_healer has been running and resolving issues."""
    checks = []
    healer_file = BASE_DIR / ".healer_history.json"
    if healer_file.exists():
        try:
            history = json.loads(healer_file.read_text())
            if history:
                last = history[-1]
                last_ts = last.get("timestamp", "?")
                issues = last.get("issues_found", 0)
                fixed = last.get("fixed", 0)
                checks.append(HealthCheck(
                    "auto_healer", "meta", True,
                    f"last: {last_ts[:16]}, {issues} found, {fixed} fixed"
                ))
            else:
                checks.append(HealthCheck("auto_healer", "meta", True, "no history"))
        except Exception:
            checks.append(HealthCheck(
                "auto_healer", "meta", True, "history unreadable",
            ))
    else:
        checks.append(HealthCheck("auto_healer", "meta", False, "never ran"))
    return checks


async def collect_health() -> list[HealthCheck]:
    """Run all health checks in parallel."""
    results = await asyncio.gather(
        check_bots(),
        check_digests(),
        check_system(),
        check_cookies(),
        check_services(),
        check_logs(),
        check_healer_history(),
    )
    # Flatten
    all_checks = []
    for group in results:
        all_checks.extend(group)
    return all_checks


# ── Phase 2: Auto-Fix Known Patterns ─────────────────────────────────


async def auto_fix(issues: list[HealthCheck]) -> list[FixResult]:
    """Fix known patterns. Returns list of fixes applied."""
    fixes = []

    for check in issues:
        if check.ok:
            continue

        # Bot process down → systemd restart
        if check.category == "bots" and check.name in ("bot1", "bot2", "admin"):
            try:
                subprocess.run(
                    ["sudo", "systemctl", "restart", "telegram-bots"],
                    timeout=10, capture_output=True,
                )
                # Wait for restart
                await asyncio.sleep(5)
                fixes.append(FixResult(
                    f"Restarted telegram-bots ({check.name} was down)",
                    True,
                ))
            except Exception as e:
                fixes.append(FixResult(
                    f"Failed to restart telegram-bots: {e}",
                    False,
                ))
            break  # Only restart once even if multiple bots down

        # Stale Twitter cookies → flag for Mac refresh
        if check.category == "cookies" and check.name == "twitter":
            flag = BASE_DIR / ".cookies_need_refresh"
            if not flag.exists():
                flag.write_text("stale")
                fixes.append(FixResult("Flagged Twitter cookies for Mac refresh", True))

        # MCP service down → restart
        if check.category == "services":
            svc_name = "xhs-mcp" if "xhs" in check.name else "douyin-mcp"
            try:
                subprocess.run(
                    ["sudo", "systemctl", "restart", svc_name],
                    timeout=10, capture_output=True,
                )
                await asyncio.sleep(3)
                fixes.append(FixResult(f"Restarted {svc_name}", True))
            except Exception as e:
                fixes.append(FixResult(f"Failed to restart {svc_name}: {e}", False))

        # Error spike → clear stale caches (common cause)
        if check.category == "logs" and not check.ok:
            cleared = []
            for cache_name in [
                ".reddit_cache.json", ".xcurate_prefetch.json",
                ".china_trends_cache.json", ".youtube_cache.json",
                ".podcast_cache.json",
            ]:
                cache_path = BASE_DIR / cache_name
                if cache_path.exists():
                    age_h = (time.time() - cache_path.stat().st_mtime) / 3600
                    if age_h > 6:
                        cache_path.unlink()
                        cleared.append(cache_name)
            if cleared:
                fixes.append(FixResult(
                    f"Cleared {len(cleared)} stale caches",
                    True, detail=", ".join(cleared),
                ))

    # Disk cleanup if needed
    disk_check = next((c for c in issues if c.name == "disk" and not c.ok), None)
    if disk_check:
        cleaned_mb = 0
        # Truncate large log files
        for log_path in Path("/tmp").glob("*.log"):
            try:
                size_mb = log_path.stat().st_size / (1024 * 1024)
                if size_mb > 10:
                    # Keep last 1000 lines
                    lines = log_path.read_text().splitlines()[-1000:]
                    log_path.write_text("\n".join(lines) + "\n")
                    cleaned_mb += size_mb - (log_path.stat().st_size / (1024 * 1024))
            except Exception as e:
                log.debug("cleanup skip: %s", e)
        # Delete old log backups
        for old in Path("/tmp").glob("*.log.old"):
            try:
                size_mb = old.stat().st_size / (1024 * 1024)
                old.unlink()
                cleaned_mb += size_mb
            except Exception as e:
                log.debug("cleanup skip: %s", e)
        # Clean __pycache__
        for pc in BASE_DIR.rglob("__pycache__"):
            try:
                import shutil
                size_mb = sum(f.stat().st_size for f in pc.rglob("*")) / (1024 * 1024)
                shutil.rmtree(pc)
                cleaned_mb += size_mb
            except Exception as e:
                log.debug("cleanup skip: %s", e)
        if cleaned_mb > 0:
            fixes.append(FixResult(f"Freed ~{cleaned_mb:.0f}MB disk space", True))

    return fixes


# ── Phase 3: Git Commit Patches ───────────────────────────────────────


def git_commit_fixes(fixes: list[FixResult]) -> Optional[str]:
    """Commit any file changes from fixes. Returns commit hash or None."""
    changed_files = []
    for f in fixes:
        changed_files.extend(f.files_changed)

    if not changed_files:
        return None

    try:
        # Check if files are git-tracked and modified
        r = subprocess.run(
            ["git", "diff", "--name-only"] + changed_files,
            capture_output=True, text=True, cwd=BASE_DIR, timeout=10,
        )
        modified = [f for f in r.stdout.strip().split("\n") if f]
        if not modified:
            return None

        summary = "; ".join(f.action for f in fixes if f.success)[:100]
        subprocess.run(["git", "add"] + modified, cwd=BASE_DIR, timeout=10)
        subprocess.run(
            ["git", "commit", "-m", f"[morning-report] Auto-fix: {summary}"],
            cwd=BASE_DIR, timeout=10,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=BASE_DIR, timeout=30,
        )
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=5,
        )
        return r.stdout.strip()
    except Exception as e:
        log.error(f"Git commit failed: {e}")
        return None


# ── Phase 4: LLM Anomaly Interpretation ──────────────────────────────


async def interpret_anomalies(
    issues: list[HealthCheck],
    fixes: list[FixResult],
    history: list[dict],
) -> Optional[str]:
    """Call LLM only if unfixed anomalies remain. Returns analysis or None."""
    unfixed = [c for c in issues if not c.ok]

    # Remove issues that were successfully fixed
    fixed_names = {f.action.split("(")[-1].rstrip(")") for f in fixes if f.success}

    # Check if any real anomalies remain after fixes
    ok_fixes = [f for f in fixes if f.success]
    remaining = []
    for c in unfixed:
        # Skip if this was fixed by a service restart or cache clear
        if c.name in ("bot1", "bot2", "admin"):
            if any("Restarted" in f.action for f in ok_fixes):
                continue
        if c.category == "cookies":
            if any("cookies" in f.action.lower() for f in ok_fixes):
                continue
        if c.category == "services":
            svc = c.name.replace("_", "-")
            if any(svc in f.action for f in ok_fixes):
                continue
        if c.category == "logs":
            if any("cache" in f.action.lower() for f in ok_fixes):
                continue
        remaining.append(c)

    if not remaining:
        return None

    # Check for persistent patterns (same issue 3+ days in recent history)
    persistent = []
    for c in remaining:
        days_failed = sum(
            1 for h in history[-7:]
            if c.name in h.get("failed_names", [])
        )
        if days_failed >= 3:
            persistent.append(f"{c.name} ({days_failed}/7 days)")

    issue_text = "\n".join(
        f"- {c.category}/{c.name}: {c.detail}" for c in remaining
    )

    # Read recent errors for context
    recent_errors = ""
    try:
        with open("/tmp/start_all.log") as f:
            lines = f.readlines()[-200:]
        error_lines = [
            ln.strip() for ln in lines if "ERROR" in ln
        ][-10:]
        if error_lines:
            recent_errors = "\n".join(error_lines)
    except Exception:
        pass

    prompt_parts = [
        "System morning check found anomalies after auto-fixes:",
        "",
        issue_text,
    ]
    if persistent:
        joined = "\n".join(f"  ⚠️ {p}" for p in persistent)
        prompt_parts.extend(["", "Persistent (3+ days):", joined])
    if recent_errors:
        prompt_parts.extend(["", "Recent errors:", recent_errors])
    prompt_parts.extend([
        "",
        "For each: root cause (1 line), fix, "
        "urgency (can-wait/fix-today/fix-now).",
        "Be terse. 3 lines max per issue.",
    ])

    try:
        from llm_client import chat_completion
        analysis = chat_completion(
            messages=[{"role": "user", "content": "\n".join(prompt_parts)}],
            max_tokens=500,
        )
        return analysis if analysis else None
    except Exception as e:
        log.error(f"LLM interpretation failed: {e}")
        return None


# ── Phase 5: Format & Send Report ─────────────────────────────────────


def format_report(
    checks: list[HealthCheck],
    fixes: list[FixResult],
    analysis: Optional[str],
    commit_hash: Optional[str],
) -> str:
    """Format the morning report for Telegram."""
    now = datetime.now(HKT)
    lines = [f"<b>☀️ {now.strftime('%H:%M')} Morning Report</b>", ""]

    # Group checks by category
    by_cat: dict[str, list[HealthCheck]] = {}
    for c in checks:
        by_cat.setdefault(c.category, []).append(c)

    # Bots
    bots = by_cat.get("bots", [])
    bot_parts = []
    for b in bots:
        if b.name == "systemd":
            continue
        bot_parts.append(f"{'✅' if b.ok else '❌'}{b.name}")
    lines.append(f"<b>Bots:</b> {' '.join(bot_parts)}")

    # Digests (yesterday)
    digests = by_cat.get("digests", [])
    ok_digests = [d for d in digests if d.ok]
    fail_digests = [d for d in digests if not d.ok]
    digest_status = f"{len(ok_digests)}/{len(digests)}"
    if fail_digests:
        failed_names = ", ".join(d.name for d in fail_digests)
        lines.append(f"<b>Digests:</b> {digest_status} ⚠️ missed: {failed_names}")
    else:
        lines.append(f"<b>Digests:</b> {digest_status} ✅")

    # System
    system = by_cat.get("system", [])
    sys_parts = []
    for s in system:
        if s.name == "uptime":
            sys_parts.append(f"up {s.detail}")
        else:
            icon = "✅" if s.ok else "⚠️"
            sys_parts.append(f"{s.name} {s.detail}{'' if s.ok else icon}")
    lines.append(f"<b>System:</b> {' | '.join(sys_parts)}")

    # Cookies
    cookies = by_cat.get("cookies", [])
    if cookies:
        c = cookies[0]
        icon = "✅" if c.ok else "⚠️"
        lines.append(f"<b>Cookies:</b> Twitter {c.detail} {icon}")

    # Services
    services = by_cat.get("services", [])
    if services:
        svc_parts = []
        for s in services:
            svc_parts.append(f"{'✅' if s.ok else '❌'}{s.name}")
        lines.append(f"<b>Services:</b> {' '.join(svc_parts)}")

    # Logs
    logs = by_cat.get("logs", [])
    if logs and not logs[0].ok:
        lines.append(f"<b>Logs:</b> ⚠️ {logs[0].detail}")

    lines.append("")

    # All green?
    all_ok = all(c.ok for c in checks)
    if all_ok and not fixes:
        lines.append("All green. ☕")
        return "\n".join(lines)

    # Fixes applied
    successful_fixes = [f for f in fixes if f.success]
    if successful_fixes:
        lines.append("<b>🔧 Fixed:</b>")
        for f in successful_fixes:
            lines.append(f"  • {f.action}")
        if commit_hash:
            lines.append(f"  📦 Committed: {commit_hash}")
        lines.append("")

    failed_fixes = [f for f in fixes if not f.success]
    if failed_fixes:
        lines.append("<b>❌ Fix failed:</b>")
        for f in failed_fixes:
            lines.append(f"  • {f.action}")
        lines.append("")

    # Remaining issues (not fixed)
    remaining = [c for c in checks if not c.ok]
    if remaining and not all(f.success for f in fixes):
        unfixed = []
        for c in remaining:
            # Skip if fixed
            restarted = any(
                "Restarted" in f.action for f in successful_fixes
            )
            cookies_fixed = any(
                "cookies" in f.action.lower()
                for f in successful_fixes
            )
            if c.category == "bots" and restarted:
                continue
            if c.category == "cookies" and cookies_fixed:
                continue
            unfixed.append(c)
        if unfixed:
            lines.append("<b>⚠️ Needs attention:</b>")
            for c in unfixed:
                lines.append(f"  • {c.category}/{c.name}: {c.detail}")
            lines.append("")

    # LLM analysis
    if analysis:
        lines.append(f"<b>🤖 Analysis:</b>\n{analysis[:1000]}")

    return "\n".join(lines)


async def send_report(text: str):
    """Send to Telegram."""
    from telegram import Bot
    bot = Bot(token=BOT_TOKEN)
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            message_thread_id=THREAD_ID,
            text=text[:4000],
            parse_mode="HTML",
        )
    except Exception as e:
        log.error(f"Failed to send report: {e}")
        # Try without HTML
        try:
            import re
            plain = re.sub(r"<[^>]+>", "", text)
            await bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=THREAD_ID,
                text=plain[:4000],
            )
        except Exception as e2:
            log.error(f"Failed to send plain report: {e2}")


# ── History ───────────────────────────────────────────────────────────


def load_history() -> list[dict]:
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return []


def save_history(history: list[dict]):
    # Keep last 30 days
    cutoff = (datetime.now(HKT) - timedelta(days=30)).isoformat()
    history = [h for h in history if h.get("date", "") > cutoff[:10]]
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


# ── Main ──────────────────────────────────────────────────────────────


async def main(dry_run: bool = False):
    log.info("Morning Report starting...")

    # Phase 1: Collect
    checks = await collect_health()
    all_ok = all(c.ok for c in checks)
    ok_count = sum(1 for c in checks if c.ok)
    log.info(f"Health checks: {ok_count}/{len(checks)} OK")

    # Phase 2: Auto-fix
    fixes = []
    if not all_ok:
        fixes = await auto_fix(checks)
        ok_fixes = sum(1 for f in fixes if f.success)
        log.info(f"Auto-fixes: {len(fixes)} applied, {ok_fixes} ok")

    # Phase 3: Git commit
    commit_hash = None
    if fixes:
        commit_hash = git_commit_fixes(fixes)
        if commit_hash:
            log.info(f"Committed fix: {commit_hash}")

    # Phase 4: LLM interpretation (only if anomalies remain)
    analysis = None
    if not all_ok:
        history = load_history()
        analysis = await interpret_anomalies(checks, fixes, history)

    # Phase 5: Format and send
    report = format_report(checks, fixes, analysis, commit_hash)

    if dry_run:
        # Strip HTML for terminal
        import re
        print(re.sub(r"<[^>]+>", "", report))
    else:
        await send_report(report)
        log.info("Report sent to Telegram")

    # Save history
    history = load_history()
    history.append({
        "date": datetime.now(HKT).strftime("%Y-%m-%d"),
        "ts": datetime.now(HKT).isoformat(),
        "checks_total": len(checks),
        "checks_ok": sum(1 for c in checks if c.ok),
        "all_green": all_ok,
        "fixes_applied": [f.action for f in fixes if f.success],
        "fixes_failed": [f.action for f in fixes if not f.success],
        "failed_names": [c.name for c in checks if not c.ok],
        "analysis_called": analysis is not None,
        "commit": commit_hash,
    })
    save_history(history)

    log.info("Morning Report complete")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry))
