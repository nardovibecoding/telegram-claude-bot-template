#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Standalone daily code review — run via cron at 18:00 HKT.
Uses Claude Code to review codebase with rotating spotlight areas.

Enhanced with auto-fix:
- SAFE issues (unused imports, dead vars, formatting) → auto-fix → commit → push
- RISKY issues (logic, error handling, API changes) → branch → TG notification with [Fix] [Skip]
- Auto-revert if any bot crashes within 5 min of safe fix commit
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("code_review")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
CHAT_ID = int(os.environ.get("PERSONAL_GROUP_ID", "0"))  # Admin group
THREAD_ID = 147  # Code review / audit thread
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", os.path.expanduser("~/.claude/local/claude"))
HKT = timezone(timedelta(hours=8))

REVIEW_CHECKLIST = str(BASE_DIR / "references" / "review-checklist.md")

# Extra spotlight by day of week (on top of the full checklist)
DAILY_SPOTLIGHT = {
    0: "Code quality & dead code",
    1: "Performance & caching",
    2: "Reliability & retry logic",
    3: "Security & authorization",
    4: "Architecture & duplication",
    5: "Digest/cron operational health",
    6: "Feature ideas (top 3 most impactful)",
}

SENT_FLAG = str(BASE_DIR / ".code_review_sent")
AUTOFIX_COMMIT_FLAG = "/tmp/.autofix_commit_hash"

# Files that are RISKY to auto-fix — changes go to branch instead
RISKY_FILES = {"auto_reply.py", "bridge.py", "admin_bot.py", "start_all.sh", "llm_client.py"}

# SAFE issue patterns — can be auto-fixed without review
SAFE_PATTERNS = [
    re.compile(r"unused\s+import", re.IGNORECASE),
    re.compile(r"dead\s+(variable|code|file)", re.IGNORECASE),
    re.compile(r"formatting|whitespace|trailing\s+space", re.IGNORECASE),
    re.compile(r"typo\s+in\s+comment", re.IGNORECASE),
    re.compile(r"unreachable\s+code", re.IGNORECASE),
    re.compile(r"unused\s+variable", re.IGNORECASE),
    re.compile(r"import\s+not\s+used", re.IGNORECASE),
    re.compile(r"redundant\s+(import|pass|return)", re.IGNORECASE),
    re.compile(r"empty\s+(except|if|else)", re.IGNORECASE),
]

# RISKY issue patterns — need human review
RISKY_PATTERNS = [
    re.compile(r"logic\s+(change|error|bug|issue)", re.IGNORECASE),
    re.compile(r"error\s+handling", re.IGNORECASE),
    re.compile(r"api\s+(change|endpoint|key)", re.IGNORECASE),
    re.compile(r"config\s+(change|update|missing)", re.IGNORECASE),
    re.compile(r"security|auth|token|secret", re.IGNORECASE),
    re.compile(r"race\s+condition|deadlock|concurren", re.IGNORECASE),
    re.compile(r"data\s+loss|corruption", re.IGNORECASE),
]


def _find_claude_bin() -> str | None:
    """Find the Claude binary."""
    if os.path.exists(CLAUDE_BIN):
        return CLAUDE_BIN
    for path in ["/usr/local/bin/claude", os.path.expanduser("~/.local/bin/claude")]:
        if os.path.exists(path):
            return path
    return None


def _classify_issue(title: str, file_path: str) -> str:
    """Classify an issue as SAFE or RISKY based on patterns and file."""
    # Any mention of risky files -> RISKY
    basename = os.path.basename(file_path) if file_path else ""
    if basename in RISKY_FILES:
        return "RISKY"

    # Check against risky patterns first (higher priority)
    for pattern in RISKY_PATTERNS:
        if pattern.search(title):
            return "RISKY"

    # Check safe patterns
    for pattern in SAFE_PATTERNS:
        if pattern.search(title):
            return "SAFE"

    # Default: RISKY (conservative)
    return "RISKY"


def _parse_review_findings(result: str) -> list[dict]:
    """Parse review output into structured findings.

    Expected format:
        - **[SEVERITY]** one-line title
        - File:line
        - WHY
        - Fix snippet
    """
    findings = []

    # Split on finding headers: - **[SEVERITY]**
    finding_pattern = re.compile(
        r"-\s*\*\*\[(\w+)\]\*\*\s*(.*?)(?=(?:-\s*\*\*\[|\Z))",
        re.DOTALL,
    )

    for m in finding_pattern.finditer(result):
        severity = m.group(1).strip()
        body = m.group(2).strip()

        # Extract title (first line)
        lines = body.split("\n")
        title = lines[0].strip() if lines else ""

        # Extract file path
        file_path = ""
        for line in lines:
            line = line.strip()
            if line.startswith("- File:") or line.startswith("File:"):
                file_path = line.split(":", 1)[1].strip() if ":" in line else ""
                break

        # Classify
        classification = _classify_issue(title, file_path)

        findings.append({
            "severity": severity,
            "title": title,
            "file_path": file_path,
            "body": body,
            "classification": classification,
        })

    return findings


async def _run_claude_review(claude_bin: str, prompt: str) -> str:
    """Run Claude Code review and return the result text."""
    review_args = [claude_bin, "-p", "--verbose",
                   "--model", "claude-sonnet-4-6",
                   "--allowedTools", "Read,Glob,Grep,Bash",
                   "--output-format", "json"]
    env = os.environ.copy()
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)

    proc = await asyncio.create_subprocess_exec(
        *review_args, cwd=str(BASE_DIR),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(input=prompt.encode()), timeout=600)
    output = stdout.decode().strip()

    result = ""
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            result = data.get("result", output)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("type") == "result":
                    result = item.get("result", "")
                    break
            if not result:
                result = output
        else:
            result = output
    except json.JSONDecodeError:
        result = output

    return result or "(no output from review)"


async def _auto_fix_safe_issues(claude_bin: str, safe_findings: list[dict]) -> bool:
    """Use Claude to auto-fix SAFE issues, commit, and push."""
    if not safe_findings:
        return False

    issues_text = "\n".join(
        f"- {f['title']} (File: {f['file_path']})\n  {f['body'][:200]}"
        for f in safe_findings
    )

    fix_prompt = (
        "Fix these SAFE code issues. Only make minimal, targeted fixes:\n\n"
        f"{issues_text}\n\n"
        "Rules:\n"
        "- Only fix unused imports, dead variables, formatting, typos in comments\n"
        "- Do NOT change any logic, error handling, or API calls\n"
        "- Do NOT modify auto_reply.py, bridge.py, admin_bot.py, or start_all.sh\n"
        "- Run py_compile on every modified .py file to verify syntax\n"
        "- If a fix is uncertain, skip it\n"
        "- After fixing, run: git add <files> && git commit -m 'autofix: <summary>'\n"
        "- Then run: git push origin main"
    )

    try:
        fix_args = [claude_bin, "-p", "--verbose",
                    "--model", "claude-sonnet-4-6",
                    "--allowedTools", "Read,Edit,Glob,Grep,Bash",
                    "--output-format", "json"]
        env = os.environ.copy()
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            env.pop(k, None)

        proc = await asyncio.create_subprocess_exec(
            *fix_args, cwd=str(BASE_DIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        await asyncio.wait_for(proc.communicate(input=fix_prompt.encode()), timeout=600)

        # Record the commit hash for auto-revert monitoring
        result = subprocess.run(
            ["git", "-C", str(BASE_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            commit_hash = result.stdout.strip()
            with open(AUTOFIX_COMMIT_FLAG, "w") as f:
                f.write(f"{commit_hash}\n{datetime.now(timezone.utc).isoformat()}\n")
            log.info("Safe auto-fix committed: %s", commit_hash)

            # Schedule auto-revert check in 5 minutes
            asyncio.create_task(_schedule_revert_check(commit_hash))
            return True

    except asyncio.TimeoutError:
        log.warning("Auto-fix timed out")
    except Exception as e:
        log.error("Auto-fix failed: %s", e)

    return False


async def _schedule_revert_check(commit_hash: str):
    """Wait 5 min, then check if any bot crashed since the auto-fix commit."""
    await asyncio.sleep(300)  # 5 minutes

    try:
        # Check if any bot processes have crashed
        log_path = "/tmp/start_all.log"
        if not os.path.exists(log_path):
            log.info("No start_all.log -- skipping revert check")
            return

        # Read the flag to get commit time
        if not os.path.exists(AUTOFIX_COMMIT_FLAG):
            return

        with open(AUTOFIX_COMMIT_FLAG) as f:
            lines = f.read().strip().split("\n")
            if len(lines) < 2:
                return
            stored_hash = lines[0]
            commit_time_str = lines[1]

        if stored_hash != commit_hash:
            return  # Different commit, skip

        commit_time = datetime.fromisoformat(commit_time_str)

        # Check log for crash messages after commit time
        result = subprocess.run(
            ["tail", "-100", log_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return

        log_content = result.stdout
        crash_keywords = ["stopped (exit code", "Traceback", "CRITICAL", "killed"]
        crash_detected = False

        for line in log_content.split("\n"):
            # Check if this line has a timestamp after our commit
            ts_match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
            if ts_match:
                try:
                    line_time = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                    line_time = line_time.replace(tzinfo=timezone.utc)
                    if line_time > commit_time:
                        for keyword in crash_keywords:
                            if keyword.lower() in line.lower():
                                crash_detected = True
                                log.warning("Crash detected after auto-fix: %s", line.strip()[:200])
                                break
                except ValueError:
                    pass
            if crash_detected:
                break

        if crash_detected:
            log.warning("Bot crash detected after auto-fix commit %s -- reverting", commit_hash[:8])

            # Revert the commit
            revert_result = subprocess.run(
                ["git", "-C", str(BASE_DIR), "revert", "--no-edit", commit_hash],
                capture_output=True, text=True, timeout=30,
            )
            if revert_result.returncode == 0:
                # Push the revert
                subprocess.run(
                    ["git", "-C", str(BASE_DIR), "push", "origin", "main"],
                    capture_output=True, text=True, timeout=30,
                )
                log.info("Auto-reverted commit %s", commit_hash[:8])

                # Notify
                _notify_tg(
                    f"Auto-revert: commit {commit_hash[:8]} reverted\n"
                    f"Bot crash detected within 5 min of auto-fix"
                )
            else:
                log.error("Revert failed: %s", revert_result.stderr[:200])
                _notify_tg(f"Auto-revert FAILED for {commit_hash[:8]} -- manual intervention needed")
        else:
            log.info("No crashes detected after auto-fix commit %s -- keeping changes", commit_hash[:8])

        # Clean up flag
        if os.path.exists(AUTOFIX_COMMIT_FLAG):
            os.remove(AUTOFIX_COMMIT_FLAG)

    except Exception as e:
        log.error("Revert check failed: %s", e)


async def _handle_risky_issues(claude_bin: str, risky_findings: list[dict], today: str) -> bool:
    """Create fixes for risky issues on a separate branch, notify via TG."""
    if not risky_findings:
        return False

    branch_name = f"autofix-{today}"

    # Create branch
    subprocess.run(
        ["git", "-C", str(BASE_DIR), "checkout", "-b", branch_name],
        capture_output=True, text=True, timeout=10,
    )

    issues_text = "\n".join(
        f"- [{f['severity']}] {f['title']} (File: {f['file_path']})\n  {f['body'][:200]}"
        for f in risky_findings
    )

    fix_prompt = (
        f"Fix these code issues on branch {branch_name}:\n\n"
        f"{issues_text}\n\n"
        "Rules:\n"
        "- Make careful, well-tested fixes\n"
        "- Run py_compile on every modified .py file\n"
        "- After fixing, run: git add <files> && git commit -m 'autofix: <summary>'\n"
        "- Then push: git push origin " + branch_name
    )

    try:
        fix_args = [claude_bin, "-p", "--verbose",
                    "--model", "claude-sonnet-4-6",
                    "--allowedTools", "Read,Edit,Glob,Grep,Bash",
                    "--output-format", "json"]
        env = os.environ.copy()
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
            env.pop(k, None)

        proc = await asyncio.create_subprocess_exec(
            *fix_args, cwd=str(BASE_DIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        await asyncio.wait_for(proc.communicate(input=fix_prompt.encode()), timeout=600)

        # Switch back to main
        subprocess.run(
            ["git", "-C", str(BASE_DIR), "checkout", "main"],
            capture_output=True, text=True, timeout=10,
        )

        # Send TG notification with Fix/Skip buttons
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        bot = Bot(token=BOT_TOKEN)

        issues_summary = "\n".join(
            f"- [{f['severity']}] {f['title'][:60]}"
            for f in risky_findings[:10]
        )

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Fix (merge)", callback_data=f"review:merge:{branch_name}"),
                InlineKeyboardButton("Skip", callback_data=f"review:skip:{branch_name}"),
            ]
        ])

        await bot.send_message(
            chat_id=CHAT_ID,
            message_thread_id=THREAD_ID,
            text=(
                f"<b>Risky Auto-Fix Ready</b>\n"
                f"Branch: <code>{branch_name}</code>\n"
                f"{len(risky_findings)} issues:\n\n"
                f"{issues_summary[:2000]}"
            )[:4000],
            parse_mode="HTML",
            reply_markup=kb,
        )
        return True

    except asyncio.TimeoutError:
        log.warning("Risky fix timed out")
    except Exception as e:
        log.error("Risky fix failed: %s", e)
    finally:
        # Always return to main branch
        subprocess.run(
            ["git", "-C", str(BASE_DIR), "checkout", "main"],
            capture_output=True, text=True, timeout=10,
        )

    return False


def _notify_tg(text: str):
    """Send TG notification."""
    try:
        import httpx
        if not BOT_TOKEN or not ADMIN_USER_ID:
            return
        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"
        httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_USER_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        log.warning("TG notify failed: %s", e)


async def main():
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    if os.path.exists(SENT_FLAG):
        with open(SENT_FLAG) as f:
            if today in f.read():
                log.info(f"Already sent for {today}")
                return

    day = datetime.now(timezone.utc).weekday()
    spotlight = DAILY_SPOTLIGHT.get(day, DAILY_SPOTLIGHT[0])
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d (%A)")

    claude_bin = _find_claude_bin()
    if not claude_bin:
        log.error("Claude binary not found")
        return

    prompt = (
        f"Today is {today_str}. You are doing a daily autonomous review of the telegram-claude-bot codebase.\n\n"
        f"Step 1: Read the review checklist at `references/review-checklist.md`.\n"
        f"Step 2: Check /tmp/start_all.log (last 200 lines) for recent errors or warnings.\n"
        f"Step 3: Scan the codebase against ALL categories in the checklist.\n"
        f"Step 4: TODAY'S SPOTLIGHT: {spotlight} -- dig deeper into this area.\n\n"
        "Output format -- give 3-7 findings, sorted by severity:\n"
        "For each finding:\n"
        "  - **[SEVERITY]** one-line title\n"
        "  - File:line\n"
        "  - WHY it's a problem\n"
        "  - Exact fix (code snippet)\n\n"
        "End with a 1-line health summary.\n"
        "Be concise. No fluff. Only real issues worth fixing."
    )

    log.info("Starting code review (spotlight: %s)", spotlight.split(":")[0])

    try:
        result = await _run_claude_review(claude_bin, prompt)

        # Save review file
        review_file = BASE_DIR / ".daily_review_latest.md"
        review_file.write_text(f"# Daily Review -- {today_str}\n## Focus: {spotlight}\n\n{result}\n")

        # Also save dated file for review:fixall callback
        dated_file = BASE_DIR / f".daily_review_{today}.md"
        dated_file.write_text(f"# Daily Review -- {today_str}\n## Focus: {spotlight}\n\n{result}\n")

        # Parse findings and classify
        findings = _parse_review_findings(result)
        safe_findings = [f for f in findings if f["classification"] == "SAFE"]
        risky_findings = [f for f in findings if f["classification"] == "RISKY"]

        log.info("Findings: %d total, %d SAFE, %d RISKY", len(findings), len(safe_findings), len(risky_findings))

        # Auto-fix SAFE issues
        safe_fixed = False
        if safe_findings:
            log.info("Auto-fixing %d SAFE issues...", len(safe_findings))
            safe_fixed = await _auto_fix_safe_issues(claude_bin, safe_findings)

        # Handle RISKY issues on branch
        risky_branched = False
        if risky_findings:
            log.info("Creating branch for %d RISKY issues...", len(risky_findings))
            risky_branched = await _handle_risky_issues(claude_bin, risky_findings, today)

        # Build status line for TG message
        fix_status = ""
        if safe_fixed:
            fix_status += f"\n\nSafe fixes: {len(safe_findings)} auto-fixed + committed"
        if risky_branched:
            fix_status += f"\nRisky fixes: {len(risky_findings)} on branch autofix-{today}"

        # Send to TG
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        bot = Bot(token=BOT_TOKEN)

        header = f"<b>Daily Review -- {today_str}</b>\nFocus: {spotlight.split(':')[0]}{fix_status}\n\n"

        buttons = []
        if risky_findings and not risky_branched:
            buttons.append(InlineKeyboardButton("Fix All", callback_data=f"review:fixall:{today}"))

        kb = InlineKeyboardMarkup([buttons]) if buttons else None

        await bot.send_message(
            chat_id=CHAT_ID, message_thread_id=THREAD_ID,
            text=(header + result)[:4000], parse_mode="HTML", reply_markup=kb,
        )

        log.info("Code review sent")

        with open(SENT_FLAG, "a") as f:
            f.write(today + "\n")

    except asyncio.TimeoutError:
        log.warning("Code review timed out (10 min)")
    except Exception as e:
        log.error("Code review failed: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
