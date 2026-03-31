#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Auto-Healer — universal self-diagnostic for the ENTIRE system.
Detects failures across ALL components, diagnoses root cause, auto-fixes.

Covers: digests (news/X/Reddit/crypto), Team A Scout, evolution digest,
MCP servers, bot crashes, stuck processes, cookie staleness,
send failures, empty sections — EVERYTHING.

Runs every 3 hours via cron. Reads all logs, checks all processes, fixes what it can."""

import asyncio
import json
import os
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("auto_healer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
CHAT_ID = int(os.environ.get("PERSONAL_GROUP_ID", "0"))
THREAD_ID = int(os.environ.get("HEALER_THREAD_ID", "0"))  # Healer/Heartbeat thread
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".claude/local/claude"))
HKT = timezone(timedelta(hours=8))

LOG_FILES = {
    "bots": "/tmp/start_all.log",
    "admin": "/tmp/admin_bot.log",
    "xhs_mcp": "/tmp/xhs-mcp-py.log",
    "douyin_mcp": "/tmp/dy-mcp-py.log",
    "ai_digest": "/tmp/ai_digest.log",
    "china_trends": "/tmp/china_trends.log",
    "code_review": "/tmp/code_review.log",
    "reddit_digest": "/tmp/reddit_digest.log",
    "xdigest": "/tmp/xdigest.log",
}

HEALER_LOG = str(BASE_DIR / ".healer_history.json")
ALERTED_FILE = str(BASE_DIR / ".healer_alerted.json")


def load_history() -> list:
    try:
        with open(HEALER_LOG) as f:
            return json.load(f)
    except Exception:
        return []


def save_history(h: list):
    with open(HEALER_LOG, "w") as f:
        json.dump(h[-100:], f, indent=2)  # Keep last 100


def _issue_key(issue: dict) -> str:
    """Unique key for dedup: type + component."""
    return f"{issue['type']}:{issue['component']}"


def load_alerted() -> dict:
    """Load already-alerted issues {key: timestamp}."""
    try:
        with open(ALERTED_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_alerted(alerted: dict):
    with open(ALERTED_FILE, "w") as f:
        json.dump(alerted, f, indent=2)


def dedup_issues(issues: list[dict]) -> list[dict]:
    """Filter out issues that were already alerted within the last 24h."""
    alerted = load_alerted()
    now = time.time()
    new_issues = []
    for i in issues:
        key = _issue_key(i)
        last_alert = alerted.get(key, 0)
        if now - last_alert > 86400:  # Only re-alert after 24h
            new_issues.append(i)
    return new_issues


def mark_alerted(issues: list[dict]):
    """Mark issues as alerted so they don't repeat."""
    alerted = load_alerted()
    now = time.time()
    for i in issues:
        alerted[_issue_key(i)] = now
    # Clean entries older than 48h
    alerted = {k: v for k, v in alerted.items() if now - v < 172800}
    save_alerted(alerted)


def read_log(path: str, lines: int = 500) -> str:
    try:
        with open(path) as f:
            return "".join(f.readlines()[-lines:])
    except Exception:
        return ""


async def check_process(name: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "pgrep", "-f", name,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return bool(stdout.decode().strip())


async def check_port(port: int) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "curl", "-sf", "--max-time", "5", f"http://localhost:{port}/health",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def detect_all_issues() -> list[dict]:
    """Comprehensive system scan."""
    issues = []
    now = datetime.now(HKT)
    bot_log = read_log("/tmp/start_all.log", 2000)

    # ── 1. Bot processes ──
    for bot_id in ["bot1", "bot2", "reddit"]:
        if not await check_process(f"run_bot.py {bot_id}"):
            issues.append({"type": "bot_down", "severity": "CRITICAL",
                           "component": bot_id, "details": f"Bot {bot_id} not running"})

    if not await check_process("admin_bot"):
        issues.append({"type": "bot_down", "severity": "CRITICAL",
                       "component": "admin", "details": "Admin bot not running"})

    # ── 2. MCP servers ──
    if not await check_port(18060):
        issues.append({"type": "service_down", "severity": "HIGH",
                       "component": "xhs_mcp", "details": "XHS MCP (port 18060) not responding"})
    if not await check_port(18070):
        issues.append({"type": "service_down", "severity": "HIGH",
                       "component": "douyin_mcp", "details": "Douyin MCP (port 18070) not responding"})

    # ── 3. OpenClaw ──
    if not await check_port(18789):
        issues.append({"type": "service_down", "severity": "LOW",
                       "component": "openclaw", "details": "OpenClaw gateway not responding"})

    # ── 4. Empty digest sections ──
    empty_sections = re.findall(r'Category (\w+/\w+): 0 articles', bot_log)
    if empty_sections:
        issues.append({"type": "empty_sections", "severity": "HIGH",
                       "component": "news_digest",
                       "details": f"Empty sections: {', '.join(empty_sections)}",
                       "data": empty_sections})

    # ── 5. Dead RSS feeds ──
    zero_feeds = re.findall(r'RSS\s+(\S.*?)\s+→ 0 articles', bot_log)
    if zero_feeds:
        issues.append({"type": "dead_feeds", "severity": "HIGH",
                       "component": "news_digest",
                       "details": f"0-article feeds: {', '.join(zero_feeds)}",
                       "data": zero_feeds})

    # ── 6. Dead scrapers ──
    zero_scrapers = re.findall(r'SCRP\s+(\S.*?)\s+→ 0 articles', bot_log)
    if zero_scrapers:
        issues.append({"type": "dead_scrapers", "severity": "HIGH",
                       "component": "news_digest",
                       "details": f"0-article scrapers: {', '.join(zero_scrapers)}",
                       "data": zero_scrapers})

    # ── 7. Digest send failures ──
    send_failures = re.findall(r'⚠️ (\w+).*?(\d+) FAILED', bot_log)
    if send_failures:
        issues.append({"type": "send_failures", "severity": "MEDIUM",
                       "component": "digests",
                       "details": f"Send failures: {send_failures}"})

    # ── 8. Reddit blocked ──
    reddit_empty = bot_log.count("Daily Reddit digest: no posts")
    if reddit_empty > 2:
        issues.append({"type": "reddit_blocked", "severity": "HIGH",
                       "component": "reddit_digest",
                       "details": f"Reddit blocked {reddit_empty}x — VPS IP issue"})

    # ── 9. X digest failures ──
    x_failures = re.findall(r'X digest failed: (.+)', bot_log)
    if x_failures:
        issues.append({"type": "x_digest_failed", "severity": "HIGH",
                       "component": "x_digest",
                       "details": f"X digest errors: {x_failures[-1][:100]}"})

    # ── 10. Team A Scout failures ──
    scout_fail = "Scout" in bot_log and "failed" in bot_log.lower()
    scout_log = read_log("/tmp/start_all.log", 500)
    team_a_errors = re.findall(r'team_a.*(?:error|failed|exception)', scout_log, re.IGNORECASE)
    if team_a_errors:
        issues.append({"type": "team_a_failed", "severity": "MEDIUM",
                       "component": "team_a_scout",
                       "details": f"Team A Scout errors: {len(team_a_errors)}"})

    # ── 11. Cookie staleness ──
    cookie_path = BASE_DIR / "twitter_cookies.json"
    if cookie_path.exists():
        age_h = (time.time() - cookie_path.stat().st_mtime) / 3600
        if age_h > 36:
            issues.append({"type": "stale_cookies", "severity": "MEDIUM",
                           "component": "twitter_cookies",
                           "details": f"Twitter cookies {int(age_h)}h old"})

    # ── 12. Disk space ──
    proc = await asyncio.create_subprocess_exec(
        "df", "--output=pcent", "/", stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    pct = int(stdout.decode().strip().split("\n")[-1].strip().rstrip("%"))
    if pct >= 80:
        issues.append({"type": "disk_full", "severity": "HIGH",
                       "component": "system",
                       "details": f"Disk at {pct}%"})

    # ── 13. Memory ──
    proc = await asyncio.create_subprocess_exec("free", "-m", stdout=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    parts = stdout.decode().strip().split("\n")[1].split()
    mem_pct = int(int(parts[2]) / int(parts[1]) * 100)
    if mem_pct >= 90:
        issues.append({"type": "memory_high", "severity": "HIGH",
                       "component": "system",
                       "details": f"Memory at {mem_pct}%"})

    # ── 14. Bot stuck (ERROR spike) ──
    recent_errors = bot_log.count("ERROR")
    if recent_errors > 20:
        # Check if same error repeating
        error_lines = [l for l in bot_log.split("\n") if "ERROR" in l]
        if error_lines:
            issues.append({"type": "error_spike", "severity": "HIGH",
                           "component": "bots",
                           "details": f"{recent_errors} ERRORs. Latest: {error_lines[-1][:100]}"})

    # ── 15. Evolution/AI digest failures ──
    # Only alert if it didn't complete — partial failures are normal
    ai_log = read_log("/tmp/ai_digest.log", 100)
    ai_has_errors = "error" in ai_log.lower() or "failed" in ai_log.lower()
    ai_completed = "complete" in ai_log.lower() or "sent" in ai_log.lower()
    if ai_has_errors and not ai_completed:
        issues.append({"type": "ai_digest_failed", "severity": "LOW",
                       "component": "ai_digest",
                       "details": "AI digest failed to complete"})

    # ── 16. China trends failures ──
    # Only alert if it didn't complete — partial source failures are normal
    cn_log = read_log("/tmp/china_trends.log", 100)
    cn_has_errors = "error" in cn_log.lower() or "failed" in cn_log.lower()
    cn_completed = "china trends complete" in cn_log.lower()
    if cn_has_errors and not cn_completed:
        issues.append({"type": "china_trends_failed", "severity": "LOW",
                       "component": "china_trends",
                       "details": "China trends failed to complete"})

    # ── 17. Cron job didn't run (check flag files) ──
    today = now.strftime("%Y-%m-%d")
    expected_flags = {
        "news_bot1": BASE_DIR / ".digest_sent_news_bot1",
        "news_bot2": BASE_DIR / ".digest_sent_news_bot2",
        "x_twitter": BASE_DIR / ".digest_sent_x_twitter",
        "x_xcn": BASE_DIR / ".digest_sent_x_xcn",
        "x_xai": BASE_DIR / ".digest_sent_x_xai",
        "x_xniche": BASE_DIR / ".digest_sent_x_xniche",
        "reddit_reddit": BASE_DIR / ".digest_sent_reddit_reddit",
    }
    # Digests running at 15:00-16:00 HKT — check after 17:00 HKT
    late_flags = {
        "youtube":    BASE_DIR / ".youtube_digest_sent",
        "podcast_cn": BASE_DIR / ".podcast_digest_sent",
        "podcast_en": BASE_DIR / ".podcast_en_digest_sent",
        "evolution":  BASE_DIR / ".evolution_feed_sent",
    }

    def _check_flags(flags: dict):
        for name, flag_path in flags.items():
            if flag_path.exists():
                content = flag_path.read_text().strip()
                if today not in content:
                    issues.append({"type": "digest_not_sent", "severity": "HIGH",
                                   "component": name,
                                   "details": f"{name} digest not sent today"})
            else:
                issues.append({"type": "digest_not_sent", "severity": "HIGH",
                               "component": name,
                               "details": f"{name} flag file missing"})

    # Only check after 12:30 HKT (digests should be done by then)
    if now.hour > 12 or (now.hour == 12 and now.minute >= 30):
        _check_flags(expected_flags)
    if now.hour >= 17:
        _check_flags(late_flags)

    return issues


async def auto_fix(issues: list[dict]):
    """Run Claude Code to fix issues. Only for fixable ones."""
    # Separate auto-fixable from alert-only
    fixable = [i for i in issues if i["type"] in (
        "empty_sections", "dead_feeds", "dead_scrapers", "error_spike",
        "bot_down", "service_down", "digest_not_sent",
    )]
    alert_only = [i for i in issues if i not in fixable]

    # Dedup: skip issues already alerted in last 24h
    alert_only = dedup_issues(alert_only)
    fixable = dedup_issues(fixable)

    from telegram import Bot
    bot = Bot(token=BOT_TOKEN)
    severity_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}

    if fixable:
        # Build fix prompt
        issue_text = "\n".join([
            f"- [{i['severity']}] {i['component']}: {i['details']}"
            for i in fixable
        ])

        prompt = (
            f"Auto-Healer detected {len(fixable)} fixable issues:\n\n"
            f"{issue_text}\n\n"
            f"Read the relevant logs first:\n"
            f"- tail -200 /tmp/start_all.log (for bot/digest issues)\n"
            f"- Check the specific component's code\n\n"
            f"For each issue:\n"
            f"1. Diagnose ROOT CAUSE from logs and code\n"
            f"2. Fix the root cause (not a band-aid)\n"
            f"3. For dead feeds/scrapers: test URL, find new URL if moved, update code\n"
            f"4. For bot_down: check why it crashed, fix the crash, it'll auto-restart\n"
            f"5. For service_down: restart the service via systemctl\n"
            f"6. For digest_not_sent: check why cron failed, fix and re-trigger\n"
            f"7. Test your fix works\n"
            f"8. Commit with message starting with '[auto-healer]'\n"
            f"9. Report what you fixed\n\n"
            f"If unsure about a fix, report but DON'T change code."
        )

        log.info(f"Auto-fixing {len(fixable)} issues via Claude Code...")

        try:
            claude_bin = CLAUDE_BIN
            if not os.path.exists(claude_bin):
                for path in ["/usr/local/bin/claude", str(Path.home() / ".local/bin/claude")]:
                    if os.path.exists(path):
                        claude_bin = path
                        break

            args = [claude_bin, "-p", "--verbose",
                    "--model", "claude-sonnet-4-6",
                    "--dangerously-skip-permissions",
                    "--output-format", "json"]
            env = os.environ.copy()
            for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
                env.pop(k, None)

            proc = await asyncio.create_subprocess_exec(
                *args, cwd=str(BASE_DIR),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()), timeout=600
            )
            output = stdout.decode().strip()
            result = ""
            try:
                data = json.loads(output)
                if isinstance(data, dict):
                    result = data.get("result", output[:3000])
                elif isinstance(data, list):
                    # Claude JSON output is a list of message blocks
                    texts = []
                    for item in data:
                        if isinstance(item, dict):
                            # ResultMessage has "result" key
                            if "result" in item:
                                texts.append(item["result"])
                            # AssistantMessage has "content" with text blocks
                            elif "content" in item and isinstance(item["content"], list):
                                for block in item["content"]:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        texts.append(block.get("text", ""))
                    result = "\n".join(texts)[:3000] if texts else output[:3000]
                else:
                    result = str(data)[:3000]
            except json.JSONDecodeError:
                result = output[:3000] if output else "(no output)"

            # Report
            fix_summary = "\n".join([
                f"{severity_emoji.get(i['severity'], '⚪')} {i['details'][:60]}"
                for i in fixable
            ])
            text = f"🔧 <b>Auto-Healer Fixed</b>\n\n{fix_summary}\n\n{result[:2000]}"
            try:
                await bot.send_message(chat_id=CHAT_ID, message_thread_id=THREAD_ID, text=text[:4000], parse_mode="HTML")
            except Exception:
                await bot.send_message(chat_id=CHAT_ID, message_thread_id=THREAD_ID,
                                       text=text[:4000], parse_mode="HTML")

        except asyncio.TimeoutError:
            log.warning("Auto-healer timed out")
            await bot.send_message(chat_id=CHAT_ID, message_thread_id=THREAD_ID,
                                   text="⚠️ Auto-healer timed out (10 min)")
        except Exception as e:
            log.error(f"Auto-healer failed: {e}")

    # Alert-only issues (can't auto-fix) — only NEW ones (deduped)
    if alert_only:
        alert_text = "\n".join([
            f"{severity_emoji.get(i['severity'], '⚪')} [{i['component']}] {i['details'][:80]}"
            for i in alert_only
        ])
        text = f"🫀 <b>Auto-Healer Alert</b> (manual fix needed)\n\n{alert_text}"
        try:
            await bot.send_message(chat_id=CHAT_ID, message_thread_id=THREAD_ID, text=text[:4000], parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id=CHAT_ID, message_thread_id=THREAD_ID,
                                   text=text[:4000], parse_mode="HTML")
        mark_alerted(alert_only)

    # Mark fixable as alerted too
    if fixable:
        mark_alerted(fixable)

    # Save history
    history = load_history()
    history.append({
        "timestamp": datetime.now(HKT).isoformat(),
        "issues_found": len(issues),
        "fixed": len(fixable),
        "alert_only": len(alert_only),
        "types": [i["type"] for i in issues],
    })
    save_history(history)


async def main():
    log.info("Auto-Healer scanning entire system...")
    issues = await detect_all_issues()

    if issues:
        log.info(f"Detected {len(issues)} issues")
        await auto_fix(issues)
    else:
        log.info("All systems healthy")


if __name__ == "__main__":
    asyncio.run(main())
