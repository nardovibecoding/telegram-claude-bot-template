import re
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Multi-persona Telegram bot base.
Each persona is defined in personas/<id>.json and run via: python run_bot.py <id>
"""
import asyncio
import os
import json
import logging
import tempfile
import time as _time_mod
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from news import generate_full_digest
from digest_ui import handle_digest_callback
from crypto_news import generate_crypto_digest
from stablecoin_yields import generate_yield_report
from twitter_feed import generate_twitter_digest
from x_feed import generate_xlist_digest
from x_curator import generate_daily_digest, make_key
from reddit_digest import generate_reddit_digest
from x_feedback import record_vote, get_vote
from memory import MemoryManager, format_memory_block, RECENT_WINDOW
from conversation_compressor import ConversationCompressor
from utils import CLAUDE_BIN, PROJECT_DIR
from conversation_logger import log_message
from sanitizer import sanitize_external_content
from llm_client import chat_completion_async, get_primary_client

# MiniMax removed — all LLM calls route through llm_client.py (Kimi → fallback chain)
PERSONAS_DIR    = Path(__file__).parent / "personas"
TOPIC_CACHE     = Path(__file__).parent / "topic_cache.json"
CLAUDE_SESSIONS_FILE = Path(__file__).parent / "claude_sessions.json"
CLAUDE_COST_LOG   = Path(__file__).parent / "claude_cost_log.jsonl"
MINIMAX_COST_LOG  = Path(__file__).parent / "minimax_cost_log.jsonl"

# When True, digest scheduling is handled by OS cron (send_digest.py, send_xdigest.py, etc.)
# Bot internal schedulers are disabled to prevent double-sends.
# Manual commands (/digest, /xdigest, /xcurate, /reddit) still work.
CRON_MANAGED = True

MAX_HISTORY = 40
INPUT_MAX_LENGTH = 10000
DEAD_LETTERS_FILE = Path(__file__).parent / "dead_letters.json"

# ── Security: allowed users & rate limiting ──────────────────────────────

_admin_id = int(os.environ.get("ADMIN_USER_ID", "0"))
_allowed_users_raw = os.environ.get("ALLOWED_USERS", "")
ALLOWED_USERS: set[int] = set()
if _allowed_users_raw:
    for _uid in _allowed_users_raw.split(","):
        _uid = _uid.strip()
        if _uid.isdigit():
            ALLOWED_USERS.add(int(_uid))
if _admin_id:
    ALLOWED_USERS.add(_admin_id)

# Per-user rate limiting: 10 messages per 60 seconds
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 10
_rate_limit_data: dict[int, list[float]] = defaultdict(list)
_rate_limit_notified: dict[int, float] = {}  # user_id → last notification time


def _is_allowed_user(user_id: int) -> bool:
    """Check if user is in the whitelist."""
    if not ALLOWED_USERS:
        return True  # no whitelist configured → allow all
    return user_id in ALLOWED_USERS


def _check_rate_limit(user_id: int) -> bool:
    """Return True if user is within rate limit, False if exceeded."""
    now = _time_mod.time()
    timestamps = _rate_limit_data[user_id]
    # Prune old timestamps
    _rate_limit_data[user_id] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
    if len(_rate_limit_data[user_id]) >= _RATE_LIMIT_MAX:
        return False
    _rate_limit_data[user_id].append(now)
    return True


def _save_dead_letter(chat_id: int, text: str, error: str) -> None:
    """Append a failed message to dead_letters.json (capped at 100 entries)."""
    import stat
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "text": text[:200],
        "error": error,
    }
    letters = []
    import fcntl
    try:
        with open(DEAD_LETTERS_FILE, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                letters = json.load(f)
            except (json.JSONDecodeError, ValueError):
                letters = []
            letters.append(entry)
            if len(letters) > 100:
                letters = letters[-100:]
            f.seek(0)
            f.truncate()
            json.dump(letters, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logging.warning("dead_letter write failed: %s", e)
    try:
        os.chmod(DEAD_LETTERS_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _get_today_cost() -> float:
    """Sum today's Claude Code costs from the JSONL log (HKT day boundary)."""
    if not CLAUDE_COST_LOG.exists():
        return 0.0
    from datetime import timedelta
    HKT = timezone(timedelta(hours=8))
    today_str = datetime.now(HKT).strftime("%Y-%m-%d")
    total = 0.0
    try:
        with open(CLAUDE_COST_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ts", "").startswith(today_str):
                        total += entry.get("cost_usd", 0)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("Failed to read cost log: %s", e)
    return total


DAILY_COST_CAP = float(os.environ.get("DAILY_COST_CAP", "50.0"))


# ── Claude Code session persistence (shared across persona bots) ─────────

def _load_bot_sessions() -> dict:
    if CLAUDE_SESSIONS_FILE.exists():
        try:
            return json.loads(CLAUDE_SESSIONS_FILE.read_text())
        except Exception as e:
            logging.warning("Failed to load sessions from %s, starting fresh: %s", CLAUDE_SESSIONS_FILE, e)
    return {}


def _save_bot_sessions(sessions: dict) -> None:
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=CLAUDE_SESSIONS_FILE.parent, suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(sessions, f, indent=2)
        os.replace(tmp_path, CLAUDE_SESSIONS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _log_claude_cost(persona_id: str, session_key: str, cost_usd: float, duration_ms: int) -> None:
    """Append Claude SDK cost entry to JSONL log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "persona": persona_id,
        "session": session_key,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
    }
    with open(CLAUDE_COST_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _log_minimax_cost(persona_id: str, model: str, input_tokens: int, output_tokens: int) -> None:
    """Append MiniMax/Groq usage entry to JSONL log."""
    # Pricing per 1M tokens (USD)
    _pricing = {
        "MiniMax-M2.5": {"input": 0.50, "output": 1.50},
        "MiniMax-M2.5-highspeed": {"input": 0.50, "output": 1.50},
        "llama-3.3-70b-versatile": {"input": 0.00, "output": 0.00},  # Groq free tier
    }
    p = _pricing.get(model, {"input": 0.50, "output": 1.50})
    cost_usd = (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "persona": persona_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }
    try:
        with open(MINIMAX_COST_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


# ── Topic cache (shared JSON file across all personas) ───────────────────────

_retry_logger = logging.getLogger("send_retry")

async def _send_with_retry(bot, chat_id, thread_id, retries=3, **kwargs) -> bool:
    for attempt in range(1, retries + 1):
        try:
            await bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                read_timeout=30, write_timeout=30, **kwargs,
            )
            return True
        except TelegramError as e:
            if attempt < retries:
                wait = attempt * 3
                _retry_logger.warning("Retry %d/%d in %ds (chat=%s): %s", attempt, retries, wait, chat_id, e)
                await asyncio.sleep(wait)
            else:
                _retry_logger.error("Give up after %d attempts (chat=%s): %s", retries, chat_id, e)
                _save_dead_letter(chat_id, kwargs.get("text", "")[:200], str(e))
                return False


def _load_cache() -> dict:
    if TOPIC_CACHE.exists():
        try:
            return json.loads(TOPIC_CACHE.read_text())
        except Exception as e:
            logging.warning("Failed to load cache from %s, starting fresh: %s", TOPIC_CACHE, e)
    return {}


def _save_cache(cache: dict) -> None:
    TOPIC_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


# ── Main runner ───────────────────────────────────────────────────────────────

def run_persona(persona_id: str) -> None:
    config_path = PERSONAS_DIR / f"{persona_id}.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Persona config not found: {config_path}")

    persona = json.loads(config_path.read_text())
    logger  = logging.getLogger(persona_id)

    # Token: try TELEGRAM_BOT_TOKEN_<ID> first, fall back to TELEGRAM_BOT_TOKEN
    token_env = f"TELEGRAM_BOT_TOKEN_{persona_id.upper()}"
    token = os.environ.get(token_env) or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError(f"Set {token_env} (or TELEGRAM_BOT_TOKEN) in .env")

    display_name   = persona["display_name"]
    topic_names    = {n.lower() for n in persona.get("topic_names", [])}
    system_prompt  = persona["system_prompt"]



    voice_enabled    = persona.get("voice_enabled", False)
    whisper_lang     = persona.get("whisper_language")
    news_module      = persona.get("news_module", "standard")
    yields_enabled   = persona.get("yields_enabled", False)
    digest_enabled   = persona.get("digest_enabled", True)
    twitter_enabled  = persona.get("twitter_enabled", False)
    twitter_accounts = persona.get("twitter_accounts", [])
    xcurate_target   = persona.get("xcurate_target")  # {"chat_id": ..., "thread_id": ...}
    xcurate_lang     = persona.get("xcurate_lang")    # "zh" for Chinese-only
    xcurate_lists    = persona.get("xcurate_lists", [])  # list IDs for list-based fetching
    reddit_enabled   = persona.get("reddit_enabled", False)
    reddit_subs      = persona.get("reddit_subreddits", [])
    reddit_target    = persona.get("reddit_target")   # {"chat_id": ..., "thread_id": ...}
    _responses     = persona.get("responses", {})

    def _r(key: str, **kw) -> str:
        """Return a persona-specific response string, with optional format vars."""
        tmpl = _responses.get(key, f"[{key}]")
        return tmpl.format(display_name=display_name, **kw)
    db_path        = Path(__file__).parent / f"memory_{persona_id}.db"
    subs_file      = Path(__file__).parent / f"subscribers_{persona_id}.json"

    import time as _time
    _start_time = _time.time()

    _llm_client, _llm_model = get_primary_client()
    logger.info("Primary LLM client: model=%s", _llm_model)
    if _llm_client is None:
        raise RuntimeError("No LLM provider available — check API keys in .env")
    mem    = MemoryManager(_llm_client, db_path=db_path, model_name=_llm_model)
    compressor = ConversationCompressor(_llm_client, model_name=_llm_model)
    convs: dict[tuple, list[dict]] = defaultdict(list)

    # In-memory topic cache (thread_id str → name str), keyed by group chat_id str
    topic_cache: dict[str, dict[str, str]] = _load_cache()

    # ── Whisper (optional) ────────────────────────────────────────────────────

    whisper_model = None
    if voice_enabled:
        from faster_whisper import WhisperModel
        logger.info("Loading Whisper for %s…", display_name)
        whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
        logger.info("Whisper ready.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _thread_name(chat_id: int, thread_id: int | None) -> str | None:
        g = topic_cache.get(str(chat_id), {})
        return g.get(str(thread_id or 0))

    def _cache_thread(chat_id: int, thread_id: int | None, name: str) -> None:
        g = str(chat_id)
        tid = str(thread_id or 0)
        topic_cache.setdefault(g, {})[tid] = name
        _save_cache(topic_cache)
        logger.info("Cached topic: chat=%s thread=%s → '%s'", g, tid, name)

    def _is_my_thread(chat_type: str, chat_id: int, thread_id: int | None) -> bool:
        if chat_type == "private":
            return True
        if not topic_names:
            return False
        name = _thread_name(chat_id, thread_id)
        return name is not None and name.lower() in topic_names

    def _check_private_user(update: Update) -> bool:
        """Return True if this private-chat user is allowed. Groups always pass."""
        chat_type = update.effective_chat.type
        if chat_type != "private":
            return True
        user_id = update.effective_user.id if update.effective_user else 0
        return _is_allowed_user(user_id)

    def _mem_key(chat_id: int, thread_id: int | None) -> str:
        return f"{chat_id}:{thread_id or 0}"

    def _conv_key(chat_id: int, thread_id: int | None) -> tuple:
        return (chat_id, thread_id or 0)

    # ── Subscriber helpers ────────────────────────────────────────────────────

    def _load_subs() -> set[int]:
        if subs_file.exists():
            try:
                return set(json.loads(subs_file.read_text()))
            except Exception:
                pass
        return set()

    def _save_subs(subs: set[int]) -> None:
        subs_file.write_text(json.dumps(sorted(subs)))

    def _sub_targets() -> list[tuple[int, int | None]]:
        """Return (chat_id, thread_id) pairs: private subs + registered topic threads."""
        targets: list[tuple[int, int | None]] = []
        for chat_id in _load_subs():
            targets.append((int(chat_id), None))
        for group_id_str, threads in topic_cache.items():
            for thread_id_str, name in threads.items():
                if name and name.lower() in topic_names:
                    targets.append((int(group_id_str), int(thread_id_str)))
        return targets

    async def _notify_fail(bot, job_name: str, error: Exception) -> None:
        """Send a failure notice to all subscribers."""
        from html import escape as _hesc
        msg = f"⚠️ <b>{_hesc(job_name)}</b> failed\n<code>{_hesc(str(error))}</code>"
        for chat_id, thread_id in _sub_targets():
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    text=msg,
                    parse_mode="HTML",
                )
            except Exception:
                pass

    # ── Core respond logic ────────────────────────────────────────────────────

    async def _respond(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        mk  = _mem_key(chat_id, thread_id)
        ck  = _conv_key(chat_id, thread_id)

        user_id = update.effective_user.id if update.effective_user else 0
        log_message(persona_id, user_id, "user", text, "text")

        try:
            await update.effective_chat.send_action("typing")

            # Truncate old messages BEFORE appending the new user turn
            conv = convs[ck]
            if len(conv) >= MAX_HISTORY:
                truncated = conv[:-MAX_HISTORY]
                compressor.absorb_truncated(ck, truncated)
                convs[ck] = conv[-MAX_HISTORY:]

            # Append user message to the (possibly rebound) active list
            convs[ck].append({"role": "user", "content": text})

            past = mem.retrieve(mk, text)
            memory_block = format_memory_block(past)
            system = system_prompt + ("\n\n" + memory_block if memory_block else "")
            if memory_block:
                logger.info("Injected %d memories for %s", len(past), mk)

            # Compress older messages into rolling summary
            await compressor.maybe_compress_async(ck, convs[ck], RECENT_WINDOW)
            summary_block = compressor.get_summary_block(ck)
            if summary_block:
                system = system + "\n\n" + summary_block

            recent = convs[ck][-RECENT_WINDOW:]
            messages = [{"role": "system", "content": system}, *recent]
            reply = await chat_completion_async(
                messages=messages,
                max_tokens=4096,
            )
            convs[ck].append({"role": "assistant", "content": reply})
            mem.store(mk, "user", text)
            mem.store(mk, "assistant", reply)
            log_message(persona_id, user_id, "assistant", reply, "text", model="llm_client")

            if len(reply) <= 4096:
                await update.message.reply_text(reply)
            else:
                for i in range(0, len(reply), 4096):
                    await update.message.reply_text(reply[i : i + 4096])

        except Exception as e:
            logger.error("Respond error: %s", e)
            # Remove the user message we just appended — no ghost entries
            if convs[ck] and convs[ck][-1].get("role") == "user":
                convs[ck].pop()
            await update.message.reply_text("⚠️ Something went wrong. Check logs.")

    # ── Claude Code fallback (for tasks needing tools) ─────────────────────

    # #1: Per-topic task queue — only 1 Claude Code task at a time per topic
    _claude_locks: dict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)
    # Track which topics are in "Claude mode" for follow-up routing (#6)
    _claude_active: dict[tuple, float] = {}  # conv_key → last claude timestamp
    CLAUDE_MODE_TIMEOUT = 300  # 5 min of inactivity → back to MiniMax

    # ── Photo accumulator (intent-based) + album dedup ────────────────────────
    _photo_queue: dict[tuple, list[dict]] = defaultdict(list)  # ck → [{file_id, caption}]
    _media_group_seen: dict[str, float] = {}  # media_group_id → timestamp

    # ── Text chunk merging (0.6s debounce) ────────────────────────────────────
    _text_buffer: dict[tuple, list[str]] = defaultdict(list)
    _text_buffer_tasks: dict[tuple, asyncio.Task] = {}
    _text_buffer_updates: dict[tuple, Update] = {}  # keep latest update for flush
    _text_buffer_contexts: dict[tuple, ContextTypes.DEFAULT_TYPE] = {}
    TEXT_MERGE_DELAY = 0.6

    def _in_claude_mode(ck: tuple) -> bool:
        """Check if this topic is in active Claude Code conversation."""
        last = _claude_active.get(ck)
        if last is None:
            return False
        import time as _t
        if _t.time() - last > CLAUDE_MODE_TIMEOUT:
            _claude_active.pop(ck, None)
            return False
        return True

    # #2: Smart task detection — ask MiniMax if unsure
    async def _needs_claude_smart(text: str) -> bool:
        """Use MiniMax to classify if a message needs tools."""
        lower = text.lower().strip()
        # Obvious tasks — skip the classifier
        obvious = ("research", "find out", "search for", "look up", "analyze",
                   "investigate", "scrape", "crawl", "find me", "dig into")
        if any(lower.startswith(t) for t in obvious):
            return True
        # Obvious chat — skip the classifier
        if len(text) < 10 or lower in ("hi", "hello", "hey", "ok", "thanks", "bye", "lol"):
            return False
        # Ask MiniMax (cheap, fast)
        try:
            resp = await asyncio.to_thread(
                _llm_client.chat.completions.create,
                model=_llm_model,
                max_tokens=5,
                messages=[
                    {"role": "system", "content":
                     "Classify if this message requires web search, real-time data, file access, "
                     "or code execution to answer properly. Reply ONLY 'tools' or 'chat'."},
                    {"role": "user", "content": text},
                ],
            )
            answer = resp.choices[0].message.content.strip().lower()
            # Strip <think> blocks from MiniMax
            import re
            answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip().lower()
            return "tool" in answer
        except Exception:
            return False  # default to MiniMax on classifier failure

    async def _respond_claude(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Run a task via Claude Code CLI with persistent session per topic."""
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        ck = _conv_key(chat_id, thread_id)

        # Security: daily cost cap
        today_cost = _get_today_cost()
        if today_cost >= DAILY_COST_CAP:
            logger.warning("Daily cost cap reached: $%.2f >= $%.2f", today_cost, DAILY_COST_CAP)
            await update.message.reply_text("Daily API cost limit reached.")
            return

        # #1: Queue — reject if already busy on this topic
        lock = _claude_locks[ck]
        if lock.locked():
            await update.message.reply_text(f"⏳ {display_name} is already working on a task. Please wait.")
            return

        async with lock:
            # #6: Mark topic as in Claude mode
            import time as _t
            _claude_active[ck] = _t.time()

            # #3: Session persistence + expiry recovery
            session_key = f"{persona_id}:{chat_id}:{thread_id or 0}"
            sessions = _load_bot_sessions()
            session_id = sessions.get(session_key)

            status_msg = await update.message.reply_text(f"🔍 {display_name} is working on it...")
            await update.effective_chat.send_action("typing")

            result = ""
            cost_usd = 0.0
            duration_ms = 0

            for attempt in range(2):  # retry once on session expiry
                try:
                    cc_args = [CLAUDE_BIN, "-p",
                               "--model", "claude-opus-4-6",
                               "--allowedTools", "Bash,Read,Glob,Grep,WebFetch,WebSearch",
                               "--output-format", "json"]
                    if session_id:
                        cc_args += ["--resume", session_id]
                    else:
                        cc_args += ["--system-prompt", system_prompt]
                    cc_env = os.environ.copy()
                    for _k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
                        cc_env.pop(_k, None)
                    proc = await asyncio.create_subprocess_exec(
                        *cc_args,
                        cwd=PROJECT_DIR,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=cc_env,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(input=text.encode()), timeout=180)
                    raw = stdout.decode().strip()

                    if not raw:
                        err = stderr.decode().strip()
                        # #3: Session expired — retry without resume
                        if session_id and ("session" in err.lower() or "resume" in err.lower() or proc.returncode != 0):
                            logger.warning("Claude session expired for %s, starting fresh", session_key)
                            sessions.pop(session_key, None)
                            _save_bot_sessions(sessions)
                            session_id = None
                            continue
                        result = "⚠️ No output. Check logs."
                        break

                    data = json.loads(raw)
                    result = data.get("result", "(no result)")

                    # Save session for follow-ups
                    sid = data.get("session_id")
                    if sid:
                        sessions[session_key] = sid
                        _save_bot_sessions(sessions)

                    # #4: Cost tracking
                    cost_usd = data.get("cost_usd", 0)
                    duration_ms = data.get("duration_ms", 0)
                    if cost_usd:
                        _log_claude_cost(persona_id, session_key, cost_usd, duration_ms)
                        logger.info("Claude cost: $%.4f (%dms) for %s", cost_usd, duration_ms, session_key)
                    break

                except asyncio.TimeoutError:
                    result = "⚠️ Task timed out (180s)"
                    break
                except json.JSONDecodeError as e:
                    # #3: Bad JSON might mean session error — retry fresh
                    if attempt == 0 and session_id:
                        logger.warning("Claude JSON error for %s, retrying fresh: %s", session_key, e)
                        sessions.pop(session_key, None)
                        _save_bot_sessions(sessions)
                        session_id = None
                        continue
                    result = f"⚠️ Failed to parse response"
                    break
                except Exception as e:
                    logger.error("Claude Code error: %s", e)
                    result = "⚠️ Something went wrong. Check logs."
                    break

            # Delete status message, send result
            try:
                await status_msg.delete()
            except Exception:
                pass

            # Store in conversation + memory
            mk = _mem_key(chat_id, thread_id)
            convs[ck].append({"role": "user", "content": text})
            convs[ck].append({"role": "assistant", "content": result})
            mem.store(mk, "user", text)
            mem.store(mk, "assistant", result)
            _user_id = update.effective_user.id if update.effective_user else 0
            log_message(persona_id, _user_id, "user", text, "text")
            log_message(persona_id, _user_id, "assistant", result, "text", model="claude-opus")

            if len(result) <= 4096:
                await update.message.reply_text(result)
            else:
                for i in range(0, len(result), 4096):
                    await update.message.reply_text(result[i : i + 4096])

    # ── Persona /menu command (layered inline keyboard) ─────────────────────

    # Category definitions: (key, emoji_label, [(cmd_name, description), ...])
    # Content commands are added dynamically based on persona config
    _PMENU_CATEGORIES = [
        ("chat", "💬 Chat", [("clear", "Clear conversation"), ("status", "Bot status")]),
        ("content", "📰 Content", []),  # populated dynamically
        ("subscribe", "🔔 Subscribe", [("subscribe", "Subscribe digests"), ("unsubscribe", "Unsubscribe")]),
        ("info", "ℹ️ Info", [("start", "About this bot")]),
    ]

    def _build_persona_content_cmds() -> list[tuple[str, str]]:
        """Build content command list based on persona config flags."""
        cmds = []
        if digest_enabled and news_module != "none":
            cmds.append(("news", "News digest"))
        if twitter_enabled:
            cmds.append(("xcurate", "X curation"))
            cmds.append(("tweets", "Twitter digest"))
        if reddit_enabled and reddit_subs:
            cmds.append(("reddit", "Reddit digest"))
        if yields_enabled:
            cmds.append(("yields", "Yield report"))
        return cmds

    def _pmenu_top_keyboard() -> InlineKeyboardMarkup:
        """Build top-level persona menu keyboard."""
        buttons = []
        row = []
        content_cmds = _build_persona_content_cmds()
        for key, label, _cmds in _PMENU_CATEGORIES:
            # Skip content category if no content commands available
            if key == "content" and not content_cmds:
                continue
            row.append(InlineKeyboardButton(label, callback_data=f"pmenu:{key}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        return InlineKeyboardMarkup(buttons)

    def _pmenu_sub_keyboard(category_key: str) -> InlineKeyboardMarkup:
        """Build sub-command keyboard for a persona menu category."""
        cmds = []
        for key, _label, cmd_list in _PMENU_CATEGORIES:
            if key == category_key:
                cmds = list(cmd_list)
                break
        # Dynamically populate content commands
        if category_key == "content":
            cmds = _build_persona_content_cmds()
        buttons = []
        row = []
        for cmd, desc in cmds:
            row.append(InlineKeyboardButton(
                f"/{cmd} — {desc}",
                callback_data=f"pmenu:cmd:{cmd}",
            ))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("← Back", callback_data="pmenu:back")])
        return InlineKeyboardMarkup(buttons)

    def _pmenu_category_label(key: str) -> str:
        for k, label, _cmds in _PMENU_CATEGORIES:
            if k == key:
                return label
        return key

    async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show layered inline command menu for persona bot."""
        chat_type = update.effective_chat.type
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, update.effective_chat.id, thread_id):
            return
        if not _check_private_user(update):
            return
        kb = _pmenu_top_keyboard()
        await update.message.reply_text(
            f"<b>📋 {display_name} — Menu</b>\nTap a category:",
            parse_mode="HTML",
            reply_markup=kb,
        )

    # Late-bound dispatch map: populated after all command functions are defined
    _cmd_dispatch: dict = {}

    async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle persona menu inline keyboard callbacks."""
        query = update.callback_query
        if not query or not query.data:
            return
        # Check user is allowed
        user_id = query.from_user.id if query.from_user else 0
        if not _is_allowed_user(user_id):
            await query.answer("Not authorized")
            return

        data = query.data  # pmenu:category | pmenu:back | pmenu:cmd:name

        if data == "pmenu:back":
            await query.answer()
            await query.edit_message_text(
                f"<b>📋 {display_name} — Menu</b>\nTap a category:",
                parse_mode="HTML",
                reply_markup=_pmenu_top_keyboard(),
            )
            return

        if data.startswith("pmenu:cmd:"):
            cmd = data.split(":", 2)[2]
            await query.answer(f"/{cmd}")
            await query.edit_message_text(f"▶️ /{cmd}")
            handler_fn = _cmd_dispatch.get(cmd)
            if handler_fn:
                await handler_fn(update, context)
            return

        # Category selected — show sub-commands
        category_key = data.split(":", 1)[1]
        label = _pmenu_category_label(category_key)
        await query.answer()
        await query.edit_message_text(
            f"<b>📋 {label}</b>\nTap a command:",
            parse_mode="HTML",
            reply_markup=_pmenu_sub_keyboard(category_key),
        )

    # ── Topic event handlers ──────────────────────────────────────────────────

    async def handle_topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg or not msg.forum_topic_created:
            return
        _cache_thread(update.effective_chat.id, msg.message_thread_id, msg.forum_topic_created.name)

    async def handle_topic_edited(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg or not msg.forum_topic_edited:
            return
        if msg.forum_topic_edited.name:
            _cache_thread(update.effective_chat.id, msg.message_thread_id, msg.forum_topic_edited.name)

    # ── Command handlers ──────────────────────────────────────────────────────

    async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/register — run this inside a forum topic thread to register it for this bot."""
        if not update.message:
            return
        thread_id = update.message.message_thread_id
        if not thread_id:
            await update.message.reply_text(_r("register_no_thread"))
            return
        # Use provided name or fall back to bot's first configured topic name
        name = " ".join(context.args) if context.args else (next(iter(topic_names), "") if topic_names else "")
        if not name:
            await update.message.reply_text("/register <topic name>")
            return
        _cache_thread(update.effective_chat.id, thread_id, name)
        await update.message.reply_text(_r("register_ok", name=name))

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, update.effective_chat.id, thread_id):
            return
        await update.message.reply_text(_r("start"))

    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        msg = update.effective_message
        thread_id = msg.message_thread_id if msg else None
        if chat_type != "private" and not _is_my_thread(chat_type, update.effective_chat.id, thread_id):
            return

        lines = [f"<b>🤖 {display_name} — Status</b>"]

        # Uptime
        import time as _time
        uptime_secs = int(_time.time() - _start_time)
        h, m = divmod(uptime_secs // 60, 60)
        lines.append(f"⏱ Uptime: {h}h {m}m")

        # Topics
        lines.append(f"📌 Topics: {', '.join(topic_names) if topic_names else 'none'}")

        # Memory DB
        try:
            n_mem = mem._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            lines.append(f"🧠 Messages stored: {n_mem}")
        except Exception as e:
            lines.append(f"🧠 Memory DB: ❌ {e}")

        # LLM API test (uses primary model from fallback chain)
        try:
            test_resp = await asyncio.to_thread(
                _llm_client.chat.completions.create,
                model=_llm_model,
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            lines.append(f"🔌 LLM ({_llm_model}): ✅ OK")
        except Exception as e:
            lines.append(f"🔌 LLM ({_llm_model}): ❌ {type(e).__name__}: {e}")

        # Digest sent today?
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for flag_path, label in [
            (Path(__file__).parent / f".digest_sent_x_{persona_id}", "X digest"),
            (Path(__file__).parent / f".digest_sent_reddit_{persona_id}", "Reddit digest"),
            (Path(__file__).parent / f".digest_sent_{persona_id}", "News digest"),
        ]:
            if flag_path.exists():
                sent_date = flag_path.read_text().strip()
                icon = "✅" if sent_date == today_str else "⚠️"
                lines.append(f"{icon} {label}: last sent {sent_date}")

        # Twitter cookie age (X bots)
        cookies_path = Path(__file__).parent / "twitter_cookies.json"
        if cookies_path.exists() and twitter_enabled:
            import os as _os
            age_secs = int(_time.time() - _os.path.getmtime(cookies_path))
            age_h = age_secs // 3600
            icon = "✅" if age_h < 13 else "⚠️"
            lines.append(f"{icon} Twitter cookies: {age_h}h old")

        await msg.reply_text("\n".join(lines), parse_mode="HTML")

    async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            return
        if not _check_private_user(update):
            return
        ck = _conv_key(chat_id, thread_id)
        convs[ck].clear()
        compressor.clear(ck)
        saved = mem.flush_staging(_mem_key(chat_id, thread_id))
        if saved:
            msg = _r("clear_saved", n=saved)
        else:
            msg = _r("clear_empty")
        await update.message.reply_text(msg)

    async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            return
        if not _check_private_user(update):
            return
        subs = _load_subs()
        if chat_id in subs:
            await update.message.reply_text(_r("subscribe_already"))
            return
        subs.add(chat_id)
        _save_subs(subs)
        await update.message.reply_text(_r("subscribe_ok"))

    async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            return
        subs = _load_subs()
        if chat_id not in subs:
            await update.message.reply_text(_r("unsubscribe_not"))
            return
        subs.discard(chat_id)
        _save_subs(subs)
        await update.message.reply_text(_r("unsubscribe_ok"))

    async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            return
        if not _check_private_user(update):
            return
        await update.message.reply_text(_r("news_loading"))
        try:
            if news_module == "crypto_news":
                messages = await generate_crypto_digest("")
            else:
                messages = await generate_full_digest("")
            plain_texts: list[str] = []
            for msg in messages:
                if isinstance(msg, dict):
                    text   = msg["text"]
                    pm     = msg.get("parse_mode")
                    markup = msg.get("reply_markup")
                    plain_texts.append(text)
                else:
                    text   = msg
                    pm     = "HTML"
                    markup = None
                    plain_texts.append(text)
                await context.bot.send_message(chat_id=chat_id, text=text,
                                               message_thread_id=thread_id,
                                               parse_mode=pm,
                                               reply_markup=markup)
        except Exception as e:
            logger.error("Digest error: %s", e)
            await update.message.reply_text("⚠️ Something went wrong. Check logs.")

    async def yields_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            return
        await update.message.reply_text(_r("yields_loading"))
        try:
            messages = await generate_yield_report()
            for msg in messages:
                await context.bot.send_message(chat_id=chat_id, text=msg,
                                               message_thread_id=thread_id)
        except Exception as e:
            logger.error("Yields error: %s", e)
            await update.message.reply_text("⚠️ Something went wrong. Check logs.")

    async def tweets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            return
        if not _check_private_user(update):
            return
        await update.message.reply_text(_r("tweets_loading"))
        try:
            accounts = list(context.args) if context.args else twitter_accounts
            messages = await generate_twitter_digest("", accounts)
            for msg in messages:
                await context.bot.send_message(chat_id=chat_id, text=msg,
                                               message_thread_id=thread_id)
        except Exception as e:
            logger.error("Tweets error: %s", e)
            await update.message.reply_text("⚠️ Something went wrong. Check logs.")

    async def xlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            return
        if not _check_private_user(update):
            return
        await update.message.reply_text("Fetching X list…")
        try:
            results = await generate_xlist_digest()
            for item in results:
                msg = item["message"]
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    text=msg["text"],
                    reply_markup=msg.get("reply_markup"),
                    parse_mode=msg.get("parse_mode", "HTML"),
                )
        except Exception as e:
            logger.error("xlist error: %s", e)
            await update.message.reply_text("⚠️ Something went wrong. Check logs.")

    # ── Message handlers ──────────────────────────────────────────────────────

    async def _process_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        """Core text processing — called after debounce or immediately for commands."""
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id if update.message else None
        ck = _conv_key(chat_id, thread_id)

        # Photo queue flush: if photos are queued, this text is the intent
        photos = _photo_queue.pop(ck, [])
        if photos:
            logger.info("PHOTO FLUSH: %d photos + intent=%.60r", len(photos), text)
            await _respond_with_photos(update, context, text, photos)
            return

        # "exit" breaks out of Claude mode back to MiniMax chat
        if text.lower().strip() in ("exit", "/exit", "done", "/done"):
            if _in_claude_mode(ck):
                _claude_active.pop(ck, None)
                _photo_queue.pop(ck, None)  # clear any stale photos
                await update.message.reply_text(f"Back to chat mode.")
                return

        # #6: If in active Claude mode, follow-ups go to Claude Code
        if _in_claude_mode(ck):
            logger.info("CLAUDE CODE (follow-up) — %.60r", text)
            await _respond_claude(update, context, text)
            return

        # #2: Smart task detection
        use_claude = await _needs_claude_smart(text)
        if use_claude:
            logger.info("CLAUDE CODE (smart detect) — %.60r", text)
            await _respond_claude(update, context, text)
        else:
            await _respond(update, context, text)

    async def _flush_text_buffer(ck: tuple) -> None:
        """Flush debounced text chunks and process as one message."""
        await asyncio.sleep(TEXT_MERGE_DELAY)
        chunks = _text_buffer.pop(ck, [])
        update = _text_buffer_updates.pop(ck, None)
        context = _text_buffer_contexts.pop(ck, None)
        _text_buffer_tasks.pop(ck, None)
        if not chunks or not update or not context:
            return
        merged = "\n".join(chunks)
        logger.info("TEXT MERGE: %d chunks → %d chars for ck=%s", len(chunks), len(merged), ck)
        await _process_text(update, context, merged)

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id
        text      = update.message.text
        logger.info("MSG chat=%s type=%s thread=%s text=%.40r", chat_id, chat_type, thread_id, text)
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            logger.info("SKIP — not my thread (my topics: %s)", topic_names)
            return

        # Security: user whitelist (private chats only)
        if not _check_private_user(update):
            logger.warning("BLOCKED — user %s not in ALLOWED_USERS", update.effective_user.id)
            return

        # Security: per-user rate limiting
        user_id = update.effective_user.id if update.effective_user else 0
        if not _check_rate_limit(user_id):
            now = _time_mod.time()
            last_notified = _rate_limit_notified.get(user_id, 0)
            if now - last_notified > _RATE_LIMIT_WINDOW:
                _rate_limit_notified[user_id] = now
                await update.message.reply_text("Rate limit reached. Please wait.")
            return

        # Security: input length limit
        if len(text) > INPUT_MAX_LENGTH:
            logger.warning("Input truncated: user=%s len=%d", user_id, len(text))
            text = text[:INPUT_MAX_LENGTH]

        ck = _conv_key(chat_id, thread_id)

        # Text chunk debouncing: buffer and merge within 0.6s window
        _text_buffer[ck].append(text)
        _text_buffer_updates[ck] = update   # keep latest update
        _text_buffer_contexts[ck] = context

        # Cancel existing flush task, schedule new one
        existing = _text_buffer_tasks.get(ck)
        if existing and not existing.done():
            existing.cancel()
        _text_buffer_tasks[ck] = asyncio.ensure_future(_flush_text_buffer(ck))

    async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not whisper_model:
            return
        voice = update.message.voice or update.message.audio
        if not voice:
            return
        chat_type = update.effective_chat.type
        thread_id = update.message.message_thread_id
        if chat_type != "private" and not _is_my_thread(chat_type, update.effective_chat.id, thread_id):
            return

        # Security: user whitelist (private chats only)
        if not _check_private_user(update):
            return

        # Security: per-user rate limiting
        voice_user_id = update.effective_user.id if update.effective_user else 0
        if not _check_rate_limit(voice_user_id):
            now = _time_mod.time()
            last_notified = _rate_limit_notified.get(voice_user_id, 0)
            if now - last_notified > _RATE_LIMIT_WINDOW:
                _rate_limit_notified[voice_user_id] = now
                await update.message.reply_text("Rate limit reached. Please wait.")
            return

        await update.effective_chat.send_action("typing")
        try:
            file = await context.bot.get_file(voice.file_id)
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp_path = tmp.name
                await file.download_to_drive(tmp_path)

            segments, info = whisper_model.transcribe(tmp_path, beam_size=5, language=whisper_lang)
            transcript = "".join(seg.text for seg in segments).strip()
            os.unlink(tmp_path)

            if not transcript:
                await update.message.reply_text(_r("voice_no_audio"))
                return

            # Security: sanitize voice transcript (injection via spoken text)
            transcript = sanitize_external_content(transcript)

            # Security: input length limit for voice transcripts
            if len(transcript) > INPUT_MAX_LENGTH:
                logger.warning("Voice transcript truncated: len=%d", len(transcript))
                transcript = transcript[:INPUT_MAX_LENGTH]

            logger.info("Voice (%s, %.1fs): %s", info.language, info.duration, transcript[:80])
            _voice_uid = update.effective_user.id if update.effective_user else 0
            log_message(persona_id, _voice_uid, "user", transcript, "voice")
            await update.message.reply_text(_r("voice_heard", transcript=transcript))
            await _respond(update, context, transcript)

        except Exception as e:
            logger.error("Voice error: %s", e)
            await update.message.reply_text(_r("voice_error", e=e))

    # ── Photo handler (accumulator + album dedup) ──────────────────────────────

    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.photo:
            return
        chat_type = update.effective_chat.type
        chat_id   = update.effective_chat.id
        thread_id = update.message.message_thread_id
        if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
            return
        if not _check_private_user(update):
            return
        user_id = update.effective_user.id if update.effective_user else 0
        if not _check_rate_limit(user_id):
            return

        ck = _conv_key(chat_id, thread_id)

        # Album dedup: skip if we already processed this media_group_id
        mg_id = update.message.media_group_id
        if mg_id:
            if mg_id in _media_group_seen:
                # Still add the photo to the queue (album has multiple photos)
                pass
            _media_group_seen[mg_id] = _time_mod.time()

        # Get largest photo size
        photo = update.message.photo[-1]
        caption = update.message.caption or ""
        if caption:
            caption = sanitize_external_content(caption)

        _photo_queue[ck].append({
            "file_id": photo.file_id,
            "file_unique_id": photo.file_unique_id,
            "caption": caption,
        })

        count = len(_photo_queue[ck])
        # For albums, only reply once (on last photo — after 0.8s debounce)
        if mg_id:
            # Cancel existing album notify task if any
            task_key = f"album_{ck}"
            existing = _text_buffer_tasks.get(task_key)
            if existing and not existing.done():
                existing.cancel()

            async def _notify_album():
                await asyncio.sleep(0.8)
                n = len(_photo_queue[ck])
                if n > 0:
                    await update.message.reply_text(
                        f"Got it ({n} photo{'s' if n > 1 else ''} queued). Send more or tell me what to do with them."
                    )
            _text_buffer_tasks[task_key] = asyncio.ensure_future(_notify_album())
        else:
            await update.message.reply_text(
                f"Got it ({count} photo{'s' if count > 1 else ''} queued). Send more or tell me what to do with them."
            )

        logger.info("PHOTO queued: chat=%s count=%d album=%s", chat_id, count, mg_id or "no")

    async def _respond_with_photos(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, photos: list[dict]) -> None:
        """Download queued photos and route to Claude Code with the user's text intent."""
        await update.effective_chat.send_action("typing")
        downloaded = []
        for p in photos:
            try:
                file = await context.bot.get_file(p["file_id"])
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    await file.download_to_drive(tmp.name)
                    downloaded.append(tmp.name)
            except Exception as e:
                logger.warning("Photo download failed (file_id=%s): %s", p["file_id"], e)

        if not downloaded:
            await update.message.reply_text("Failed to download photos. Please try again.")
            return

        # Build prompt with photo paths for Claude Code
        captions = [p["caption"] for p in photos if p["caption"]]
        caption_note = f"\nPhoto captions: {'; '.join(captions)}" if captions else ""
        prompt = f"[User sent {len(downloaded)} photo(s)]{caption_note}\nUser's instruction: {text}\n\nPhoto file paths:\n" + "\n".join(downloaded)

        log_message(persona_id, update.effective_user.id if update.effective_user else 0, "user", prompt, "photo")
        await _respond_claude(update, context, prompt)

        # Cleanup temp files
        for path in downloaded:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ── Media group cleanup (periodic, every 5 min) ──────────────────────────

    async def _cleanup_media_groups(context: ContextTypes.DEFAULT_TYPE) -> None:
        now = _time_mod.time()
        stale = [k for k, ts in _media_group_seen.items() if now - ts > 60]
        for k in stale:
            del _media_group_seen[k]

    # ── Startup flush ─────────────────────────────────────────────────────────

    async def on_startup(app: Application) -> None:
        keys = mem._conn.execute("SELECT DISTINCT chat_id FROM staging").fetchall()
        if keys:
            logger.info("Startup: flushing staging for %d key(s)", len(keys))
            for (k,) in keys:
                saved = mem.flush_staging(k)
                logger.info("Flushed %s → saved %d", k, saved)

        # Auto-register command menu (always, even on fresh deploy)
        from telegram import BotCommand
        cmds = [
            BotCommand("menu", "Command menu"),
            BotCommand("start", "開始"),
            BotCommand("clear", "清除對話"),
            BotCommand("news", "即刻出新聞/crypto 簡報"),
            BotCommand("subscribe", "訂閱每日 digest"),
            BotCommand("unsubscribe", "退訂"),
            BotCommand("restart_admin", "重啟 Admin Bot"),
        ]
        if yields_enabled:
            cmds.append(BotCommand("yields", "Stablecoin 收益報告"))
        await app.bot.set_my_commands(cmds)

    # ── Error handler ─────────────────────────────────────────────────────────

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling update:", exc_info=context.error)

    # ── Build app ─────────────────────────────────────────────────────────────

    app = Application.builder().token(token).post_init(on_startup).build()
    app.add_error_handler(error_handler)

    # ── Admin restart command (any persona bot can restart admin bot) ────────
    async def restart_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        admin_id = int(os.environ.get("ADMIN_USER_ID", "0"))
        if update.effective_user.id != admin_id:
            return
        import signal
        proc = await asyncio.create_subprocess_exec(
            "pgrep", "-f", "python admin_bot",
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        pids = stdout.decode().strip()
        if pids:
            for pid in pids.split("\n"):
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (ValueError, ProcessLookupError) as e:
                    logger.warning("kill pid failed: %s", e)
            await update.message.reply_text(f"🔄 Admin bot killed (PID {pids}). start_all.sh 會 5 秒內重啟。")
        else:
            await update.message.reply_text("⚠️ Admin bot 冇搵到進程。")


    app.add_handler(CommandHandler("restart_admin", restart_admin))
    app.add_handler(CommandHandler("register",    register))
    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("status",      status))
    app.add_handler(CommandHandler("clear",       clear))
    app.add_handler(CommandHandler("subscribe",   subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("news",        news_command))

    app.add_handler(CommandHandler("menu",        cmd_menu))
    app.add_handler(CallbackQueryHandler(handle_menu_callback, pattern=r"^pmenu:"))
    _cmd_dispatch.update({
        "start": start, "status": status, "clear": clear,
        "subscribe": subscribe, "unsubscribe": unsubscribe, "news": news_command,
    })

    # ── Proactive health-check job (every 30 min) ──────────────────────────────
    _api_ok = {"ok": True}  # track last known API state

    async def health_check_job(context) -> None:
        # Test LLM API (primary model from fallback chain)
        try:
            await asyncio.to_thread(
                _llm_client.chat.completions.create,
                model=_llm_model,
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            if not _api_ok["ok"]:
                # Recovered — notify
                _api_ok["ok"] = True
                for chat_id, thread_id in _sub_targets():
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id, message_thread_id=thread_id,
                            text=f"✅ <b>{display_name}</b> — MiniMax API recovered.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
        except Exception as e:
            if _api_ok["ok"]:
                # Just went down — notify
                _api_ok["ok"] = False
                for chat_id, thread_id in _sub_targets():
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id, message_thread_id=thread_id,
                            text=f"⚠️ <b>{display_name}</b> — MiniMax API unreachable\n<code>{type(e).__name__}: {e}</code>",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

    from datetime import time as dtime
    app.job_queue.run_repeating(health_check_job, interval=1800, first=60, name="health_check")
    if yields_enabled:
        app.add_handler(CommandHandler("yields",  yields_command))
    if twitter_enabled:
        app.add_handler(CommandHandler("tweets",  tweets_command))
        _cmd_dispatch["tweets"] = tweets_command
    app.add_handler(CommandHandler("xlist",   xlist_command))
    app.add_handler(CallbackQueryHandler(handle_digest_callback, pattern=r"^(dex|dcl):"))

    if twitter_enabled:
        # ── X curator: vote callbacks ──────────────────────────────────────

        async def handle_xvote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            query = update.callback_query
            if not query or not query.data:
                return
            action, key = query.data[:3], query.data[4:]
            vote = 1 if action == "xup" else -1

            # Retrieve metadata stored in message text
            msg_text = query.message.text or ""
            lines    = msg_text.split("\n")
            author   = lines[0] if lines else "?"
            summary  = lines[1] if len(lines) > 1 else ""
            url      = next((l for l in lines if l.startswith("🔗 ")), "").replace("🔗 ", "")

            record_vote(key, url, author, summary, vote)

            label = "👍 Noted" if vote == 1 else "👎 Noted"
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            new_markup = InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="noop")]])
            try:
                await query.edit_message_reply_markup(reply_markup=new_markup)
            except Exception:
                pass
            await query.answer("Got it!")

        async def handle_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.callback_query:
                await update.callback_query.answer()

        async def xcurate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            chat_type = update.effective_chat.type
            chat_id   = update.effective_chat.id
            thread_id = update.message.message_thread_id if update.message else None
            if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
                return
            if not _check_private_user(update):
                return
            await update.message.reply_text("Running X curation… this may take a few minutes.")
            try:
                cards = await generate_daily_digest("", lang=xcurate_lang, list_ids=xcurate_lists)
                if not cards:
                    await update.message.reply_text("No picks today.")
                    return
                today = datetime.now(timezone.utc).strftime("%d %b %Y")
                await context.bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id,
                    text=f"🗞 <b>X Daily — {today}</b>  ({len(cards)} picks)",
                    parse_mode="HTML",
                )
                for card in cards:
                    await context.bot.send_message(
                        chat_id=chat_id, message_thread_id=thread_id,
                        text=card["text"], reply_markup=card["markup"], parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(0.3)
            except Exception as e:
                logger.error("xcurate error: %s", e)
                await update.message.reply_text("⚠️ Something went wrong. Check logs.")

        app.add_handler(CommandHandler("xcurate", xcurate_command))
        _cmd_dispatch["xcurate"] = xcurate_command
        app.add_handler(CallbackQueryHandler(handle_xvote,  pattern=r"^x(up|dn):"))
        app.add_handler(CallbackQueryHandler(handle_noop,   pattern=r"^noop$"))

        # ── X curator: daily digest at 04:00 UTC (12:00 HKT) ──────────────

        async def send_daily_xdigest(context) -> bool:
            """Returns True if digest was successfully sent."""
            targets = _sub_targets()
            if xcurate_target:
                t = (xcurate_target["chat_id"], xcurate_target.get("thread_id"))
                if t not in targets:
                    targets.append(t)
            if not targets:
                logger.info("No targets for daily X digest")
                return False
            logger.info("Running daily X digest for %d target(s)…", len(targets))
            try:
                cards = await generate_daily_digest("", lang=xcurate_lang, list_ids=xcurate_lists)
            except Exception as e:
                logger.error("Daily X digest failed: %s", e)
                await _notify_fail(context.bot, "X Daily Digest", e)
                return False
            if not cards:
                logger.warning("Daily X digest: no cards generated")
                await _notify_fail(context.bot, "X Daily Digest", Exception("No picks generated — twikit may be down or no finance content found"))
                return False
            today = datetime.now(timezone.utc).strftime("%d %b %Y")
            separator = "🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧"
            header = f"🗞 <b>X Daily — {today}</b>  ({len(cards)} picks)"
            for (chat_id, thread_id) in targets:
                sent = 0
                failed = 0
                total = len(cards) + 2
                for msg_kwargs in [
                    {"text": separator},
                    {"text": header, "parse_mode": "HTML"},
                ] + [
                    {"text": card["text"], "reply_markup": card["markup"], "parse_mode": "HTML", "disable_web_page_preview": True}
                    for card in cards
                ]:
                    ok = await _send_with_retry(context.bot, chat_id, thread_id, **msg_kwargs)
                    if ok:
                        sent += 1
                    else:
                        failed += 1
                    await asyncio.sleep(0.5)
                if failed == 0:
                    logger.info("✅ X digest: %d/%d sent OK → chat=%s thread=%s", sent, total, chat_id, thread_id)
                else:
                    logger.warning("⚠️ X digest: %d/%d sent, %d FAILED → chat=%s thread=%s", sent, total, failed, chat_id, thread_id)
            return True

        from datetime import time as dtime
        from telegram.ext import JobQueue
        from x_curator import prefetch_tweets

        if not CRON_MANAGED:
            async def prefetch_job(context):
                try:
                    await prefetch_tweets()
                except Exception as e:
                    logger.error("Prefetch failed: %s", e)

            # Staggered times (HKT = UTC+8, so 11:xx HKT = 03:xx UTC)
            # prefetch: 11:32 HKT (shared, only 1st bot fetches)
            # sends: twitter=11:42, xcn=11:44, xai=11:46, xniche=11:48
            x_send_minutes = {"twitter": 42, "xcn": 44, "xai": 46, "xniche": 48}
            send_min = x_send_minutes.get(persona_id, 42)

            if xcurate_lang != "lists":
                app.job_queue.run_daily(
                    prefetch_job,
                    time=dtime(hour=3, minute=32, tzinfo=timezone.utc),
                    name="prefetch_x_tweets",
                )

            _x_digest_flag = Path(__file__).parent / f".digest_sent_x_{persona_id}"

            async def send_daily_xdigest_wrapped(context):
                success = await send_daily_xdigest(context)
                if success:
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    _x_digest_flag.write_text(today_str)

            app.job_queue.run_daily(
                send_daily_xdigest_wrapped,
                time=dtime(hour=3, minute=send_min, tzinfo=timezone.utc),
                name="daily_x_digest",
            )

            # Retry: check every 30 min if today's digest hasn't been sent yet
            async def _x_retry(context):
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if _x_digest_flag.exists() and _x_digest_flag.read_text().strip() == today_str:
                    return  # already sent today
                now_utc = datetime.now(timezone.utc)
                sched_minutes = 3 * 60 + send_min
                now_minutes = now_utc.hour * 60 + now_utc.minute
                if now_minutes > sched_minutes and now_utc.hour < 16:
                    logger.info("X digest retry: not sent today, retrying now")
                    await send_daily_xdigest_wrapped(context)

            app.job_queue.run_repeating(_x_retry, interval=1800, first=90, name="x_digest_retry")
        else:
            logger.info("X digest scheduler disabled (CRON_MANAGED=True)")
    # ── Reddit digest ───────────────────────────────────────────────────
    if reddit_enabled and reddit_subs:
        async def reddit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            chat_type = update.effective_chat.type
            chat_id   = update.effective_chat.id
            thread_id = update.message.message_thread_id if update.message else None
            if chat_type != "private" and not _is_my_thread(chat_type, chat_id, thread_id):
                return
            if not _check_private_user(update):
                return
            await update.message.reply_text("Fetching Reddit top posts…")
            try:
                cards = await asyncio.to_thread(generate_reddit_digest, reddit_subs, api_key="")
                if not cards:
                    await update.message.reply_text("No posts found.")
                    return
                today = datetime.now(timezone.utc).strftime("%d %b %Y")
                await context.bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id,
                    text=f"📰 <b>Reddit Digest — {today}</b>  ({len(cards)} posts)",
                    parse_mode="HTML",
                )
                for card in cards:
                    await context.bot.send_message(
                        chat_id=chat_id, message_thread_id=thread_id,
                        text=card["text"], parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_markup=card.get("reply_markup"),
                    )
                    await asyncio.sleep(0.3)
            except Exception as e:
                logger.error("reddit command error: %s", e)
                await update.message.reply_text("⚠️ Something went wrong. Check logs.")

        app.add_handler(CommandHandler("reddit", reddit_command))
        _cmd_dispatch.update({"reddit": reddit_command})

        async def send_daily_reddit(context) -> bool:
            """Returns True if digest was successfully sent."""
            target = reddit_target
            if not target:
                return False
            chat_id = target["chat_id"]
            thread_id = target.get("thread_id")
            logger.info("Running daily Reddit digest…")
            try:
                cards = await asyncio.to_thread(generate_reddit_digest, reddit_subs, api_key="")
            except Exception as e:
                logger.error("Daily Reddit digest failed: %s", e)
                await _notify_fail(context.bot, "Reddit Digest", e)
                return False
            if not cards:
                logger.warning("Daily Reddit digest: no posts")
                return False
            today = datetime.now(timezone.utc).strftime("%d %b %Y")
            separator = "🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧"
            header = f"📰 <b>Reddit Digest — {today}</b>  ({len(cards)} posts)"
            sent = 0
            failed = 0
            total = len(cards) + 2
            for msg_kwargs in [
                {"text": separator},
                {"text": header, "parse_mode": "HTML"},
            ] + [
                {"text": card["text"], "parse_mode": "HTML", "disable_web_page_preview": True,
                 "reply_markup": card.get("reply_markup")}
                for card in cards
            ]:
                ok = await _send_with_retry(context.bot, chat_id, thread_id, **msg_kwargs)
                if ok:
                    sent += 1
                else:
                    failed += 1
                await asyncio.sleep(0.5)
            if failed == 0:
                logger.info("✅ Reddit digest: %d/%d sent OK", sent, total)
            else:
                logger.warning("⚠️ Reddit digest: %d/%d sent, %d FAILED", sent, total, failed)
            return True

        if not CRON_MANAGED:
            from datetime import time as dtime
            _reddit_digest_flag = Path(__file__).parent / f".digest_sent_reddit_{persona_id}"

            async def send_daily_reddit_wrapped(context):
                success = await send_daily_reddit(context)
                if success:
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    _reddit_digest_flag.write_text(today_str)

            app.job_queue.run_daily(
                send_daily_reddit_wrapped,
                time=dtime(hour=3, minute=56, tzinfo=timezone.utc),
                name="daily_reddit_digest",
            )

            # Retry: check every 30 min if today's Reddit digest hasn't been sent
            async def _reddit_retry(context):
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if _reddit_digest_flag.exists() and _reddit_digest_flag.read_text().strip() == today_str:
                    return  # already sent today
                now_utc = datetime.now(timezone.utc)
                if now_utc.hour * 60 + now_utc.minute > 3 * 60 + 56 and now_utc.hour < 16:
                    logger.info("Reddit digest retry: not sent today, retrying now")
                    await send_daily_reddit_wrapped(context)

            app.job_queue.run_repeating(_reddit_retry, interval=1800, first=120, name="reddit_digest_retry")
        else:
            logger.info("Reddit digest scheduler disabled (CRON_MANAGED=True)")

    # ── News digest (standard / crypto_news) ─────────────────────────────
    if digest_enabled and news_module != "none":
        _news_digest_flag = Path(__file__).parent / f".digest_sent_news_{persona_id}"

        async def send_daily_news(context) -> bool:
            """Returns True if news digest was successfully sent."""
            targets = _sub_targets()
            if not targets:
                logger.info("No targets for daily news digest")
                return False
            logger.info("Running daily news digest (%s) for %d target(s)…", news_module, len(targets))
            try:
                if news_module == "crypto_news":
                    messages = await generate_crypto_digest("")
                else:
                    messages = await generate_full_digest("")
            except Exception as e:
                logger.error("Daily news digest failed: %s", e)
                await _notify_fail(context.bot, f"News Digest ({news_module})", e)
                return False
            if not messages:
                logger.warning("Daily news digest: no content generated")
                return False

            today = datetime.now(timezone.utc).strftime("%d %b %Y")
            separator = "🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧"
            header = f"📰 <b>Daily News — {today}</b>"
            for chat_id, thread_id in targets:
                sent = 0
                failed = 0
                total = len(messages) + 2
                for msg_kwargs in [
                    {"text": separator},
                    {"text": header, "parse_mode": "HTML"},
                ] + [
                    {"text": m, "parse_mode": "HTML"} if isinstance(m, str)
                    else {"text": m["text"], "parse_mode": m.get("parse_mode", "HTML")}
                    for m in messages
                ]:
                    ok = await _send_with_retry(context.bot, chat_id, thread_id, **msg_kwargs)
                    if ok:
                        sent += 1
                    else:
                        failed += 1
                    await asyncio.sleep(0.5)
                if failed == 0:
                    logger.info("✅ News digest: %d/%d sent OK → chat=%s thread=%s", sent, total, chat_id, thread_id)
                else:
                    logger.warning("⚠️ News digest: %d/%d sent, %d FAILED → chat=%s thread=%s", sent, total, failed, chat_id, thread_id)
            return True

        if not CRON_MANAGED:
            async def send_daily_news_wrapped(context):
                success = await send_daily_news(context)
                if success:
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    _news_digest_flag.write_text(today_str)

            # Schedule: 12:00 HKT = 04:00 UTC
            from datetime import time as dtime
            app.job_queue.run_daily(
                send_daily_news_wrapped,
                time=dtime(hour=4, minute=0, tzinfo=timezone.utc),
                name="daily_news_digest",
            )

            # Retry: check every 30 min if today's news digest hasn't been sent
            async def _news_retry(context):
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if _news_digest_flag.exists() and _news_digest_flag.read_text().strip() == today_str:
                    return  # already sent today
                now_utc = datetime.now(timezone.utc)
                if now_utc.hour * 60 + now_utc.minute > 4 * 60 and now_utc.hour < 16:
                    logger.info("News digest retry: not sent today, retrying now")
                    await send_daily_news_wrapped(context)

            app.job_queue.run_repeating(_news_retry, interval=1800, first=150, name="news_digest_retry")
            logger.info("News digest scheduled daily at 12:00 HKT (news_module=%s)", news_module)
        else:
            logger.info("News digest scheduler disabled (CRON_MANAGED=True)")

    app.add_handler(MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CREATED, handle_topic_created))
    app.add_handler(MessageHandler(filters.StatusUpdate.FORUM_TOPIC_EDITED,  handle_topic_edited))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))  # before TEXT (captions)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    if voice_enabled:
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    # Periodic cleanup: purge stale media_group_seen entries
    app.job_queue.run_repeating(_cleanup_media_groups, interval=300, first=300)

    logger.info("Starting %s bot (topics: %s)…", display_name, topic_names)
    app.run_polling()
