# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Shared helper functions: message sending, parsing, decorators, inflight tracking."""
import asyncio
import json
import logging
import os
import re
import time

from telegram import Update
from telegram.ext import ContextTypes

from .config import ADMIN_USER_ID, _INFLIGHT_FILE

_log = logging.getLogger("admin")


async def timed_await(coro, label: str, warn_threshold: float = 10.0):
    """Await a coroutine and log a warning if it takes longer than warn_threshold seconds."""
    start = time.monotonic()
    try:
        return await coro
    finally:
        elapsed = time.monotonic() - start
        if elapsed > warn_threshold:
            _log.warning("SLOW AWAIT: %s took %.1fs (threshold: %.0fs)", label, elapsed, warn_threshold)


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id != ADMIN_USER_ID:
            return
        return await func(update, context)
    return wrapper


def _parse_claude_output(output: str) -> str:
    """Parse Claude Code JSON/stream-json output into clean result text."""
    if not output:
        return "(no output)"
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            return data.get("result", output)
        elif isinstance(data, list):
            texts = []
            for item in data:
                if isinstance(item, dict):
                    if item.get("type") == "result":
                        return item.get("result", "")
                    elif item.get("type") == "assistant":
                        for block in item.get("message", {}).get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                texts.append(block["text"])
            return "\n".join(texts) if texts else output[:2000]
        return str(data)[:2000]
    except (json.JSONDecodeError, AttributeError):
        return output[:2000]


def _parse_step(event):
    """Extract a short progress description from a stream-json event."""
    etype = event.get("type")
    if etype == "assistant":
        msg = event.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                inp = block.get("input", {})
                if name == "Bash":
                    return f"$ {inp.get('command', '')[:120]}"
                elif name == "Read":
                    return f"Reading {inp.get('file_path', '')}"
                elif name == "Edit":
                    return f"Editing {inp.get('file_path', '')}"
                elif name == "Write":
                    return f"Writing {inp.get('file_path', '')}"
                elif name == "Glob":
                    return f"Searching {inp.get('pattern', '')}"
                elif name == "Grep":
                    return f"Grep: {inp.get('pattern', '')}"
                elif name == "WebSearch":
                    return f"Searching: {inp.get('query', '')}"
                elif name == "WebFetch":
                    return f"Fetching: {inp.get('url', '')[:100]}"
                else:
                    return f"Using {name}"
            elif block.get("type") == "thinking":
                text = block.get("thinking", "")[:100]
                return f"Thinking: {text}..." if text else None
            elif block.get("type") == "text":
                text = block.get("text", "")[:80]
                if text:
                    return f"💬 {text}..."
    elif etype == "result":
        return None
    elif etype == "system":
        msg = event.get("message", "")[:80]
        if msg:
            return f"⚙️ {msg}"
    return None


def _clean_result(text: str) -> str:
    """Strip raw tool call XML and other noise from Claude Code output."""
    text = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'<function_calls>.*?</function_calls>', '', text, flags=re.DOTALL)
    text = re.sub(r'</?tool[^>]*>', '', text)
    text = re.sub(r'</?antml:[^>]*>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


async def _send_with_retry(bot, chat_id, text, thread_id=None, retries=2):
    """Send a message with RetryAfter handling."""
    for attempt in range(retries + 1):
        try:
            return await bot.send_message(
                chat_id=chat_id, text=text,
                message_thread_id=thread_id)
        except Exception as e:
            retry_after = getattr(e, 'retry_after', None)
            if retry_after and attempt < retries:
                await asyncio.sleep(retry_after + 0.5)
                continue
            raise


async def _send_msg(bot, chat_id, text, thread_id=None):
    """Send a message, splitting safely within Telegram's 4096 char limit."""
    if not text:
        return
    text = _clean_result(text)
    if not text:
        return
    MAX = 4000
    while text:
        if len(text) <= MAX:
            await _send_with_retry(bot, chat_id, text, thread_id)
            break
        cut = text.rfind('\n', 0, MAX)
        if cut < MAX // 2:
            cut = text.rfind(' ', 0, MAX)
        if cut < MAX // 4:
            cut = MAX
        chunk = text[:cut]
        text = text[cut:].lstrip('\n')
        await _send_with_retry(bot, chat_id, chunk, thread_id)


def _save_inflight(chat_id, msg_id, key):
    try:
        data = {"chat_id": chat_id, "msg_id": msg_id, "key": key, "ts": time.time()}
        with open(_INFLIGHT_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _clear_inflight():
    try:
        os.unlink(_INFLIGHT_FILE)
    except OSError:
        pass


async def _recover_inflight(app):
    """On startup, clean up stale status messages and orphan Claude processes."""
    import asyncio
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill", "-f", "claude.*--output-format.*stream-json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        pass

    try:
        if os.path.exists(_INFLIGHT_FILE):
            with open(_INFLIGHT_FILE) as f:
                data = json.load(f)
            await app.bot.edit_message_text(
                chat_id=data["chat_id"],
                message_id=data["msg_id"],
                text="🔄 Bot restarted — please resend your message.",
            )
            _clear_inflight()
            import logging
            logging.getLogger("admin").info("Recovered stale inflight request for [%s]", data.get("key"))
    except Exception as e:
        import logging
        logging.getLogger("admin").warning("Inflight recovery failed: %s", e)
        _clear_inflight()
