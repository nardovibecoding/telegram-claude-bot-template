# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Shared LLM client with automatic fallback chain and parallel cross-check.

Fallback order (chat_completion):
  1. MiniMax M2.5-highspeed  (primary — fast, cheap)
  2. Cerebras llama-3.3-70b  (fastest inference)
  3. Groq llama-3.3-70b      (free tier fallback)
  4. DeepSeek deepseek-chat   (free tier fallback)
  5. Gemini 2.5 Flash         (free tier fallback)

Parallel cross-check (chat_completion_multi / cross_check):
  All 4 providers in parallel: Cerebras, Gemini, Kimi, Qwen3

Usage:
    from llm_client import chat_completion, chat_completion_multi, cross_check

    # Single model with fallback chain
    text = chat_completion(
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=1000,
    )

    # Parallel cross-check across all 6 models
    result = await cross_check(
        messages=[{"role": "user", "content": "What is quantum computing?"}],
        max_tokens=1000,
    )
"""

import asyncio
import logging
import os
import re
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

logger = logging.getLogger(__name__)

# ── Provider registry ────────────────────────────────────────────────────────

PROVIDERS = {
    "cerebras": {
        "name": "Cerebras-Qwen3-235b",
        "api_key_env": "CEREBRAS_API_KEY",
        "base_url": "https://api.cerebras.ai/v1",
        "model": "qwen-3-235b-a22b-instruct-2507",
    },
    "gemini": {
        "name": "Gemini-2.5-Flash",
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",
    },
    "kimi": {
        "name": "Kimi-for-Coding",
        "api_key_env": "KIMI_API_KEY",
        "base_url": "https://api.kimi.com/coding/v1",
        "model": "kimi-for-coding",
        "headers": {"User-Agent": "claude-code/1.0"},
    },
    "qwen": {
        "name": "Qwen3-32b",
        "api_key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "qwen/qwen3-32b",
        "no_think": True,  # append /no_think to system prompt to disable CoT
    },
}

# Fallback chain order for chat_completion (single-model path)
_FALLBACK_CHAIN = ["kimi", "qwen", "cerebras", "gemini"]

# Errors that trigger immediate fallback (no retry on same model)
_FATAL_PATTERNS = [
    "insufficient_balance",
    "account_deactivated",
    "invalid_api_key",
    "authentication",
]


def _is_fatal(error_str: str) -> bool:
    """Check if error should skip retries and fall through immediately."""
    lower = error_str.lower()
    return any(p in lower for p in _FATAL_PATTERNS)


def _strip_think(text: str) -> str:
    """Remove <think>...</think>, /think patterns, and plain-text CoT from LLM output.

    Handles:
    - Standard <think>...</think> blocks
    - Qwen-style /think patterns
    - Plain-text reasoning leaked as body text (no tags)
    If stripping leaves empty (some models put everything in think), extract think content.
    """
    # Strip standard <think>...</think> blocks
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip Qwen-style think blocks: <think>...\n</think> or content before /think
    stripped = re.sub(r"<\|think\|>.*?<\|/think\|>", "", stripped, flags=re.DOTALL).strip()
    # Also handle plain /think closing without opening tag (partial streaming artifacts)
    stripped = re.sub(r"^.*?/think\s*\n?", "", stripped, flags=re.DOTALL).strip() if "/think" in stripped else stripped

    if stripped:
        # Strip plain-text CoT that leaked without tags
        stripped = _strip_plaintext_cot(stripped)
        if stripped:
            return stripped

    # If stripping left empty, extract from think blocks
    match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"<\|think\|>(.*?)<\|/think\|>", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()

    return text.strip()


# Plain-text CoT patterns — reasoning that leaks without <think> tags
_PLAINTEXT_COT_PATTERNS = [
    r"^The user wants me to\b",
    r"^The user is asking\b",
    r"^I need to\b.*?(?:analyze|check|verify|think|consider|understand|respond)",
    r"^I should\b.*?(?:analyze|check|verify|think|consider|respond|structure|keep)",
    r"^Let me\b.*?(?:analyze|check|think|draft|refine|structure|break|consider|verify|make sure)",
    r"^Looking at\b.*?(?:the input|the content|this|the request|the question)",
    r"^(?:First|Now),? (?:I need|I should|let me|I will|I'll)\b",
    r"^(?:OK|Okay|Alright),? (?:so |let me |I need |I should )",
    r"^Wait,? (?:I need|let me|I should)\b",
    r"^(?:My|The) (?:draft|analysis|response|answer) (?:is|should|would|could)\b",
    r"^I (?:can see|notice|observe) that\b",
    r"^(?:Actually|Hmm),? (?:looking|thinking|let me)\b",
    r"^This (?:appears|seems|looks) to be\b.*?(?:asking|about|requesting)",
]
_PLAINTEXT_COT_RE = [re.compile(p, re.IGNORECASE) for p in _PLAINTEXT_COT_PATTERNS]


def _strip_plaintext_cot(text: str) -> str:
    """Strip plain-text CoT reasoning that leaked without think tags.

    Strategy: scan line by line from the top. If leading lines match CoT
    patterns, strip them. Stop at first non-CoT line. This preserves the
    actual answer which typically follows the reasoning block.
    """
    lines = text.split("\n")
    first_real_line = 0

    for i, line in enumerate(lines):
        stripped_line = line.strip()
        if not stripped_line:
            continue
        if any(rx.search(stripped_line) for rx in _PLAINTEXT_COT_RE):
            first_real_line = i + 1
            continue
        # Stop at first non-CoT, non-empty line
        break

    if first_real_line > 0:
        result = "\n".join(lines[first_real_line:]).strip()
        if result:
            logger.info("Stripped %d lines of plain-text CoT", first_real_line)
            return result

    return text


def _get_client(provider_key: str, timeout: int = 30):
    """Create OpenAI client for a provider. Returns (client, model_cfg) or (None, None)."""
    from openai import OpenAI

    cfg = PROVIDERS.get(provider_key)
    if not cfg:
        logger.warning("Unknown provider: %s", provider_key)
        return None, None

    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        logger.warning("Skipping %s: no API key (%s)", cfg["name"], cfg["api_key_env"])
        return None, None

    extra_headers = cfg.get("headers") or {}
    kwargs = dict(
        api_key=api_key,
        base_url=cfg["base_url"],
        timeout=timeout,
    )
    if extra_headers:
        import httpx
        _h = extra_headers.copy()

        def _inject(request, _h=_h):
            request.headers.update(_h)

        kwargs["http_client"] = httpx.Client(
            event_hooks={"request": [_inject]},
        )
    client = OpenAI(**kwargs)
    return client, cfg


# ── Single model with fallback chain ─────────────────────────────────────────

def chat_completion(
    messages: list[dict],
    max_tokens: int = 1500,
    timeout: int = 45,
    system: str | None = None,
) -> str:
    """
    Call LLM with automatic fallback chain.

    Chain: Qwen -> Kimi -> Cerebras -> Gemini

    Parameters
    ----------
    messages : list of {"role": ..., "content": ...}
    max_tokens : max response tokens
    timeout : per-request timeout in seconds
    system : optional system message (prepended to messages if provided)

    Returns
    -------
    str : model response text (think tags stripped), or error string starting with warning sign
    """
    if system:
        messages = [{"role": "system", "content": system}] + messages

    last_error = "all models failed"

    for provider_key in _FALLBACK_CHAIN:
        client, cfg = _get_client(provider_key, timeout)
        if client is None:
            continue

        model_name = cfg["name"]

        # Inject /no_think for models that support it (Qwen3)
        call_messages = messages
        if cfg.get("no_think") and call_messages and call_messages[0]["role"] == "system":
            call_messages = list(call_messages)
            call_messages[0] = {**call_messages[0], "content": call_messages[0]["content"] + " /no_think"}
        elif cfg.get("no_think"):
            call_messages = [{"role": "system", "content": "/no_think"}] + list(call_messages)

        for attempt in range(1, 3):  # 2 attempts per model
            try:
                logger.info("LLM call: %s (attempt %d/2)", model_name, attempt)
                resp = client.chat.completions.create(
                    model=cfg["model"],
                    messages=call_messages,
                    max_tokens=max_tokens,
                )
                msg = resp.choices[0].message
                text = msg.content or ""
                # Reasoning models (e.g. kimi-for-coding) put thinking in
                # reasoning_content and the answer in content
                if not text:
                    text = getattr(msg, "reasoning_content", "") or ""
                text = _strip_think(text)
                logger.info("LLM success: %s (%d chars)", model_name, len(text))
                return text

            except Exception as e:
                err_str = str(e)
                last_error = err_str
                logger.warning("LLM %s attempt %d failed: %s", model_name, attempt, err_str[:200])

                if _is_fatal(err_str):
                    logger.error("Fatal error on %s, falling back: %s", model_name, err_str[:200])
                    break  # skip retries, go to next model

                if attempt < 2:
                    time.sleep(attempt * 3)

        logger.warning("Falling back from %s to next model", model_name)

    logger.error("All LLM models failed. Last error: %s", last_error[:200])
    return f"\u26a0\ufe0f All models failed: {last_error[:100]}"


def get_primary_client(timeout: int = 30):
    """Return an OpenAI-compatible client for the first available provider (Kimi first).

    Used by MemoryManager and ConversationCompressor which need a client object.
    Returns (client, model_name) or (None, None) if all providers unavailable.
    """
    for key in _FALLBACK_CHAIN:
        client, cfg = _get_client(key, timeout)
        if client is not None:
            logger.info("Primary client: %s", cfg["name"])
            return client, cfg["model"]
    logger.error("get_primary_client: no providers available")
    return None, None


async def chat_completion_async(
    messages: list[dict],
    max_tokens: int = 1500,
    timeout: int = 45,
    system: str | None = None,
) -> str:
    """Async version -- runs chat_completion in executor to avoid blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: chat_completion(messages, max_tokens, timeout, system)
    )


# ── Parallel multi-model ─────────────────────────────────────────────────────

def _call_single_model(
    provider_key: str,
    messages: list[dict],
    max_tokens: int,
    timeout: int,
) -> tuple[str, str | None]:
    """Call a single model synchronously. Returns (response_text, error_or_None)."""
    client, cfg = _get_client(provider_key, timeout)
    if client is None:
        return "", f"No API key or unknown provider: {provider_key}"

    # Inject /no_think for models that support it (Qwen3)
    call_messages = messages
    if cfg.get("no_think") and call_messages and call_messages[0]["role"] == "system":
        call_messages = list(call_messages)
        call_messages[0] = {**call_messages[0], "content": call_messages[0]["content"] + " /no_think"}
    elif cfg.get("no_think"):
        call_messages = [{"role": "system", "content": "/no_think"}] + list(call_messages)

    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=call_messages,
            max_tokens=max_tokens,
        )
        msg = resp.choices[0].message
        text = msg.content or ""
        # Reasoning models (e.g. kimi-for-coding) put answer in reasoning_content
        if not text:
            text = getattr(msg, "reasoning_content", "") or ""
        text = _strip_think(text)
        logger.info("Multi-LLM success: %s (%d chars)", cfg["name"], len(text))
        return text, None
    except Exception as e:
        err_str = str(e)
        logger.warning("Multi-LLM %s failed: %s", cfg["name"], err_str[:200])
        return "", err_str[:200]


async def chat_completion_multi(
    messages: list[dict],
    max_tokens: int = 1500,
    timeout: int = 30,
    system: str | None = None,
    models: list[str] | None = None,
) -> dict:
    """
    Run multiple models in parallel on the same prompt.

    Parameters
    ----------
    messages : list of {"role": ..., "content": ...}
    max_tokens : max response tokens
    timeout : per-model timeout in seconds (default 30)
    system : optional system message
    models : subset of provider keys to use (default: all 6)

    Returns
    -------
    dict with keys for each model name and an "errors" dict:
        {
                                                "gemini": "response...",
            "kimi": "response...",
            "qwen": "response...",
            "errors": {"model_name": "error message"}
        }
    """
    if system:
        messages = [{"role": "system", "content": system}] + messages

    target_models = models or list(PROVIDERS.keys())
    loop = asyncio.get_event_loop()

    # Launch all models in parallel using executor (OpenAI SDK is sync)
    tasks = {}
    for key in target_models:
        if key not in PROVIDERS:
            logger.warning("Unknown model key in multi-call: %s", key)
            continue
        tasks[key] = loop.run_in_executor(
            None,
            _call_single_model,
            key,
            messages,
            max_tokens,
            timeout,
        )

    # Gather results
    results_raw = await asyncio.gather(*tasks.values(), return_exceptions=True)

    result = {}
    errors = {}

    for key, raw in zip(tasks.keys(), results_raw):
        if isinstance(raw, Exception):
            errors[key] = str(raw)[:200]
            result[key] = ""
            logger.warning("Multi-LLM %s exception: %s", key, str(raw)[:200])
        else:
            text, err = raw
            result[key] = text
            if err:
                errors[key] = err

    result["errors"] = errors
    successful = len(target_models) - len(errors)
    logger.info("Multi-LLM complete: %d/%d succeeded", successful, len(target_models))
    return result


# ── Cross-check with judge synthesis ─────────────────────────────────────────

_DEFAULT_JUDGE_PROMPT = """You received answers from {n} different AI models to the same question. Synthesize their responses:
1. Where do they AGREE? (high confidence findings)
2. Where do they DISAGREE? (highlight the tensions)
3. Rate consensus 1-10 (10 = unanimous)
4. What unique insight did only 1-2 models catch?

Format your consensus score as: CONSENSUS: X/10

Here are the model responses:

{responses}"""


async def cross_check(
    messages: list[dict],
    max_tokens: int = 1500,
    timeout: int = 30,
    system: str | None = None,
    models: list[str] | None = None,
    judge_model: str = "kimi",
    judge_prompt: str | None = None,
) -> dict:
    """
    Higher-level cross-check: run all models in parallel, then judge/synthesize.

    Parameters
    ----------
    messages : list of {"role": ..., "content": ...}
    max_tokens : max response tokens per model
    timeout : per-model timeout in seconds
    system : optional system message
    models : subset of provider keys (default: all 6)
    judge_model : provider key for synthesis (default: minimax)
    judge_prompt : custom judge prompt (use {n} and {responses} placeholders)

    Returns
    -------
    dict:
        {
            "responses": {model: text, ...},
            "synthesis": "judge's synthesis...",
            "consensus_score": 0-10,
            "disagreements": ["..."]
        }
    """
    # Step 1: Get all model responses in parallel
    multi_result = await chat_completion_multi(
        messages=messages,
        max_tokens=max_tokens,
        timeout=timeout,
        system=system,
        models=models,
    )

    errors = multi_result.pop("errors", {})
    responses = {k: v for k, v in multi_result.items() if v}

    if not responses:
        return {
            "responses": {},
            "synthesis": "All models failed. No synthesis possible.",
            "consensus_score": 0,
            "disagreements": [],
        }

    # Step 2: Build judge prompt
    prompt_template = judge_prompt or _DEFAULT_JUDGE_PROMPT
    response_block = "\n\n".join(
        f"### {PROVIDERS.get(k, {}).get('name', k)}:\n{v}"
        for k, v in responses.items()
    )
    filled_prompt = prompt_template.format(n=len(responses), responses=response_block)

    # Step 3: Call judge model
    loop = asyncio.get_event_loop()
    synthesis_text, synth_err = await loop.run_in_executor(
        None,
        _call_single_model,
        judge_model,
        [{"role": "user", "content": filled_prompt}],
        max_tokens * 2,  # judge needs more room
        60,  # longer timeout for synthesis
    )

    if synth_err:
        logger.warning("Judge model %s failed: %s, trying fallback", judge_model, synth_err)
        # Try next in fallback chain
        for fallback_key in _FALLBACK_CHAIN:
            if fallback_key != judge_model:
                synthesis_text, synth_err = await loop.run_in_executor(
                    None,
                    _call_single_model,
                    fallback_key,
                    [{"role": "user", "content": filled_prompt}],
                    max_tokens * 2,
                    60,
                )
                if not synth_err:
                    break

    if synth_err:
        synthesis_text = "Judge synthesis failed. Raw responses available."

    # Step 4: Extract consensus score
    consensus_score = 0
    score_match = re.search(r"CONSENSUS:\s*(\d+)/10", synthesis_text)
    if score_match:
        consensus_score = int(score_match.group(1))

    # Step 5: Extract disagreements
    disagreements = []
    disagree_section = re.search(
        r"(?:DISAGREE|disagree|tensions?).*?(?:\n[-*]\s*.+)+",
        synthesis_text,
        re.IGNORECASE | re.DOTALL,
    )
    if disagree_section:
        points = re.findall(r"[-*]\s*(.+)", disagree_section.group())
        disagreements = [p.strip() for p in points if p.strip()]

    return {
        "responses": responses,
        "errors": errors,
        "synthesis": synthesis_text,
        "consensus_score": consensus_score,
        "disagreements": disagreements,
    }


# ── Gemini video analysis ─────────────────────────────────────────────────────

def gemini_video_analysis(video_path_or_url: str, prompt: str | None = None) -> str:
    """Analyze a video file or URL using Gemini 2.5 Flash native video API.

    Accepts a local file path or any URL supported by yt-dlp (X/Twitter, YouTube, etc.).
    Returns a text description/analysis of the video content.
    """
    import hashlib
    import mimetypes
    import subprocess
    import time
    import requests as _requests

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "⚠️ GEMINI_API_KEY not set"

    if prompt is None:
        prompt = (
            "Watch this video carefully. Describe:\n"
            "1. What product or feature is being demonstrated\n"
            "2. Key UX interactions and flows\n"
            "3. Technical stack if visible\n"
            "4. Whether this is clonable and suggested approach\n"
            "Be specific and concise."
        )

    # Step 1: If URL, download with yt-dlp
    video_path = video_path_or_url
    tmp_file = None
    if video_path_or_url.startswith("http"):
        url_hash = hashlib.md5(video_path_or_url.encode()).hexdigest()[:8]
        tmp_file = f"/tmp/watch_video_{url_hash}.mp4"
        try:
            subprocess.run(
                ["yt-dlp", "-o", f"/tmp/watch_{url_hash}.%(ext)s",
                 "--merge-output-format", "mp4",
                 "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]",
                 "--no-playlist", video_path_or_url],
                check=True, capture_output=True, timeout=120,
            )
            # yt-dlp may produce .mp4 or .mkv — find whichever was created
            import glob as _glob
            matches = _glob.glob(f"/tmp/watch_{url_hash}.*")
            if not matches:
                return "⚠️ yt-dlp: download succeeded but output file not found"
            video_path = matches[0]
            tmp_file = video_path
        except Exception as e:
            return f"⚠️ yt-dlp download failed: {e}"

    if not os.path.exists(video_path):
        return f"⚠️ Video file not found: {video_path}"

    mime_type = mimetypes.guess_type(video_path)[0] or "video/mp4"
    file_size = os.path.getsize(video_path)

    # Step 2: Upload via Gemini Files API
    upload_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    headers_init = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    try:
        r = _requests.post(upload_url, headers=headers_init,
                           json={"file": {"display_name": os.path.basename(video_path)}},
                           timeout=30)
        r.raise_for_status()
        resumable_uri = r.headers.get("X-Goog-Upload-URL")
        if not resumable_uri:
            return "⚠️ Gemini upload: no resumable URI returned"

        # Upload file bytes
        with open(video_path, "rb") as f:
            video_bytes = f.read()
        r2 = _requests.post(resumable_uri, headers={
            "Content-Length": str(file_size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        }, data=video_bytes, timeout=300)
        r2.raise_for_status()
        file_uri = r2.json().get("file", {}).get("uri", "")
        if not file_uri:
            return "⚠️ Gemini upload: no file URI in response"

        # Step 3: Wait for file to be ACTIVE
        status_url = f"https://generativelanguage.googleapis.com/v1beta/{file_uri.split('/')[-2]}/{file_uri.split('/')[-1]}?key={api_key}"
        for _ in range(20):
            sr = _requests.get(status_url, timeout=10)
            state = sr.json().get("state", "")
            if state == "ACTIVE":
                break
            time.sleep(3)
        else:
            return "⚠️ Gemini file processing timed out"

        # Step 4: Generate content
        gen_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [
            {"file_data": {"mime_type": mime_type, "file_uri": file_uri}},
            {"text": prompt},
        ]}]}
        gr = _requests.post(gen_url, json=payload, timeout=120)
        gr.raise_for_status()
        candidates = gr.json().get("candidates", [])
        if not candidates:
            return "⚠️ Gemini returned no candidates"
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip()

    except _requests.RequestException as e:
        return f"⚠️ Gemini API error: {e}"
