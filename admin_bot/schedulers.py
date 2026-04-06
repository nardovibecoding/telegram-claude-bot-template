# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Scheduled jobs: cookie refresh, health checks, daily review, scout, gmail, weekly report."""
import asyncio
import json
import logging
import os
import time as _time

from datetime import datetime, timezone, timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .config import (
    ADMIN_USER_ID, BOTS, BOT_THREADS, GROUP_ID,
    PROJECT_DIR, PERSONAL_GROUP, COOKIE_LOCK_FILE,
    _HEARTBEAT_FILE, _HEARTBEAT_THREAD, _REVIEW_FOCUS, _GMAIL_CHECK_PROMPT,
)
from utils import CLAUDE_BIN

log = logging.getLogger("admin")


# ── Cookie refresh ──────────────────────────────────────────────────────

def _is_cookie_refresh_in_progress() -> bool:
    """Check if another process is currently refreshing cookies."""
    if not os.path.exists(COOKIE_LOCK_FILE):
        return False
    try:
        with open(COOKIE_LOCK_FILE) as f:
            data = json.loads(f.read())
        pid = data.get("pid", 0)
        started = data.get("started", "")
        try:
            os.kill(pid, 0)
        except OSError:
            log.warning("Stale cookie lock file (PID %d dead), removing", pid)
            os.unlink(COOKIE_LOCK_FILE)
            return False
        if started:
            lock_age = (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds()
            if lock_age > 300:
                log.warning("Cookie lock is %ds old (stale), removing", int(lock_age))
                os.unlink(COOKIE_LOCK_FILE)
                return False
        return True
    except Exception:
        return False


async def _refresh_cookies(context: ContextTypes.DEFAULT_TYPE):
    """Validate Twitter cookies — Mac handles actual refresh via launchd."""
    if _is_cookie_refresh_in_progress():
        log.info("Cookie refresh skipped — another process is refreshing (lock file exists)")
        return

    try:
        with open(COOKIE_LOCK_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat()}, f)
    except Exception as e:
        log.warning("Failed to create cookie lock file: %s", e)

    try:
        cookie_file = os.path.join(PROJECT_DIR, "twitter_cookies.json")
        cookies_ok = False
        try:
            with open(cookie_file) as f:
                cookies = json.load(f)
            if isinstance(cookies, dict) and "auth_token" in cookies and "ct0" in cookies:
                cookies_ok = True
                log.info("Cookie check OK: auth_token and ct0 present")
        except Exception as e:
            log.warning("Cookie check failed: %s", e)

        if not cookies_ok:
            flag_file = os.path.join(PROJECT_DIR, ".cookies_need_refresh")
            if not os.path.exists(flag_file):
                with open(flag_file, "w") as f:
                    f.write("stale")
                await context.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text="⚠️ Twitter cookies stale — flagged for Mac auto-refresh (≤10 min).",
                )
                log.warning("Cookies stale — .cookies_need_refresh flag set")

        try:
            from bookmark_db import sync_bookmarks, update_taste_summary
            new_count = await sync_bookmarks("")
            if new_count > 0:
                for cat in ("en", "zh", "ai"):
                    await update_taste_summary(cat, "")
                log.info("Bookmark sync: %d new, taste summaries updated", new_count)
        except Exception as e:
            log.error("Bookmark sync failed: %s", e)
    finally:
        try:
            os.unlink(COOKIE_LOCK_FILE)
        except OSError:
            pass


# ── Health checks ───────────────────────────────────────────────────────

async def _health_check(context: ContextTypes.DEFAULT_TYPE):
    """Check every bot process is alive; write heartbeat so external watchdog knows we're alive."""
    try:
        with open(_HEARTBEAT_FILE, "w") as f:
            f.write(str(int(_time.time())))
    except Exception:
        pass

    for name in BOTS:
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", f"run_bot.py {name}",
            stdout=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            log.warning("Health check: pgrep timed out for %s", name)
            continue
        if stdout.decode().strip():
            continue
        thread_id = BOT_THREADS.get(name)
        token = os.environ.get(f"TELEGRAM_BOT_TOKEN_{name.upper()}")
        if not token or not thread_id:
            continue
        log.warning("Health check: %s is DOWN", name)
        try:
            async with Bot(token=token) as bot:
                await bot.send_message(
                    chat_id=GROUP_ID,
                    message_thread_id=thread_id,
                    text=f"⚠️ <b>{name}</b> is down — auto-restart should recover within 10s",
                    parse_mode="HTML",
                )
        except Exception as e:
            log.error("Health alert failed for %s: %s", name, e)


async def _deep_heartbeat(context: ContextTypes.DEFAULT_TYPE):
    """Deep health check every 6h — checks all services, only alerts on problems."""
    issues = []

    for bot_id in ["daliu", "sbf"]:
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", f"run_bot.py {bot_id}",
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if not stdout.decode().strip():
            issues.append(f"🔴 Bot <b>{bot_id}</b> is DOWN")


    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sf", "--max-time", "5", "http://localhost:18789/",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            issues.append("🟡 OpenClaw gateway not responding")
    except Exception:
        pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "df", "--output=pcent", "/",
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        pct = int(stdout.decode().strip().split("\n")[-1].strip().rstrip("%"))
        if pct >= 80:
            issues.append(f"🟡 Disk usage at <b>{pct}%</b>")
    except Exception:
        pass

    cookie_path = os.path.join(PROJECT_DIR, "twitter_cookies.json")
    if os.path.exists(cookie_path):
        age_hours = (_time.time() - os.path.getmtime(cookie_path)) / 3600
        if age_hours > 36:
            issues.append(f"🟡 Twitter cookies <b>{int(age_hours)}h</b> old")
    else:
        issues.append("🔴 Twitter cookies missing")

    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", "tail -5000 /tmp/start_all.log | grep -c ERROR",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        errors = int(stdout.decode().strip() or "0")
        if errors >= 10:
            issues.append(f"🟡 <b>{errors}</b> ERRORs in recent logs")
    except Exception:
        pass

    try:
        proc = await asyncio.create_subprocess_exec("free", "-m", stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        parts = stdout.decode().strip().split("\n")[1].split()
        pct = int(int(parts[2]) / int(parts[1]) * 100)
        if pct >= 90:
            issues.append(f"🟡 Memory at <b>{pct}%</b>")
    except Exception:
        pass

    if issues:
        now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT")
        text = f"🫀 <b>Heartbeat Alert</b>\n📅 {now}\n\n" + "\n".join(issues)
        try:
            await context.bot.send_message(
                chat_id=PERSONAL_GROUP, text=text,
                message_thread_id=_HEARTBEAT_THREAD, parse_mode="HTML",
            )
        except Exception as e:
            log.error("Heartbeat alert failed: %s", e)
        log.warning("Heartbeat: %d issues", len(issues))
    else:
        log.info("Heartbeat: all healthy")


# ── Daily review ────────────────────────────────────────────────────────

async def _daily_review(context: ContextTypes.DEFAULT_TYPE):
    """Daily autonomous review: run Claude Code to review the codebase and post suggestions."""
    day = datetime.now(timezone.utc).weekday()
    focus = _REVIEW_FOCUS.get(day, _REVIEW_FOCUS[0])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d (%A)")

    prompt = (
        f"Today is {today}. You are doing a daily autonomous review of the telegram-claude-bot codebase.\n\n"
        f"TODAY'S FOCUS: {focus}\n\n"
        "Instructions:\n"
        "1. Check /tmp/start_all.log (last 200 lines) for recent errors or warnings\n"
        "2. Scan the codebase with today's focus area in mind\n"
        "3. Give exactly 3-5 SPECIFIC, ACTIONABLE suggestions. For each:\n"
        "   - File and line number\n"
        "   - What the issue is\n"
        "   - Exact fix (code snippet if applicable)\n"
        "4. Rate each suggestion: [CRITICAL] [HIGH] [MEDIUM] [LOW]\n"
        "5. End with a 1-line health summary: is everything running well?\n\n"
        "Be concise. No fluff. Only real issues worth fixing."
    )

    log.info("Daily review starting (focus: %s)", focus.split(":")[0])

    try:
        review_args = [CLAUDE_BIN, "-p", "--verbose",
                       "--model", "claude-sonnet-4-6",
                       "--allowedTools", "Read,Glob,Grep",
                       "--output-format", "json"]
        review_env = os.environ.copy()
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY"):
            review_env.pop(k, None)
        proc = await asyncio.create_subprocess_exec(
            *review_args,
            cwd=PROJECT_DIR,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=review_env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(input=prompt.encode()), timeout=900)
        output = stdout.decode().strip()

        result = ""
        try:
            data = json.loads(output)
            result = data.get("result", output)
        except json.JSONDecodeError:
            result = output

        if not result:
            result = "(no output from review)"

        review_file = os.path.join(PROJECT_DIR, ".daily_review_latest.md")
        review_dated = os.path.join(PROJECT_DIR, f".daily_review_{today}.md")
        try:
            content = f"# Daily Review — {today}\n## Focus: {focus}\n\n{result}\n"
            with open(review_file, "w") as f:
                f.write(content)
            with open(review_dated, "w") as f:
                f.write(content)
        except Exception as e:
            log.warning("Failed to save review file: %s", e)

        header = f"🔍 <b>Daily Review — {today}</b>\n📋 Focus: {focus.split(':')[0]}\n\n"
        fix_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Fix All", callback_data=f"review:fixall:{today}"),
            InlineKeyboardButton("❌ Skip", callback_data=f"review:skip:{today}"),
        ]])
        try:
            await context.bot.send_message(
                chat_id=PERSONAL_GROUP,
                message_thread_id=147,
                text=(header + result)[:4000],
                parse_mode="HTML",
                reply_markup=fix_kb,
            )
        except Exception as e:
            log.warning("Review post failed: %s, trying without HTML", e)
            await context.bot.send_message(
                chat_id=PERSONAL_GROUP,
                message_thread_id=147,
                text=(header + result)[:4000],
                reply_markup=fix_kb,
            )
        log.info("Daily review posted to personal group thread 147")

    # NOTE: _daily_review is dead code — review runs via cron (send_code_review.py)
    # Keeping the function but it's never scheduled via job_queue

    except asyncio.TimeoutError:
        log.warning("Daily review timed out (900s)")
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text="⚠️ Daily review timed out (900s)",
        )
    except Exception as e:
        log.error("Daily review failed: %s", e)


# ── Andrea Scout ────────────────────────────────────────────────────────

async def _run_andrea_scout(bot, notify_chat_id: int | None = None) -> None:
    """Run andrea_scout.py as subprocess and optionally notify on error."""
    venv_python = os.path.join(PROJECT_DIR, "venv", "bin", "python")
    scout_script = os.path.join(PROJECT_DIR, "andrea_scout.py")
    try:
        proc = await asyncio.create_subprocess_exec(
            venv_python, scout_script,
            cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        output = stdout.decode().strip()
        log.info("Andrea scout finished (rc=%d)", proc.returncode)
        if proc.returncode != 0 and notify_chat_id:
            await bot.send_message(
                chat_id=notify_chat_id,
                text=f"⚠️ Andrea Scout failed (rc={proc.returncode}):\n<pre>{output[-800:]}</pre>",
                parse_mode="HTML",
            )
        elif notify_chat_id:
            await bot.send_message(
                chat_id=notify_chat_id,
                text="✅ Andrea Scout ran successfully.",
            )
    except asyncio.TimeoutError:
        log.warning("Andrea Scout timed out (300s)")
        if notify_chat_id:
            await bot.send_message(chat_id=notify_chat_id, text="⚠️ Andrea Scout timed out.")
    except Exception as e:
        log.error("Andrea Scout error: %s", e)
        if notify_chat_id:
            await bot.send_message(chat_id=notify_chat_id, text=f"⚠️ Andrea Scout error: {e}")


async def _andrea_scout_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled daily andrea scout job."""
    await _run_andrea_scout(context.bot)


async def _andrea_sync_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every 3 days: ping Bernard in DM with a structured sync prompt."""
    hkt = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    msg = (
        f"<b>🔄 Andrea Scout — 3-Day Sync</b>  {hkt}\n\n"
        "Time to review the last 3 days of app digests.\n\n"
        "<b>Quick questions:</b>\n"
        "1. Any idea that stood out? (worth prototyping?)\n"
        "2. Any vertical getting repeated signals?\n"
        "3. Scout quality: too noisy / too narrow / on point?\n"
        "4. Anything to add to the verticals list?\n\n"
        "Reply here and I'll update the scout config or log the decision."
    )
    await context.bot.send_message(
        chat_id=ADMIN_USER_ID,
        text=msg,
        parse_mode="HTML",
    )


# ── Gmail check ─────────────────────────────────────────────────────────

async def _daily_gmail_check(context: ContextTypes.DEFAULT_TYPE):
    """Daily Gmail check — run Claude Code to scan inbox and report."""
    log.info("Running daily Gmail check...")
    try:
        env = os.environ.copy()
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY"):
            env.pop(k, None)
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "-p", "--verbose",
            "--model", "claude-sonnet-4-6",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            cwd=PROJECT_DIR,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=1024 * 1024,
            env=env,
        )

        proc.stdin.write(_GMAIL_CHECK_PROMPT.encode())
        proc.stdin.write_eof()

        result = ""

        async def _read_gmail():
            nonlocal result
            async for raw_line in proc.stdout:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "result":
                    result = event.get("result", "")

        try:
            await asyncio.wait_for(_read_gmail(), timeout=300)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            log.warning("_daily_gmail_check: proc timed out/cancelled")
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text="📧 Gmail check timed out (300s)",
            )
            return
        await proc.wait()

        if result:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=f"📧 Daily Gmail Check\n\n{result[:3500]}",
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text="📧 Daily Gmail Check\n\nAll clear — no emails needing a reply from your key contacts in the last 24 hours.",
            )
        log.info("Daily Gmail check done, result_len=%d", len(result))
    except Exception as e:
        log.error("Daily Gmail check failed: %s", e)
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"📧 Gmail check failed: {e}",
        )


# ── XList populate ──────────────────────────────────────────────────────

async def _run_xlist_populate(context: ContextTypes.DEFAULT_TYPE):
    """Hourly job: add more accounts to the xcurate list until fully populated."""
    python = os.path.join(PROJECT_DIR, "venv", "bin", "python")
    script = os.path.join(PROJECT_DIR, "setup_xlist.py")
    config = os.path.join(PROJECT_DIR, "xlist_config.json")

    if not os.path.exists(config):
        log.info("xlist_config.json not found, skipping populate job")
        return

    with open(config) as f:
        cfg = json.load(f)
    if not cfg.get("list_id"):
        return

    log.info("Running xlist populate job...")
    try:
        proc = await asyncio.create_subprocess_exec(
            python, "-u", script, "--add",
            cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=900)
        output = stdout.decode()
        log.info("xlist populate done (exit %s): %s", proc.returncode, output[-300:])

        if "Accounts to add: 0" in output or "Done. Added: 0" in output:
            log.info("xlist fully populated — removing hourly job")
            done_flag = os.path.join(PROJECT_DIR, ".xlist_populated")
            with open(done_flag, "w") as f:
                f.write("1")
            context.job.schedule_removal()
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text="xcurate list fully populated.",
            )
    except asyncio.TimeoutError:
        log.warning("xlist populate timed out (rate limited) — will retry next hour")
    except Exception as e:
        log.error("xlist populate failed: %s", e)


# ── Weekly Usage Report ─────────────────────────────────────────────────

async def _weekly_usage_report(context: ContextTypes.DEFAULT_TYPE):
    """Weekly report: top commands + API costs, posted to admin group thread 152."""
    hkt = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT")

    # ── Command usage ──
    from .usage_tracker import USAGE_FILE
    cmd_data = {}
    if USAGE_FILE.exists():
        try:
            cmd_data = json.loads(USAGE_FILE.read_text())
        except Exception:
            pass

    top_cmds = sorted(cmd_data.items(), key=lambda x: x[1], reverse=True)[:10]
    total_cmds = sum(cmd_data.values())

    cmd_lines = []
    for i, (cmd, count) in enumerate(top_cmds, 1):
        cmd_lines.append(f"  {i}. /{cmd} — <b>{count}</b>")
    cmd_section = "\n".join(cmd_lines) if cmd_lines else "  (no data)"

    # ── API costs ──
    try:
        from cost_tracker import get_weekly_costs
        weekly = get_weekly_costs()
    except Exception as e:
        log.error("Weekly report: cost_tracker import failed: %s", e)
        weekly = {"total_cost_usd": 0, "by_model": {}, "total_calls": 0}

    cost_lines = []
    for model, stats in sorted(weekly.get("by_model", {}).items(),
                                key=lambda x: x[1]["cost_usd"], reverse=True):
        cost_lines.append(
            f"  {model}: <b>${stats['cost_usd']:.2f}</b> ({stats['calls']} calls)"
        )
    cost_section = "\n".join(cost_lines) if cost_lines else "  (no data)"

    text = (
        f"📊 <b>Weekly Usage Report</b>\n"
        f"📅 {hkt}\n\n"
        f"<b>Top Commands</b> ({total_cmds} total)\n"
        f"{cmd_section}\n\n"
        f"<b>API Costs</b> (${weekly.get('total_cost_usd', 0):.2f} total, "
        f"{weekly.get('total_calls', 0)} API calls)\n"
        f"{cost_section}"
    )

    try:
        await context.bot.send_message(
            chat_id=PERSONAL_GROUP,
            message_thread_id=152,
            text=text,
            parse_mode="HTML",
        )
        log.info("Weekly usage report posted")
    except Exception as e:
        log.error("Weekly usage report failed: %s", e)


# ── Fetch Watchdog ─────────────────────────────────────────────────────

async def _fetch_watchdog_job(context: ContextTypes.DEFAULT_TYPE):
    """Pre-digest source health check — runs 30 min before digest time."""
    try:
        from fetch_watchdog import watchdog_job
        await watchdog_job(context)
    except Exception as e:
        log.error("Fetch watchdog failed: %s", e)
