# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""All command handlers for admin bot."""
import asyncio
import json
import logging
import os
import sqlite3
import sys
from html import escape as html_escape
from pathlib import Path

# ops-guard-mcp shared library (graceful fallback if not cloned on VPS yet)
try:
    sys.path.insert(0, os.path.expanduser("~/ops-guard-mcp"))
    from lib import (  # noqa: E402
        content_capture, content_queue, session_checkpoint, git_commit_push_async,
    )
    _OPS_GUARD_AVAILABLE = True
except ImportError:
    _OPS_GUARD_AVAILABLE = False

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .config import (
    ADMIN_USER_ID, BOTS, LOG_FILES, PROJECT_DIR, _HEARTBEAT_FILE,
)
from .domains import (
    _load_sessions, _save_sessions, _load_domain_groups, _save_domain_groups,
    _detect_domain, _session_key, clear_all_locks,
)
from .helpers import admin_only, _send_msg, _clean_result

log = logging.getLogger("admin")


@admin_only
async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot version: git hash, HKT deploy time, commit message."""
    from datetime import datetime, timezone, timedelta
    HKT = timezone(timedelta(hours=8))
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "-1", "--format=%h|%ct|%s",
            cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        info = stdout.decode().strip()
        hash_, ts_, msg_ = info.split("|", 2)
        commit_time = datetime.fromtimestamp(int(ts_), tz=HKT)
        date_hkt = commit_time.strftime("%Y-%m-%d %H:%M:%S HKT")
    except Exception:
        hash_, date_hkt, msg_ = "unknown", "unknown", "unknown"

    from .config import VERSION
    try:
        sys.path.insert(0, PROJECT_DIR)
        from llm_client import PROVIDERS, _FALLBACK_CHAIN
        primary = _FALLBACK_CHAIN[0] if _FALLBACK_CHAIN else "?"
        chat_label = PROVIDERS.get(primary, {}).get("name", primary)
    except Exception:
        chat_label = "unknown"
    lines = [
        f"<b>Admin Bot v{VERSION}</b>",
        f"<code>{hash_}</code>",
        f"{date_hkt}",
        f"{msg_}",
        "",
        "<b>STT:</b> Groq Whisper (v3-turbo)",
        "<b>AI:</b> Claude via SDK",
        f"<b>Chat:</b> {chat_label}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from .config import VERSION_STR
    from datetime import datetime, timezone, timedelta
    HKT = timezone(timedelta(hours=8))

    lines = [f"<b>Admin Bot {VERSION_STR}</b>"]

    # Git version info (absorbed from /version)
    try:
        _git_proc = await asyncio.create_subprocess_exec(
            "git", "log", "-1", "--format=%h|%ct|%s",
            cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _git_out, _ = await asyncio.wait_for(_git_proc.communicate(), timeout=5)
        info = _git_out.decode().strip()
        hash_, ts_, msg_ = info.split("|", 2)
        commit_time = datetime.fromtimestamp(int(ts_), tz=HKT)
        date_hkt = commit_time.strftime("%m-%d %H:%M HKT")
        lines.append(f"<code>{hash_}</code> {date_hkt} — {msg_}")
    except Exception:
        pass

    lines.append("")
    lines.append("<b>Bot Processes</b>")
    for name in BOTS:
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", f"run_bot.py {name}",
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        pids = stdout.decode().strip()
        status = f"running (PID {pids})" if pids else "stopped"
        lines.append(f"• {name}: {status}")

    sessions = _load_sessions()
    if sessions:
        lines.append("")
        lines.append("<b>Sessions</b>")
        for key, sid in sessions.items():
            lines.append(f"• {key}: <code>{sid[:12]}...</code>")

    cost_log = os.path.join(PROJECT_DIR, "claude_cost_log.jsonl")
    if os.path.exists(cost_log):
        from datetime import date
        today = date.today().isoformat()
        total = 0.0
        with open(cost_log) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("ts", "").startswith(today):
                        total += entry.get("cost_usd", 0)
                except Exception:
                    pass
        if total > 0:
            lines.append(f"\n<b>Claude Cost Today</b>: ${total:.4f}")

    # Disk usage (1-liner)
    proc = await asyncio.create_subprocess_exec(
        "df", "-h", "/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    df_lines = stdout.decode().strip().split("\n")
    if len(df_lines) >= 2:
        parts = df_lines[1].split()
        if len(parts) >= 5:
            lines.append(f"\n<b>Disk</b>: {parts[2]} / {parts[1]} ({parts[4]})")

    # Cron job status (absorbed from /cron)
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    HKT = timezone(timedelta(hours=8))
    # (flag_name, scheduled_utc_hour, scheduled_utc_minute)
    cron_jobs = {
        "x_twitter":  (".digest_sent_x_twitter",    3,  0),
        "x_xcn":      (".digest_sent_x_xcn",         3,  0),
        "x_xai":      (".digest_sent_x_xai",         3,  0),
        "x_xniche":   (".digest_sent_x_xniche",      3,  0),
        "news_bot1": (".digest_sent_news_bot1",     3, 30),
        "news_bot2":   (".digest_sent_news_bot2",       5,  0),
        "reddit":     (".digest_sent_reddit_reddit",  5, 30),
    }
    sent = []
    pending = []
    not_yet = []
    for job_name, (flag_name, sched_h, sched_m) in cron_jobs.items():
        flag_path = os.path.join(PROJECT_DIR, flag_name)
        done = False
        if os.path.exists(flag_path):
            try:
                with open(flag_path) as f:
                    done = f.read().strip() == today_str
            except Exception:
                pass
        if done:
            sent.append(job_name)
        else:
            # Check if it's still before the scheduled run time
            due_utc = now_utc.replace(hour=sched_h, minute=sched_m, second=0, microsecond=0)
            if now_utc < due_utc:
                due_hkt = due_utc.astimezone(HKT).strftime("%H:%M")
                not_yet.append(f"{job_name}(due {due_hkt})")
            else:
                pending.append(job_name)
    lines.append(f"\n<b>Digests Today</b>")
    if sent:
        lines.append(f"  ✅ {', '.join(sent)}")
    if not_yet:
        lines.append(f"  ⏳ {', '.join(not_yet)}")
    if pending:
        lines.append(f"  ❌ {', '.join(pending)}")

    # Model inventory — show which APIs are configured
    MODEL_KEYS = [
        ("MiniMax M2.5", "MINIMAX_API_KEY", "Chat + critic (paid, unlimited)"),
        ("GPT-4o", "CRITIC_API_KEY", "Critic (free, 200/day)"),
        ("Gemini 2.5 Pro", "GEMINI_API_KEY", "Code review (free, 100/day)"),
        ("DeepSeek V3.2", "DEEPSEEK_API_KEY", "Fallback ($0.28/MTok)"),
        ("Groq Llama 70B", "GROQ_API_KEY", "Fast research (free, 1K/day)"),
        ("Cerebras Qwen3", "CEREBRAS_API_KEY", "Heavy reasoning (free, 1M tok/day)"),
        ("Claude (Max x5)", "ANTHROPIC_API_KEY", "Claude Code (subscription)"),
    ]
    lines.append("\n<b>Models</b>")
    for name, env_key, desc in MODEL_KEYS:
        has_key = bool(os.environ.get(env_key))
        icon = "🟢" if has_key else "⚪"
        lines.append(f"  {icon} {name} — {desc}")

    # Last 3 errors from log (absorbed from /logs)
    log_path = "/tmp/start_all.log"
    if os.path.exists(log_path):
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c",
            f"grep -i 'error\\|traceback\\|exception' {log_path} | tail -3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        errors = stdout.decode().strip()
        if errors:
            lines.append("\n<b>Recent Errors</b>")
            err_text = errors[:600]
            err_text = err_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"<pre>{err_text}</pre>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@admin_only
async def cmd_restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restart bot1 or bot2 bot by killing their process (start_all.sh auto-restarts)."""
    bot_name = update.message.text.split("_", 1)[1] if "_" in update.message.text else ""
    if bot_name not in ("bot1", "bot2"):
        return
    import signal
    proc = await asyncio.create_subprocess_exec(
        "pgrep", "-f", f"run_bot.py {bot_name}",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    pids = stdout.decode().strip()
    if pids:
        for pid in pids.split("\n"):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
        await update.message.reply_text(f"🔄 {bot_name} killed (PID {pids}). 5 秒內重啟。")
    else:
        await update.message.reply_text(f"⚠️ {bot_name} 冇搵到進程。")


@admin_only
async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = []
        for bot_id in list(BOTS) + ["admin"]:
            buttons.append(InlineKeyboardButton(f"\U0001f504 {bot_id}", callback_data=f"restart_cmd:{bot_id}"))
        keyboard = InlineKeyboardMarkup([buttons[:2], buttons[2:]])
        await update.message.reply_text("Which bot to restart?", reply_markup=keyboard)
        return
    name = context.args[0].lower()

    if name == "admin":
        killed = 0
        for k, v in list(context.bot_data.items()):
            if k.startswith("claude_proc_"):
                if hasattr(v, 'interrupt'):
                    # SDK client — interrupt running task
                    try:
                        await v.interrupt()
                        killed += 1
                    except Exception:
                        pass
                elif hasattr(v, 'returncode') and v.returncode is None:
                    # Legacy subprocess
                    v.kill()
                    killed += 1
        # Also kill any orphan subprocess-based claude processes
        kill = await asyncio.create_subprocess_exec("pkill", "-f", "claude.*-p.*--verbose")
        await kill.wait()
        clear_all_locks()
        from .sdk_client import sdk_disconnect_all
        await sdk_disconnect_all()
        await update.message.reply_text(f"🛑 Killed {killed} stuck task(s). Admin bot ready.")
        return

    if name not in BOTS:
        await update.message.reply_text(f"Unknown bot: {name}")
        return

    import signal
    proc = await asyncio.create_subprocess_exec(
        "pgrep", "-f", f"run_bot.py {name}",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    pids = stdout.decode().strip()
    if pids:
        for pid in pids.split("\n"):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
        await update.message.reply_text(f"\U0001f504 {name} killed (PID {pids}). 5 \u79d2\u5167\u91cd\u555f\u3002")
    else:
        await update.message.reply_text(f"\u26a0\ufe0f {name} not running.")


@admin_only
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /logs <bot>\nBots: " + ", ".join(BOTS))
        return
    name = context.args[0].lower()
    if name not in BOTS:
        await update.message.reply_text(f"Unknown bot: {name}")
        return

    log_path = LOG_FILES[name]
    if not os.path.exists(log_path):
        await update.message.reply_text(f"No log file: {log_path}")
        return

    proc = await asyncio.create_subprocess_exec(
        "tail", "-n", "30", log_path,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    text = stdout.decode().strip() or "(empty)"
    if len(text) > 4000:
        text = text[-4000:]
    await update.message.reply_text(f"<pre>{html_escape(text)}</pre>", parse_mode="HTML")


@admin_only
async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger or rerun a digest: /digest <bot> or /digest rerun <job>"""
    RERUN_JOBS = {
        "bot1": {
            "flag": ".digest_sent_news_bot1",
            "cmd": ["python", "send_digest.py", "bot1"],
        },
        "bot2": {
            "flag": ".digest_sent_news_bot2",
            "cmd": ["python", "send_digest.py", "bot2"],
        },
        "twitter": {
            "flag": ".digest_sent_x_twitter",
            "cmd": ["python", "send_xdigest.py", "twitter"],
        },
        "xcn": {
            "flag": ".digest_sent_x_xcn",
            "cmd": ["python", "send_xdigest.py", "xcn"],
        },
        "xai": {
            "flag": ".digest_sent_x_xai",
            "cmd": ["python", "send_xdigest.py", "xai"],
        },
        "xniche": {
            "flag": ".digest_sent_x_xniche",
            "cmd": ["python", "send_xdigest.py", "xniche"],
        },
        "reddit": {
            "flag": ".digest_sent_reddit_reddit",
            "cmd": ["python", "send_reddit_digest.py"],
        },
        "china_trends": {
            "flag": None,
            "cmd": ["python", "china_trends.py"],
        },
        "code_review": {
            "flag": None,
            "cmd": ["python", "send_code_review.py"],
        },
        "evolution": {
            "flag": ".evolution_feed_sent",
            "cmd": ["python", "evolution_feed.py"],
        },
        "podcast": {
            "flag": ".podcast_digest_sent",
            "cmd": ["python", "podcast_digest.py"],
        },
        "youtube": {
            "flag": ".youtube_digest_sent",
            "cmd": ["python", "youtube_digest.py"],
        },
    }

    DIGEST_BOTS = ["bot1", "bot2", "twitter", "xcn", "xai", "xniche", "reddit"]

    if not context.args:
        await update.message.reply_text(
            "Usage: /digest &lt;bot&gt; or /digest all\n"
            f"Bots: {', '.join(DIGEST_BOTS)}\n"
            "Or: /digest rerun &lt;job&gt;",
            parse_mode="HTML",
        )
        return

    # Handle /digest all — run all digests sequentially
    if context.args[0].lower() == "all":
        msg = await update.message.reply_text("Running all digests...")
        python = os.path.join(PROJECT_DIR, "venv", "bin", "python")
        if not os.path.exists(python):
            python = "python3"
        results = []
        for bot_name in DIGEST_BOTS:
            info = RERUN_JOBS.get(bot_name)
            if not info:
                results.append(f"  {bot_name}: unknown job")
                continue
            if info["flag"]:
                flag_path = os.path.join(PROJECT_DIR, info["flag"])
                try:
                    os.unlink(flag_path)
                except OSError:
                    pass
            cmd = [python] + info["cmd"][1:]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=PROJECT_DIR,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
                rc = proc.returncode
                icon = "\u2705" if rc == 0 else "\u274c"
                results.append(f"  {icon} {bot_name} (rc={rc})")
            except asyncio.TimeoutError:
                results.append(f"  \u26a0\ufe0f {bot_name} (timeout)")
            except Exception as e:
                results.append(f"  \u274c {bot_name} ({e})")
        await msg.edit_text("<b>Digest results:</b>\n" + "\n".join(results), parse_mode="HTML")
        return

    # Handle /digest rerun <job>
    if context.args[0].lower() == "rerun":
        if len(context.args) < 2:
            job_list = ", ".join(RERUN_JOBS.keys())
            await update.message.reply_text(f"Usage: /digest rerun &lt;job&gt;\nJobs: {job_list}", parse_mode="HTML")
            return
        job = context.args[1].lower()
        if job not in RERUN_JOBS:
            job_list = ", ".join(RERUN_JOBS.keys())
            await update.message.reply_text(f"Unknown job: {job}\nJobs: {job_list}")
            return

        info = RERUN_JOBS[job]
        # Delete the flag file if it exists
        if info["flag"]:
            flag_path = os.path.join(PROJECT_DIR, info["flag"])
            try:
                os.unlink(flag_path)
            except OSError:
                pass
        # Determine python path
        python = os.path.join(PROJECT_DIR, "venv", "bin", "python")
        if not os.path.exists(python):
            python = "python3"
        cmd = [python] + info["cmd"][1:]
        await asyncio.create_subprocess_exec(
            *cmd, cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await update.message.reply_text(f"🔄 Re-running {job}...")
        return

    # Handle /digest <bot> — simple trigger
    name = context.args[0].lower()
    if name not in BOTS and name not in RERUN_JOBS:
        await update.message.reply_text(f"Unknown bot: {name}")
        return

    flag = os.path.join(PROJECT_DIR, f".digest_trigger_{name}")
    with open(flag, "w") as f:
        f.write("1")
    await update.message.reply_text(
        f"Digest trigger written for {name}.\n"
        f"(Bot needs to check for flag file, or implement pickup logic.)"
    )


@admin_only
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset Claude session for the current topic."""
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    domain = _detect_domain(chat_id, thread_id) or "news"
    key = _session_key(domain, thread_id)
    sessions = _load_sessions()
    if key in sessions:
        del sessions[key]
        _save_sessions(sessions)
    await update.message.reply_text(f"Session reset for [{key}]. Next message starts fresh.")


@admin_only
async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transfer a session: /session <session_id> — links a Mac session to this domain/topic."""
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    domain = _detect_domain(chat_id, thread_id) or "news"
    key = _session_key(domain, thread_id)

    if not context.args:
        sessions = _load_sessions()
        sid = sessions.get(key)
        await update.message.reply_text(f"[{key}] session: {sid or 'none'}")
        return

    args = context.args
    if args[0].lower() == "set" and len(args) >= 3:
        domain = args[1].lower()
        session_id = args[2]
        key = domain
    elif args[0].lower() == "set" and len(args) == 2:
        session_id = args[1]
    else:
        session_id = args[0]

    import re as _re
    if not _re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', session_id, _re.I):
        await update.message.reply_text(f"Invalid session ID: {session_id}\nExpected UUID format.")
        return

    sessions = _load_sessions()
    sessions[key] = session_id
    _save_sessions(sessions)
    await update.message.reply_text(f"[{key}] session set to {session_id[:16]}...\nNext message will resume that conversation.")


@admin_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the currently running Claude Code task for this topic."""
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    domain = _detect_domain(chat_id, thread_id) or "news"
    key = _session_key(domain, thread_id)
    proc_key = f"claude_proc_{key}"
    obj = context.bot_data.get(proc_key)
    if obj is not None:
        if hasattr(obj, 'interrupt'):
            try:
                await obj.interrupt()
            except Exception:
                pass
            context.bot_data.pop(proc_key, None)
            await update.message.reply_text(f"Stopped [{key}].")
        elif hasattr(obj, 'returncode') and obj.returncode is None:
            obj.kill()
            context.bot_data.pop(proc_key, None)
            await update.message.reply_text(f"Stopped [{key}].")
        else:
            await update.message.reply_text("Nothing running.")
    else:
        await update.message.reply_text("Nothing running.")


@admin_only
async def cmd_xhslogin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate xiaohongshu QR code for re-login: /xhslogin"""
    import base64
    await update.message.reply_text("🟧 Generating xiaohongshu QR code...")
    try:
        import aiohttp, json as _json
        url = "http://localhost:18060/mcp"
        accept_hdr = {"Accept": "application/json, text/event-stream"}

        async def _mcp_post(session, payload, headers, timeout=30):
            """Post to MCP, parse SSE or JSON response."""
            r = await session.post(url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout))
            ct = r.headers.get("Content-Type", "")
            sid = r.headers.get("Mcp-Session-Id", "")
            if "event-stream" in ct:
                result = None
                text = await r.text()
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("data:"):
                        try:
                            result = _json.loads(line[5:].strip())
                        except _json.JSONDecodeError:
                            pass
                return result, sid
            elif "json" in ct:
                return await r.json(), sid
            else:
                # Empty or unknown content type (e.g. notifications return 202 with no body)
                return None, sid

        async with aiohttp.ClientSession() as session:
            # Initialize
            _, sid = await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "initialize", "id": 1,
                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                  "clientInfo": {"name": "tg", "version": "1.0"}}}, accept_hdr)
            headers = {**accept_hdr}
            if sid:
                headers["Mcp-Session-Id"] = sid
            # Notify initialized
            await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "notifications/initialized",
                 "params": {}}, headers)
            # Call tool
            result, _ = await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "tools/call", "id": 2,
                 "params": {"name": "get_login_qrcode", "arguments": {}}},
                headers, timeout=30)
            content = result["result"]["content"]
            qr_data = None
            timeout_str = ""
            for item in content:
                if item.get("type") == "text":
                    # QR data is inside JSON text block
                    try:
                        info = _json.loads(item["text"])
                        img_b64 = info.get("img", "")
                        timeout_str = info.get("timeout", "")
                        if img_b64:
                            if img_b64.startswith("data:"):
                                img_b64 = img_b64.split(",", 1)[1]
                            qr_data = base64.b64decode(img_b64)
                    except (_json.JSONDecodeError, Exception):
                        pass
                elif item.get("type") == "image" and item.get("data"):
                    qr_data = base64.b64decode(item["data"])
            if qr_data and len(qr_data) > 100:
                from io import BytesIO
                qr_file = BytesIO(qr_data)
                qr_file.name = "xhs_qr.bmp"  # BMP extension forces document mode
                await update.message.reply_document(document=qr_file,
                    caption=f"🟧 Scan with 小红书 app ({timeout_str})\n\nThen send /xhscheck")
            else:
                await update.message.reply_text("❌ QR code empty — MCP may need restart. Try again.")
    except Exception as e:
        await update.message.reply_text(f"❌ XHS login error: {e}")


@admin_only
async def cmd_xhscheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check xiaohongshu login status after QR scan: /xhscheck"""
    try:
        import aiohttp, json as _json
        url = "http://localhost:18060/mcp"
        accept_hdr = {"Accept": "application/json, text/event-stream"}

        async def _mcp_post(session, payload, headers, timeout=30):
            """Post to MCP, parse SSE or JSON response."""
            r = await session.post(url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout))
            ct = r.headers.get("Content-Type", "")
            sid = r.headers.get("Mcp-Session-Id", "")
            if "event-stream" in ct:
                result = None
                text = await r.text()
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("data:"):
                        try:
                            result = _json.loads(line[5:].strip())
                        except _json.JSONDecodeError:
                            pass
                return result, sid
            elif "json" in ct:
                return await r.json(), sid
            else:
                return None, sid

        async with aiohttp.ClientSession() as session:
            _, sid = await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "initialize", "id": 1,
                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                  "clientInfo": {"name": "tg", "version": "1.0"}}}, accept_hdr)
            headers = {**accept_hdr}
            if sid:
                headers["Mcp-Session-Id"] = sid
            await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "notifications/initialized",
                 "params": {}}, headers)
            result, _ = await _mcp_post(session,
                {"jsonrpc": "2.0", "method": "tools/call", "id": 2,
                 "params": {"name": "check_login_status", "arguments": {}}},
                headers, timeout=15)
            text = result["result"]["content"][0]["text"]
            await update.message.reply_text(f"🟧 {text}")
    except Exception as e:
        await update.message.reply_text(f"❌ XHS check error: {e}")


@admin_only
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve Scout research -> unlock Builder/Growth/Critic: /approve"""
    phase_file = os.path.join(PROJECT_DIR, ".team_a_phase")
    with open(phase_file, "w") as f:
        f.write("approved")
    await update.message.reply_text("🟧 Scout approved — Builder, Growth, and Critic topics are now unlocked.")


@admin_only
async def cmd_reset_phase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lock Builder/Growth/Critic again (back to Scout phase): /resetphase [team]"""
    phase_file = os.path.join(PROJECT_DIR, ".team_a_phase")
    try:
        os.unlink(phase_file)
    except OSError:
        pass

    # Clear handoff context for the team
    from .handoff import clear_handoffs
    args = context.args
    team = args[0] if args else "team_a"
    cleared = clear_handoffs(team)

    await update.message.reply_text(
        f"🔒 Phase reset — Builder/Growth/Critic locked. Scout first.\n"
        f"Cleared {cleared} handoff(s) for team {team}."
    )


@admin_only
async def cmd_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start batch mode: /q then type messages, send ... to process all."""
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id if update.message else None
    batch_key = f"batch_{chat_id}:{thread_id or 0}"
    if context.args:
        context.bot_data[batch_key] = [" ".join(context.args)]
        await update.message.reply_text("📝 Batch started (1). Type more, send ... when done.")
    else:
        context.bot_data[batch_key] = []
        await update.message.reply_text("📝 Batch mode. Type your messages, send ... when done.")


@admin_only
async def cmd_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register current group as a domain: /domain team_a"""
    if not context.args:
        groups = _load_domain_groups()
        lines = ["Registered domains:"]
        for gid, dom in groups.items():
            lines.append(f"  {dom} → {gid}")
        await update.message.reply_text("\n".join(lines) if len(lines) > 1 else "No domains registered.")
        return
    domain = context.args[0].lower()
    if domain not in ("team_a", "news", "email", "airbnb"):
        await update.message.reply_text("Valid domains: team_a, news, email, airbnb")
        return
    chat_id = update.effective_chat.id
    groups = _load_domain_groups()
    groups[str(chat_id)] = domain
    _save_domain_groups(groups)
    await update.message.reply_text(f"This group registered as [{domain}].")


@admin_only
async def cmd_pull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Immediately git pull on VPS: /pull"""
    msg = await update.message.reply_text("⬇️ Pulling...")
    proc = await asyncio.create_subprocess_exec(
        "git", "pull", "--ff-only", "origin", "main",
        cwd=PROJECT_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    output = stdout.decode().strip()
    if proc.returncode == 0:
        await msg.edit_text(f"✅ {output[:500]}")
    else:
        await msg.edit_text(f"❌ Pull failed:\n{output[:500]}")


@admin_only
async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show model selector panel: /model"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    override_key = f"model_override_{chat_id}:{thread_id or 0}"
    current = context.bot_data.get(override_key, "auto")

    labels = {"auto": "🤖 Auto", "minimax": "💬 MiniMax", "haiku": "⚡ Haiku", "sonnet": "🟧 Sonnet", "opus": "🧠 Opus"}

    def _btn(model):
        label = labels[model]
        return InlineKeyboardButton(f"✅ {label}" if model == current else label,
                                    callback_data=f"model:{chat_id}:{thread_id or 0}:{model}")

    kb = InlineKeyboardMarkup([[_btn("auto"), _btn("haiku"), _btn("sonnet"), _btn("opus")]])
    await update.message.reply_text(f"🎛 Model locked: <b>{labels[current]}</b>", parse_mode="HTML", reply_markup=kb)


@admin_only
async def cmd_scout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually trigger the Team A Scout: /scout"""
    from .schedulers import _run_team_a_scout
    await update.message.reply_text("Running Team A Scout now...")
    await _run_team_a_scout(context.bot, notify_chat_id=update.effective_chat.id)


# ── /health — Instant system health check ────────────────────────────────

@admin_only
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Instant system health check: processes, MCP services, disk, memory, heartbeat."""
    import time as _time
    lines = ["<b>🟧 System Health</b>\n"]

    # Bot processes
    lines.append("<b>Bot Processes</b>")
    all_bots = list(BOTS) + ["admin"]
    for name in all_bots:
        pattern = f"run_bot.py {name}" if name != "admin" else "admin_bot"
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", pattern,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        pids = stdout.decode().strip()
        if pids:
            pid_list = pids.replace("\n", ",")
            lines.append(f"  ✅ {name} (PID {pid_list})")
        else:
            lines.append(f"  ❌ {name} — stopped")

    # MCP services
    lines.append("\n<b>MCP Services</b>")
    mcp_services = {"XHS (18060)": 18060, "Douyin (18070)": 18070}
    for svc_name, port in mcp_services.items():
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", f"ss -tlnp 2>/dev/null | grep -q :{port} || lsof -i :{port} -sTCP:LISTEN >/dev/null 2>&1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0:
            lines.append(f"  ✅ {svc_name}")
        else:
            lines.append(f"  ❌ {svc_name} — not listening")

    # Disk usage
    lines.append("\n<b>Disk</b>")
    proc = await asyncio.create_subprocess_exec(
        "df", "-h", "/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    df_lines = stdout.decode().strip().split("\n")
    if len(df_lines) >= 2:
        parts = df_lines[1].split()
        if len(parts) >= 5:
            lines.append(f"  Used: {parts[2]} / {parts[1]} ({parts[4]})")

    # Memory usage
    lines.append("\n<b>Memory</b>")
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", "free -m 2>/dev/null || vm_stat 2>/dev/null",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    mem_out = stdout.decode().strip()
    if "Mem:" in mem_out:
        # Linux free -m output
        for ml in mem_out.split("\n"):
            if ml.startswith("Mem:"):
                mp = ml.split()
                if len(mp) >= 3:
                    lines.append(f"  Used: {mp[2]}M / {mp[1]}M")
                break
    elif mem_out:
        lines.append(f"  <code>{mem_out[:200]}</code>")

    # Heartbeat age
    lines.append("\n<b>Heartbeat</b>")
    if os.path.exists(_HEARTBEAT_FILE):
        try:
            with open(_HEARTBEAT_FILE) as f:
                ts = int(f.read().strip())
            age = int(_time.time()) - ts
            if age < 120:
                lines.append(f"  ✅ {age}s ago")
            elif age < 600:
                lines.append(f"  ⚠️ {age}s ago")
            else:
                lines.append(f"  ❌ {age}s ago (stale!)")
        except Exception:
            lines.append("  ❌ unreadable")
    else:
        lines.append("  ❌ no heartbeat file")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    # Source health probe (absorbed from /watchdog) — run after sending initial health
    try:
        from fetch_watchdog import run_all_probes, auto_fix, record_run, format_report
        results = await run_all_probes()
        actions = await auto_fix(results)
        analysis = record_run(results)
        report = format_report(results, analysis, actions)
        await update.message.reply_text(report, parse_mode="HTML")
    except Exception as e:
        log.warning("Source health probe skipped: %s", e)


# ── /cron — Show all cron job statuses ───────────────────────────────────

@admin_only
async def cmd_cron(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show which digest/cron jobs ran today and their status."""
    from datetime import datetime, timezone, timedelta
    HKT = timezone(timedelta(hours=8))
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # All known digest jobs with flag file patterns and log files
    jobs = {
        "news_bot1":   {"flag": ".digest_sent_news_bot1",    "log": "/tmp/start_all.log"},
        "news_bot2":     {"flag": ".digest_sent_news_bot2",      "log": "/tmp/start_all.log"},
        "x_twitter":    {"flag": ".digest_sent_x_twitter",     "log": "/tmp/start_all.log"},
        "x_xcn":        {"flag": ".digest_sent_x_xcn",         "log": "/tmp/start_all.log"},
        "x_xai":        {"flag": ".digest_sent_x_xai",         "log": "/tmp/start_all.log"},
        "x_xniche":     {"flag": ".digest_sent_x_xniche",      "log": "/tmp/start_all.log"},
        "reddit":       {"flag": ".digest_sent_reddit_reddit",  "log": "/tmp/start_all.log"},
    }

    lines = [f"<b>🟧 Cron Jobs — {today_str}</b>\n"]
    lines.append("<pre>")
    lines.append(f"{'Job':<14} {'Last Run':<12} {'Status'}")
    lines.append(f"{'-'*14} {'-'*12} {'-'*6}")

    for job_name, info in jobs.items():
        flag_path = os.path.join(PROJECT_DIR, info["flag"])
        if os.path.exists(flag_path):
            try:
                with open(flag_path) as f:
                    content = f.read().strip()
                if content == today_str:
                    status = "✅"
                    last_run = "today"
                else:
                    status = "⏭"
                    last_run = content[:10] if content else "?"
            except Exception:
                status = "❓"
                last_run = "err"
        else:
            status = "❌"
            last_run = "no flag"

        lines.append(f"{job_name:<14} {last_run:<12} {status}")

    lines.append("</pre>")

    # Check log for recent errors
    log_path = "/tmp/start_all.log"
    if os.path.exists(log_path):
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c",
            f"grep -i 'error\\|traceback\\|exception' {log_path} | tail -5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        errors = stdout.decode().strip()
        if errors:
            lines.append("\n<b>Recent Errors</b>")
            # Truncate and escape for HTML
            err_text = errors[:800]
            err_text = err_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"<pre>{err_text}</pre>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /rerun <job> — Manually re-trigger a digest ─────────────────────────

@admin_only
async def cmd_rerun(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-run a digest job: delete flag file and run the script in background."""
    RERUN_JOBS = {
        "bot1": {
            "flag": ".digest_sent_news_bot1",
            "cmd": ["python", "send_digest.py", "bot1"],
        },
        "bot2": {
            "flag": ".digest_sent_news_bot2",
            "cmd": ["python", "send_digest.py", "bot2"],
        },
        "twitter": {
            "flag": ".digest_sent_x_twitter",
            "cmd": ["python", "send_xdigest.py", "twitter"],
        },
        "xcn": {
            "flag": ".digest_sent_x_xcn",
            "cmd": ["python", "send_xdigest.py", "xcn"],
        },
        "xai": {
            "flag": ".digest_sent_x_xai",
            "cmd": ["python", "send_xdigest.py", "xai"],
        },
        "xniche": {
            "flag": ".digest_sent_x_xniche",
            "cmd": ["python", "send_xdigest.py", "xniche"],
        },
        "reddit": {
            "flag": ".digest_sent_reddit_reddit",
            "cmd": ["python", "send_reddit_digest.py"],
        },
        "china_trends": {
            "flag": None,
            "cmd": ["python", "china_trends.py"],
        },
        "code_review": {
            "flag": None,
            "cmd": ["python", "send_code_review.py"],
        },
        "evolution": {
            "flag": ".evolution_feed_sent",
            "cmd": ["python", "evolution_feed.py"],
        },
        "podcast": {
            "flag": ".podcast_digest_sent",
            "cmd": ["python", "podcast_digest.py"],
        },
        "youtube": {
            "flag": ".youtube_digest_sent",
            "cmd": ["python", "youtube_digest.py"],
        },
    }

    if not context.args:
        job_list = ", ".join(RERUN_JOBS.keys())
        await update.message.reply_text(f"Usage: /rerun &lt;job&gt;\nJobs: {job_list}", parse_mode="HTML")
        return

    job = context.args[0].lower()
    if job not in RERUN_JOBS:
        job_list = ", ".join(RERUN_JOBS.keys())
        await update.message.reply_text(f"Unknown job: {job}\nJobs: {job_list}")
        return

    info = RERUN_JOBS[job]

    # Delete the flag file if it exists
    if info["flag"]:
        flag_path = os.path.join(PROJECT_DIR, info["flag"])
        try:
            os.unlink(flag_path)
        except OSError:
            pass

    # Determine python path (use venv if available)
    python = os.path.join(PROJECT_DIR, "venv", "bin", "python")
    if not os.path.exists(python):
        python = "python3"

    cmd = [python] + info["cmd"][1:]  # replace "python" with actual path

    # Run in background
    await asyncio.create_subprocess_exec(
        *cmd,
        cwd=PROJECT_DIR,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    await update.message.reply_text(f"🔄 Re-running {job}...")


# ── /disk — VPS disk and memory usage ────────────────────────────────────

async def _panel_flag_sent(flag_name: str, today_str: str) -> bool:
    """Check if a digest flag file contains today's date."""
    fp = os.path.join(PROJECT_DIR, flag_name)
    if not os.path.exists(fp):
        return False
    try:
        with open(fp) as _f:
            return _f.read().strip() == today_str
    except Exception:
        return False


def _panel_primary_model() -> str:
    """Get primary LLM name for panel display."""
    try:
        sys.path.insert(0, PROJECT_DIR)
        from llm_client import PROVIDERS, _FALLBACK_CHAIN
        primary = _FALLBACK_CHAIN[0] if _FALLBACK_CHAIN else "?"
        return PROVIDERS.get(primary, {}).get("name", primary)
    except Exception:
        return "unknown"


async def _panel_overview_text_and_kb(today_str: str):
    """Build overview text + keyboard for /panel."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    # Health summary
    process_patterns = [
        ("run_bot.py bot1", "Bot1"),
        ("run_bot.py bot2", "Bot2"),
        ("run_bot.py reddit", "Reddit"),
        ("admin_bot", "Admin"),
    ]
    bots_up = 0
    for pattern, _ in process_patterns:
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", pattern,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if out.decode().strip():
            bots_up += 1
    bots_total = len(process_patterns)
    bots_icon = "✅" if bots_up == bots_total else f"{bots_up}/{bots_total}"

    x_flags = [".digest_sent_x_twitter", ".digest_sent_x_xcn",
                ".digest_sent_x_xai", ".digest_sent_x_xniche"]
    x_results = await asyncio.gather(*[_panel_flag_sent(f, today_str) for f in x_flags])
    x_sent = sum(x_results)
    x_icon = "✅" if x_sent == 4 else f"{x_sent}/4"

    news_sent = any([
        await _panel_flag_sent(".digest_sent_news_bot1", today_str),
        await _panel_flag_sent(".digest_sent_news_bot2", today_str),
    ])
    news_icon = "✅" if news_sent else "❌"

    # Sync summary
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--short", "HEAD",
            cwd=PROJECT_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        local_out, _ = await proc.communicate()
        local_hash = local_out.decode().strip()
        proc2 = await asyncio.create_subprocess_exec(
            "git", "ls-remote", "origin", "main",
            cwd=PROJECT_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        remote_out, _ = await asyncio.wait_for(proc2.communicate(), timeout=5)
        remote_full = remote_out.decode().strip().split("\t")[0] if remote_out.decode().strip() else ""
        sync_ok = bool(local_hash) and remote_full.startswith(local_hash)
        sync_icon = "✅" if sync_ok else "⚠️"
    except Exception:
        sync_icon = "?"
    sync_line = f"🔄 Sync: Mac=VPS {sync_icon}"

    # Content summary
    draft_count = 0
    queue_count = 0
    if _OPS_GUARD_AVAILABLE:
        try:
            from datetime import datetime as _dt
            today = _dt.now().strftime("%Y-%m-%d")
            log_path = os.path.join(PROJECT_DIR, "content_drafts", "running_log.md")
            if os.path.exists(log_path):
                with open(log_path) as _f:
                    ct = _f.read()
                draft_count = sum(1 for b in ct.split("## [")[1:] if today in b[:20])
        except Exception:
            pass
        try:
            result = content_queue(action="list")
            queue_count = sum(
                1 for item in result.get("queue", [])
                if "~~POSTED~~" not in item.get("text", "")
            )
        except Exception:
            pass

    # Config summary
    persona_count = 0
    try:
        personas_dir = os.path.join(PROJECT_DIR, "personas")
        persona_count = len([f for f in os.listdir(personas_dir) if f.endswith(".json")])
    except Exception:
        pass

    # Outreach summary
    svc_active = False
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "is-active", "outreach-autoreply",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        svc_out, _ = await proc.communicate()
        svc_active = svc_out.decode().strip() == "active"
    except Exception:
        pass
    if not svc_active:
        try:
            proc2 = await asyncio.create_subprocess_exec(
                "pgrep", "-f", "auto_reply",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            pgrep_out, _ = await proc2.communicate()
            svc_active = bool(pgrep_out.decode().strip())
        except Exception:
            pass

    sep = "─────────"
    text = (
        f"<b>🟧 Panel</b>\n{sep}\n"
        f"❤️ Health: {bots_up}/{bots_total} bots {bots_icon}, X digest {x_icon}, news {news_icon}\n"
        f"{sync_line}\n"
        f"📝 Content: {draft_count} draft, {queue_count} queued\n"
        f"⚙️ Config: {_panel_primary_model()}, {persona_count} personas\n"
        f"📨 Outreach: auto-reply {'ON' if svc_active else 'OFF'}\n"
        f"{sep}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("❤️", callback_data="panel:health"),
        InlineKeyboardButton("🔄", callback_data="panel:sync"),
        InlineKeyboardButton("📝", callback_data="panel:content"),
        InlineKeyboardButton("⚙️", callback_data="panel:config"),
        InlineKeyboardButton("📨", callback_data="panel:outreach"),
    ]])
    return text, kb


@admin_only
async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Control panel overview with 5 tabs: Health, Sync, Content, Config, Outreach."""
    from datetime import datetime, timezone
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text, kb = await _panel_overview_text_and_kb(today_str)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


@admin_only
async def cmd_disk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show VPS disk and memory usage."""
    lines = ["<b>🟧 VPS Resources</b>\n"]

    # Disk usage
    lines.append("<b>Disk Usage</b>")
    proc = await asyncio.create_subprocess_exec(
        "df", "-h", "/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    df_out = stdout.decode().strip()
    if df_out:
        # Format as preformatted block
        df_out = df_out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"<pre>{df_out}</pre>")

    # Memory usage
    lines.append("\n<b>Memory Usage</b>")
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", "free -h 2>/dev/null || echo 'free not available (macOS)'",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    mem_out = stdout.decode().strip()
    if mem_out:
        mem_out = mem_out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"<pre>{mem_out}</pre>")

    # Top 5 memory-consuming processes
    lines.append("\n<b>Top Processes (by memory)</b>")
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", "ps aux --sort=-%mem 2>/dev/null | head -6 || ps aux -m 2>/dev/null | head -6",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    ps_out = stdout.decode().strip()
    if ps_out:
        # Truncate long lines for readability
        ps_lines = ps_out.split("\n")
        trimmed = []
        for pl in ps_lines[:6]:
            trimmed.append(pl[:120])
        ps_text = "\n".join(trimmed)
        ps_text = ps_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"<pre>{ps_text}</pre>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /watchdog — Manual fetch source probe ─────────────────────────────

@admin_only
async def cmd_watchdog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run fetch watchdog probes on demand: /watchdog"""
    await update.message.reply_text("🔍 Running fetch watchdog probes...")
    try:
        from fetch_watchdog import run_all_probes, auto_fix, record_run, format_report
        results = await run_all_probes()
        actions = await auto_fix(results)
        analysis = record_run(results)
        report = format_report(results, analysis, actions)
        await update.message.reply_text(report, parse_mode="HTML")
    except Exception as e:
        log.error("Watchdog command failed: %s", e)
        await update.message.reply_text(f"❌ Watchdog failed: {e}")


# ── /homein — Going home: send TG session to Mac ─────────────────────

@admin_only
async def cmd_homein(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Going home — send TG session to Mac: /homein"""
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    domain = _detect_domain(chat_id, thread_id) or "news"
    key = _session_key(domain, thread_id)
    sessions = _load_sessions()
    sid = sessions.get(key)
    if not sid:
        await update.message.reply_text("⚠️ No active session for this topic.")
        return

    # Write to pending_resume.txt for Mac /homein to pick up
    pending_path = os.path.expanduser(
        os.environ.get("CLAUDE_MEMORY_DIR", "~/.claude/projects/[project-dir]/memory") + "/pending_resume.txt"
    )
    os.makedirs(os.path.dirname(pending_path), exist_ok=True)
    with open(pending_path, "w") as f:
        f.write(sid)
    await update.message.reply_text(
        f"🟧 Session saved for Mac:\n<code>{sid}</code>\n\n"
        f"Domain: {key}\n"
        f"到家後 Mac 行: <code>/homein</code>",
        parse_mode="HTML",
    )


# ── /homeout — Left home: receive Mac session on TG ───────────────────

@admin_only
async def cmd_homeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Left home — receive Mac session on TG: /homeout or /homeout <session_id>"""
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    domain = _detect_domain(chat_id, thread_id) or "news"
    key = _session_key(domain, thread_id)

    session_id = None

    if context.args:
        session_id = context.args[0]
    else:
        # Check for pending session from Mac /homeout
        pending_path = os.path.expanduser(
            os.environ.get("CLAUDE_MEMORY_DIR", "~/.claude/projects/[project-dir]/memory") + "/pending_resume.txt"
        )
        if os.path.exists(pending_path):
            with open(pending_path) as f:
                session_id = f.read().strip()
            os.unlink(pending_path)

    if not session_id:
        # Show current sessions so user can pick one
        sessions = _load_sessions()
        if sessions:
            lines = ["⚠️ No pending session. Paste a session ID:\n"
                     "<code>/homeout &lt;session_id&gt;</code>\n\n"
                     "<b>Active sessions:</b>"]
            for k, sid in sessions.items():
                lines.append(f"• {k}: <code>{sid}</code>")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        else:
            await update.message.reply_text(
                "⚠️ No pending session.\n"
                "<code>/homeout &lt;session_id&gt;</code>\n\n"
                "Or Mac 行 /homeout 先。",
                parse_mode="HTML",
            )
        return

    import re as _re
    if not _re.match(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        session_id, _re.I,
    ):
        await update.message.reply_text(f"❌ Invalid session ID: {session_id}")
        return

    sessions = _load_sessions()
    sessions[key] = session_id
    _save_sessions(sessions)
    await update.message.reply_text(
        f"🟧 Mac session resumed:\n<code>{session_id}</code>\n\n"
        f"Domain: {key}\nNext message continues that conversation.",
        parse_mode="HTML",
    )


# ── /config — live settings viewer ────────────────────────────────────

@admin_only
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show condensed command menu."""
    text = (
        "<b>📋 Menu</b>\n\n"
        "/panel \u2014 Control panel (health, sync, content, config, outreach)\n"
        "/session \u2014 Transfer Mac\u2194Phone\n"
        "/q \u2014 Quick batch message\n\n"
        "<i>Tip: /panel has everything. Old commands still work.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


@admin_only
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current bot configs, digest schedules, and model routing."""
    lines = ["<b>⚙️ System Config</b>\n"]

    # ── LLM providers & fallback chain (live from llm_client.py) ──
    try:
        sys.path.insert(0, PROJECT_DIR)
        from llm_client import PROVIDERS, _FALLBACK_CHAIN
        primary = _FALLBACK_CHAIN[0] if _FALLBACK_CHAIN else "unknown"
        primary_name = PROVIDERS.get(primary, {}).get("name", primary)
        lines.append(f"<b>Primary LLM:</b> {primary_name}")
        chain_names = [PROVIDERS.get(k, {}).get("name", k) for k in _FALLBACK_CHAIN]
        lines.append(f"<b>Fallback chain:</b> {' → '.join(chain_names)}")
        all_providers = [f"{v['name']} ({v['model']})" for v in PROVIDERS.values()]
        lines.append(f"<b>Available:</b> {', '.join(all_providers)}\n")
    except Exception as e:
        lines.append(f"<b>LLM:</b> (error: {e})\n")

    # ── Persona configs ──
    lines.append("<b>Personas:</b>")
    personas_dir = os.path.join(PROJECT_DIR, "personas")
    for fname in sorted(os.listdir(personas_dir)):
        if not fname.endswith(".json"):
            continue
        pid = fname[:-5]
        try:
            with open(os.path.join(personas_dir, fname)) as f:
                cfg = json.load(f)
            voice = "🎤" if cfg.get("whisper_language") else ""
            topics = ", ".join(cfg.get("topic_names", [])) or "—"
            lines.append(f"  <b>{pid}</b>: {voice} topics=[{topics}]")
        except Exception:
            lines.append(f"  <b>{pid}</b>: (error reading)")

    # ── Cron overview — try local first (if on VPS), then SSH ──
    lines.append("\n<b>VPS Cron:</b>")
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c",
            "crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=5
        )
        cron_out = stdout.decode().strip()
        if not cron_out:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-o", "ConnectTimeout=3",
                f"{os.getenv('VPS_USER', 'YOUR_VPS_USER')}@{os.getenv('VPS_HOST', 'YOUR_VPS_IP')}",
                "crontab -l 2>/dev/null"
                " | grep -v '^#' | grep -v '^$'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=10
            )
            cron_out = stdout.decode().strip()
        for cl in cron_out.split("\n")[:15]:
            if cl.strip():
                lines.append(
                    f"  <code>{html_escape(cl[:80])}</code>"
                )
    except Exception:
        lines.append("  (could not fetch)")

    # ── Admin bot model routing (live from chat.py) ──
    lines.append("\n<b>Admin Model Routing:</b>")
    try:
        from .chat import _OPUS_KW, _SONNET_KW, _HAIKU_KW
        lines.append(f"  Opus: {', '.join(kw for kw, _ in _OPUS_KW[:8])}...")
        lines.append(f"  Sonnet: {', '.join(kw for kw, _ in _SONNET_KW[:8])}...")
        lines.append(f"  Haiku: {', '.join(kw for kw, _ in _HAIKU_KW[:8])}...")
    except Exception:
        lines.append("  (could not load routing config)")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /trends — on-demand Chinese trends ────────────────────────────────

@admin_only
async def cmd_trends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger china_trends.py on demand."""
    await update.message.reply_text("🔄 Generating Chinese trends...")
    try:
        venv_py = os.path.join(PROJECT_DIR, "venv", "bin", "python")
        script = os.path.join(PROJECT_DIR, "china_trends.py")
        proc = await asyncio.create_subprocess_exec(
            venv_py, script,
            cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        output = stdout.decode().strip()
        if proc.returncode == 0:
            await update.message.reply_text(f"✅ Trends sent (rc=0)")
        else:
            await update.message.reply_text(f"⚠️ Trends finished (rc={proc.returncode}):\n{output[-500:]}")
    except asyncio.TimeoutError:
        await update.message.reply_text("⚠️ Trends timed out (300s)")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}")


# ── /usage — cost tracking ────────────────────────────────────────────

@admin_only
async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show daily and weekly API cost breakdown."""
    try:
        import sys
        sys.path.insert(0, PROJECT_DIR)
        from cost_tracker import get_daily_costs, get_weekly_costs

        daily = get_daily_costs()
        weekly = get_weekly_costs()

        lines = ["<b>💰 API Usage</b>\n"]
        lines.append(f"<b>Today:</b> ${daily['total_cost_usd']:.2f} ({daily['total_calls']} calls)")
        for model, data in sorted(daily["by_model"].items()):
            lines.append(f"  {model}: ${data['cost_usd']:.2f} ({data['calls']} calls)")

        lines.append(f"\n<b>7 Days:</b> ${weekly['total_cost_usd']:.2f} ({weekly['total_calls']} calls)")
        for model, data in sorted(weekly["by_model"].items()):
            lines.append(f"  {model}: ${data['cost_usd']:.2f} ({data['calls']} calls)")

        if weekly.get("by_persona"):
            lines.append("\n<b>By Persona (7d):</b>")
            for persona, data in sorted(weekly["by_persona"].items(), key=lambda x: -x[1]["cost_usd"]):
                lines.append(f"  {persona}: ${data['cost_usd']:.2f} ({data['calls']} calls)")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Cost tracking error: {type(e).__name__}: {e}")


# ── /export — export memory DB ────────────────────────────────────────

@admin_only
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export a persona's memory DB as JSON file."""
    args = (update.message.text or "").split()
    persona = args[1] if len(args) > 1 else "admin"

    # Validate persona name to prevent path traversal
    if "/" in persona or ".." in persona or len(persona) > 20:
        await update.message.reply_text("Invalid persona name.")
        return

    db_path = os.path.join(PROJECT_DIR, f"memory_{persona}.db")
    if not os.path.exists(db_path):
        await update.message.reply_text(f"❌ No memory DB for '{persona}'")
        return

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM messages ORDER BY rowid DESC LIMIT 500").fetchall()
        finally:
            conn.close()

        data = [dict(r) for r in rows]
        export_path = f"/tmp/memory_{persona}_export.json"
        with open(export_path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        with open(export_path, "rb") as doc:
            await update.message.reply_document(
                document=doc,
                filename=f"memory_{persona}.json",
                caption=f"📦 {len(data)} memories exported for {persona}",
            )
        os.unlink(export_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Export error: {type(e).__name__}: {e}")


# ── /unsent — dead letter queue viewer ────────────────────────────────

@admin_only
async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available Claude skills as inline buttons."""
    skills_dir = os.path.expanduser("~/.claude/skills")
    if not os.path.isdir(skills_dir):
        await update.message.reply_text("No skills directory found")
        return

    # Short descriptions for skills without good frontmatter
    FALLBACK_DESC = {
        "apply-evolution": "Apply AI evolution patches",
        "autoresearch-agent": "Auto-optimize by metric",
        "build": "Agent-based dev + verify",
        "chatid": "Show session ID",
        "content-humanizer": "Make AI text sound human",
        "critic": "Adversarial code review",
        "docx": "Word documents",
        "extractskill": "Evaluate + extract skills",
        "homein": "Resume from phone",
        "homeout": "Transfer to phone",
        "indicator-plugged": "Menubar voice indicator",
        "indicator-unplugged": "Floating dot indicator",
        "mcp-server-builder": "Scaffold MCP servers",
        "memory-maintenance": "Clean + promote memory",
        "pdf": "PDF tools",
        "planning-with-files": "Manus-style task plans",
        "pptx": "PowerPoint slides",
        "readme-gen": "Generate README.md",
        "remind": "Set timer reminder",
        "singlesourceoftruth": "Mac-VPS sync via git",
        "skill-security-auditor": "Scan skills for risks",
        "skillcleaning": "Audit installed skills",
        "system-check": "Full health check",
        "systematic-debugging": "4-phase root cause debug",
        "xlsx": "Excel spreadsheets",
        "event-register": "Auto-register Luma events",
        "firecrawl": "Web scraping",
        "githubupload": "Upload project to GitHub",
    }

    skills = []
    for name in sorted(os.listdir(skills_dir)):
        skill_md = os.path.join(skills_dir, name, "SKILL.md")
        if os.path.isfile(skill_md):
            desc = ""
            try:
                with open(skill_md) as f:
                    in_fm = False
                    for line in f:
                        if line.strip() == "---":
                            in_fm = not in_fm
                            continue
                        if in_fm and line.startswith("description:"):
                            raw = line.split(":", 1)[1].strip()
                            # Clean: remove quotes, pipes, "Use this skill" boilerplate
                            raw = raw.strip('"\'|> ')
                            if raw.lower().startswith("use this skill"):
                                raw = ""
                            desc = raw[:35]
                            break
            except Exception:
                pass
            if not desc:
                desc = FALLBACK_DESC.get(name, "")
            skills.append((name, desc))

    if not skills:
        await update.message.reply_text("No skills found")
        return

    # Build inline keyboard — 1 button per row, no "/" prefix
    buttons = []
    for name, desc in skills:
        label = f"{name}" + (f" — {desc}" if desc else "")
        buttons.append([InlineKeyboardButton(label, callback_data=f"skill:{name}")])

    await update.message.reply_text(
        f"<b>🧩 Available Skills ({len(skills)})</b>\n\nTap to run:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@admin_only
async def cmd_library(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show skill library stats, filter by category or status.

    /library — overview stats + recent discoveries
    /library memory — skills in memory category
    /library installed — show installed skills
    """
    import sys
    sys.path.insert(0, PROJECT_DIR)
    from skill_library import get_skills, get_stats

    arg = context.args[0].lower() if context.args else None

    # Filter by category
    if arg and arg in (
        "memory", "crawl", "evolution", "security", "office",
        "dev-tools", "content", "automation", "voice", "agent",
    ):
        skills = get_skills(category=arg)
        if not skills:
            await update.message.reply_text(f"No skills in category: {arg}")
            return
        lines = [f"<b>📚 Skill Library — {arg}</b> ({len(skills)})\n"]
        for s in skills[:20]:
            status_icon = {"discovered": "🔵", "evaluated": "🟡", "installed": "🟢",
                           "extracted": "🟣", "skipped": "⚪"}.get(s.get("status"), "⚪")
            name = html_escape(s.get("name", "")[:50])
            desc = html_escape(s.get("description", "")[:60])
            lines.append(f"{status_icon} <b>{name}</b>\n  {desc}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # Filter by status
    if arg and arg in ("discovered", "evaluated", "installed", "extracted", "skipped"):
        skills = get_skills(status=arg)
        if not skills:
            await update.message.reply_text(f"No skills with status: {arg}")
            return
        lines = [f"<b>📚 Skill Library — {arg}</b> ({len(skills)})\n"]
        for s in skills[:20]:
            name = html_escape(s.get("name", "")[:50])
            desc = html_escape(s.get("description", "")[:60])
            src = s.get("source", "")
            lines.append(f"• <b>{name}</b> [{src}]\n  {desc}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # Default: overview stats
    stats = get_stats()
    lines = [f"<b>📚 Skill Library</b> — {stats['total']} skills\n"]

    # By status
    if stats["by_status"]:
        lines.append("<b>Status</b>")
        status_icons = {"discovered": "🔵", "evaluated": "🟡", "installed": "🟢",
                        "extracted": "🟣", "skipped": "⚪"}
        for st, count in sorted(stats["by_status"].items(), key=lambda x: -x[1]):
            icon = status_icons.get(st, "⚪")
            lines.append(f"  {icon} {st}: {count}")

    # By category
    if stats["by_category"]:
        lines.append("\n<b>Categories</b>")
        for cat, count in sorted(stats["by_category"].items(), key=lambda x: -x[1]):
            lines.append(f"  • {cat}: {count}")

    # By source
    if stats["by_source"]:
        lines.append("\n<b>Sources</b>")
        for src, count in sorted(stats["by_source"].items(), key=lambda x: -x[1]):
            lines.append(f"  • {src}: {count}")

    # Recent discoveries
    recent = stats.get("recent", [])
    if recent:
        lines.append(f"\n<b>Recent (7d)</b> — {len(recent)} new")
        for s in recent[:5]:
            name = html_escape(s.get("name", "")[:45])
            src = s.get("source", "")
            lines.append(f"  • {name} [{src}]")

    if stats["total"] == 0:
        lines.append("\nNo skills yet. Run /digest rerun evolution to populate.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@admin_only
async def cmd_unsent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show failed message deliveries."""
    dl_path = os.path.join(PROJECT_DIR, "dead_letters.json")
    if not os.path.exists(dl_path):
        await update.message.reply_text("✅ No unsent messages")
        return

    try:
        with open(dl_path) as f:
            entries = json.load(f)
        if not entries:
            await update.message.reply_text("✅ No unsent messages")
            return

        lines = [f"<b>📭 {len(entries)} Unsent Messages</b>\n"]
        for e in entries[-10:]:
            ts = e.get("timestamp", "?")[:16]
            chat = e.get("chat_id", "?")
            text = html_escape(e.get("text", "")[:80])
            err = html_escape(e.get("error", "")[:60])
            lines.append(f"<code>{ts}</code> chat={chat}\n  {text}\n  ❌ {err}\n")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {e}")


@admin_only
async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show or resolve open goals. Usage: /goals | /goals done <id>"""
    from .cognitive import list_open_goals, resolve_goal
    args = context.args or []

    if args and args[0].lower() == "done" and len(args) >= 2:
        try:
            goal_id = int(args[1])
            if resolve_goal(goal_id):
                await update.message.reply_text(f"✅ Goal #{goal_id} resolved.")
            else:
                await update.message.reply_text(f"❌ Goal #{goal_id} not found.")
        except ValueError:
            await update.message.reply_text("Usage: /goals done <id>")
        return

    goals = list_open_goals()
    if not goals:
        await update.message.reply_text("🧠 No open goals.")
        return

    lines = ["<b>🧠 Open Goals</b>\n"]
    for g in goals:
        lines.append(f"<code>#{g['id']}</code> {g['text']}\n  <i>{g['created_at'][:10]}</i>")
    lines.append("\nUse /goals done &lt;id&gt; to resolve.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@admin_only
async def cmd_redteamstart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the red team test script."""
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c",
        "cd ~/telegram-claude-bot && source venv/bin/activate && source .env && "
        "nohup python outreach/red_team_auto.py > /tmp/red_team_results.log 2>&1 & echo $!",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    pid = stdout.decode().strip()
    red_team_bot = os.environ.get("RED_TEAM_BOT_USERNAME", "your_test_bot")
    await update.message.reply_text(f"Red team (live TG) started (PID: {pid})\n\n~380 attacks via @{red_team_bot}\n/redteamstop to cancel")


@admin_only
async def cmd_redteamstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the red team test script."""
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", "pkill -f 'red_team_auto.py' 2>/dev/null && echo killed || echo not_running",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if "killed" in stdout.decode():
        await update.message.reply_text("Red team (live) stopped.")
    else:
        await update.message.reply_text("Red team not running.")


@admin_only
async def cmd_autoreplyon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the auto-reply service."""
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "start", "outreach-autoreply",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    await update.message.reply_text("Auto-reply ON")


@admin_only
async def cmd_autoreplyoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the auto-reply service."""
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "stop", "outreach-autoreply",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    await update.message.reply_text("Auto-reply OFF")


@admin_only
async def cmd_autolist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all chats with auto-reply enabled (via ...on)."""
    db_path = os.path.join(PROJECT_DIR, "outreach.db")
    try:
        import sqlite3 as _sq
        conn = _sq.connect(db_path)
        rows = conn.execute(
            "SELECT chat_id, updated_at FROM auto_reply_state "
            "WHERE auto_enabled = 1 ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
    except Exception as e:
        await update.message.reply_text(f"❌ DB error: {e}")
        return

    if not rows:
        await update.message.reply_text("No chats with auto-reply enabled.\nUse <code>...on</code> inside a DM to activate.", parse_mode="HTML")
        return

    lines = [f"<b>Auto-reply active ({len(rows)} chats):</b>\n"]
    for chat_id, updated in rows:
        lines.append(f"• <code>{chat_id}</code> (since {updated[:16]})")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@admin_only
async def cmd_redteameval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run the red team evaluator to grade bot responses."""
    await update.message.reply_text("Starting red team evaluation...\nThis takes ~5 min (LLM grading 40+ batches)")
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c",
        "cd ~/telegram-claude-bot && source venv/bin/activate && source .env && "
        "python outreach/red_team_eval.py 2>&1 | tail -30",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        await update.message.reply_text("⚠️ Red team eval timed out (10 min)")
        return
    output = stdout.decode().strip()
    if not output:
        output = stderr.decode().strip() or "No output"

    # Extract TG summary (last few lines) and send
    lines = output.split("\n")
    # Find the summary section
    summary_lines = []
    in_summary = False
    for line in lines:
        if "SUMMARY" in line or "TG Summary" in line:
            in_summary = True
        if in_summary:
            summary_lines.append(line)
        if len(summary_lines) > 15:
            break

    if summary_lines:
        msg = "\n".join(summary_lines)
    else:
        # Send last 20 lines as fallback
        msg = "\n".join(lines[-20:])

    # Truncate to TG message limit
    if len(msg) > 4000:
        msg = msg[:4000] + "\n..."

    await update.message.reply_text(
        f"<b>Red Team Eval Complete</b>\n\n"
        f"<pre>{html_escape(msg)}</pre>\n\n"
        f"Full report: /tmp/red_team_report.txt",
        parse_mode="HTML",
    )


@admin_only
async def cmd_redteamoffline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start offline red team test (no Telegram messages, uses LLM directly)."""
    # Check if already running
    check = await asyncio.create_subprocess_exec(
        "pgrep", "-f", "red_team_offline",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await check.communicate()
    if stdout.decode().strip():
        await update.message.reply_text("Offline red team already running.")
        return

    proc = await asyncio.create_subprocess_exec(
        "bash", "-c",
        "cd ~/telegram-claude-bot && source venv/bin/activate && source .env && "
        "nohup python outreach/red_team_offline.py >> /tmp/red_team_offline.log 2>&1 & echo $!",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    pid = stdout.decode().strip()
    await update.message.reply_text(
        f"Offline red team started (PID: {pid})\n\n"
        f"~380 attacks, ~60 min\n"
        f"You'll get a TG notification when done.\n\n"
        f"/redteamofflinestop to cancel"
    )


@admin_only
async def cmd_redteamofflinestop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the offline red team test."""
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", "pkill -f 'red_team_offline.py' 2>/dev/null && echo killed || echo not_running",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if "killed" in stdout.decode():
        await update.message.reply_text("Offline red team stopped.")
    else:
        await update.message.reply_text("Offline red team not running.")


@admin_only
async def cmd_redteamgenerate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate new red team attacks from last test results."""
    # Check if report exists
    if not os.path.exists("/tmp/red_team_report.md"):
        await update.message.reply_text("No report found at /tmp/red_team_report.md\nRun /redteamoffline first.")
        return

    # Check if already running
    check = await asyncio.create_subprocess_exec(
        "pgrep", "-f", "red_team_generate",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await check.communicate()
    if stdout.decode().strip():
        await update.message.reply_text("Attack generator already running.")
        return

    proc = await asyncio.create_subprocess_exec(
        "bash", "-c",
        "cd ~/telegram-claude-bot && source venv/bin/activate && source .env && "
        "nohup python outreach/red_team_generate.py > /tmp/red_team_generate.log 2>&1 & echo $!",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    pid = stdout.decode().strip()
    await update.message.reply_text(
        f"Attack generator started (PID: {pid})\n\n"
        f"~380 new attacks from last report\n"
        f"~10 min (19 batches x LLM calls)\n"
        f"You'll get a TG notification when done.\n\n"
        f"Output: /tmp/red_team_gen_attacks.py\n"
        f"Then run: /redteamofflinegen to test them"
    )


@admin_only
async def cmd_redteamofflinegen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run offline red team test using generated attacks."""
    gen_file = "/tmp/red_team_gen_attacks.py"
    if not os.path.exists(gen_file):
        await update.message.reply_text(
            "No generated attacks found at /tmp/red_team_gen_attacks.py\n"
            "Run /redteamgenerate first."
        )
        return

    # Check if already running
    check = await asyncio.create_subprocess_exec(
        "pgrep", "-f", "red_team_offline",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await check.communicate()
    if stdout.decode().strip():
        await update.message.reply_text("Offline red team already running.")
        return

    proc = await asyncio.create_subprocess_exec(
        "bash", "-c",
        "cd ~/telegram-claude-bot && source venv/bin/activate && source .env && "
        f"nohup python outreach/red_team_offline.py --attacks {gen_file} >> /tmp/red_team_offline.log 2>&1 & echo $!",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    pid = stdout.decode().strip()
    await update.message.reply_text(
        f"Offline red team (generated attacks) started (PID: {pid})\n\n"
        f"Using attacks from: {gen_file}\n"
        f"~60 min\n"
        f"You'll get a TG notification when done.\n\n"
        f"/redteamofflinestop to cancel"
    )


# ── Background tasks ──────────────────────────────────────────────────────
# Shared registry: both /bg command and auto-background (bridge.py) use this.

from .bg_tasks import (
    bg_tasks as _bg_tasks, next_task_id as _next_task_id,
    register_task as _register_task, unregister_task as _unregister_task,
    get_status_text as _get_bg_status_text,
)


async def _run_bg_task(bot, chat_id, thread_id, task_id, task_desc):
    """Run Claude task in background (/bg command) and notify on completion."""
    import time as _time
    from .sdk_client import _get_or_create_client
    from claude_agent_sdk import (
        AssistantMessage, ResultMessage, TextBlock,
    )

    start = _time.monotonic()
    try:
        client = await _get_or_create_client("news", "sonnet", PROJECT_DIR)

        result_text = ""
        text_chunks = []

        await client.query(task_desc)
        async for sdk_msg in client.receive_messages():
            if isinstance(sdk_msg, ResultMessage):
                result_text = sdk_msg.result or ""
                break
            elif isinstance(sdk_msg, AssistantMessage):
                for block in sdk_msg.content:
                    if isinstance(block, TextBlock) and block.text:
                        text_chunks.append(block.text)

        if not result_text and text_chunks:
            result_text = text_chunks[-1]

        elapsed = _time.monotonic() - start
        mins, secs = divmod(int(elapsed), 60)
        time_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"

        if result_text:
            result_text = _clean_result(result_text)
            header = f"✅ Background task #{task_id} done ({time_str}):\n{task_desc[:80]}\n\n"
            await _send_msg(bot, chat_id, header + result_text, thread_id=thread_id)
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ Background task #{task_id} done ({time_str}):\n{task_desc[:80]}\n\n(no text output)",
                message_thread_id=thread_id,
            )

    except Exception as e:
        log.error("bg task %s failed: %s", task_id, e)
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Background task #{task_id} failed:\n{task_desc[:80]}\n\nError: {str(e)[:500]}",
            message_thread_id=thread_id,
        )
    finally:
        _unregister_task(task_id)


@admin_only
async def cmd_bg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run a Claude task in the background, or show running tasks.
    /bg         — show all running background tasks (incl. auto-backgrounded)
    /bg <task>  — force-background a task (skip 5s wait, immediate background)
    """
    # No args — show running tasks (both /bg and auto-backgrounded)
    if not context.args:
        status = _get_bg_status_text()
        await update.message.reply_text(status, parse_mode="HTML")
        return

    task = " ".join(context.args)
    task_id = _next_task_id()
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id if chat_id != ADMIN_USER_ID else None

    # Spawn background task immediately (skip 5s wait)
    bg = asyncio.create_task(
        _run_bg_task(context.bot, chat_id, thread_id, task_id, task)
    )
    _register_task(task_id, task, bg)

    await update.message.reply_text(
        f"⏳ Background task #{task_id} started:\n{task[:100]}\n\n"
        f"I'll notify you when done. /bg to check, /bgkill {task_id} to stop."
    )


@admin_only
async def cmd_bgkill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kill a background task by ID. Usage: /bgkill <id>"""
    if not context.args:
        await update.message.reply_text("Usage: /bgkill &lt;id&gt;\n/bg to see running tasks.", parse_mode="HTML")
        return

    task_id = context.args[0].strip("#")
    if task_id not in _bg_tasks:
        await update.message.reply_text(f"Task #{task_id} not found.\n/bg to see running tasks.")
        return

    info = _bg_tasks[task_id]
    task_obj = info.get("task")
    if task_obj and not task_obj.done():
        task_obj.cancel()
    _unregister_task(task_id)
    await update.message.reply_text(f"Task #{task_id} killed: {info['description'][:60]}")


# ── Content pipeline commands (ops-guard-mcp lib) ────────────────────────

@admin_only
async def cmd_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Content pipeline: /content add <text>, /content list, /content next, /content posted"""
    if not _OPS_GUARD_AVAILABLE:
        await update.message.reply_text("ops-guard-mcp not available. Clone it to ~/ops-guard-mcp first.")
        return

    args = update.message.text.split(None, 2)
    action = args[1] if len(args) > 1 else "list"

    if action == "add":
        text = args[2] if len(args) > 2 else ""
        if not text:
            await update.message.reply_text("Usage: /content add <tweet text>")
            return
        result = content_queue(action="add", tweet=text)
        git_commit_push_async("auto: tweet queued from TG")
        await update.message.reply_text(f"💾 Queued at position {result.get('position', '?')}")
    elif action == "next":
        result = content_queue(action="next")
        if result.get("queue_empty"):
            await update.message.reply_text("Queue empty.")
        else:
            await update.message.reply_text(f"📝 Next ({result.get('priority')}):\n\n{result.get('next', '')}")
    elif action == "posted":
        result = content_queue(action="posted")
        await update.message.reply_text("✅ Marked as posted." if result.get("marked") else "No unposted items.")
    else:  # list
        result = content_queue(action="list")
        if not result.get("queue"):
            await update.message.reply_text("Queue empty.")
            return
        lines = [f"📝 Tweet Queue ({result['total']} items)\n"]
        for item in result["queue"]:
            posted = "~~POSTED~~" in item.get("text", "")
            icon = "✅" if posted else "📌"
            lines.append(f"{icon} [{item['priority']}] {item['date']}\n{item['text'][:100]}\n")
        await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's content draft entries from running_log.md"""
    if not _OPS_GUARD_AVAILABLE:
        await update.message.reply_text("ops-guard-mcp not available. Clone it to ~/ops-guard-mcp first.")
        return

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(PROJECT_DIR, "content_drafts", "running_log.md")
    if not os.path.exists(log_path):
        await update.message.reply_text("No running log yet.")
        return
    with open(log_path) as _f:
        content = _f.read()
    entries = []
    for block in content.split("## [")[1:]:
        if today in block[:20]:
            entries.append("## [" + block.strip())
    if not entries:
        await update.message.reply_text(f"No entries for {today}.")
        return
    text = f"📝 Drafts for {today}\n\n" + "\n\n".join(entries)
    await update.message.reply_text(text[:4000])


@admin_only
async def cmd_checkpoint_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the latest session checkpoint."""
    if not _OPS_GUARD_AVAILABLE:
        await update.message.reply_text("ops-guard-mcp not available. Clone it to ~/ops-guard-mcp first.")
        return

    drafts_dir = os.path.join(PROJECT_DIR, "content_drafts")
    checkpoints = sorted(
        [f for f in os.listdir(drafts_dir) if f.startswith("checkpoint_")],
        reverse=True
    ) if os.path.exists(drafts_dir) else []
    if not checkpoints:
        await update.message.reply_text("No checkpoints saved.")
        return
    latest = os.path.join(drafts_dir, checkpoints[0])
    with open(latest) as _f:
        text = _f.read()
    await update.message.reply_text(f"📋 Latest checkpoint:\n\n{text[:4000]}")
