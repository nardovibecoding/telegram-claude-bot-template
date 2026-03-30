# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Shared LLM client with automatic fallback chain and parallel cross-check.

Fallback order (chat_completion):
  1. MiniMax M2.5-highspeed  (primary — fast, cheap)
  2. Cerebras llama-3.3-70b  (fastest inference)
  3. Groq llama-3.3-70b      (free tier fallback)
  4. DeepSeek deepseek-chat   (free tier fallback)
  5. Gemini 2.0 Flash         (free tier fallback)

Parallel cross-check (chat_completion_multi / cross_check):
  All 6 providers in parallel: MiniMax, Cerebras, DeepSeek, Gemini, Kimi K2, Qwen3

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
    "minimax": {
        "name": "MiniMax-M2.7",
        "api_key_env": "MINIMAX_API_KEY",
        "base_url": "https://api.minimaxi.com/v1",
        "model": "MiniMax-M2.7-highspeed",
    },
    "cerebras": {
        "name": "Cerebras-Llama-3.3-70b",
        "api_key_env": "CEREBRAS_API_KEY",
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama-3.3-70b",
    },
    "deepseek": {
        "name": "DeepSeek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "gemini": {
        "name": "Gemini-2.0-Flash",
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.0-flash",
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
    },
}

# Fallback chain order for chat_completion (single-model path)
_FALLBACK_CHAIN = ["kimi", "minimax", "cerebras", "deepseek", "gemini"]

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
    """Remove <think>...</think> and /think patterns from LLM output.

    Handles:
    - Standard <think>...</think> blocks
    - Qwen-style /think patterns
    If stripping leaves empty (some models put everything in think), extract think content.
    """
    # Strip standard <think>...</think> blocks
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip Qwen-style think blocks: <think>...\n</think> or content before /think
    stripped = re.sub(r"<\|think\|>.*?<\|/think\|>", "", stripped, flags=re.DOTALL).strip()
    # Also handle plain /think closing without opening tag (partial streaming artifacts)
    stripped = re.sub(r"^.*?/think\s*\n?", "", stripped, flags=re.DOTALL).strip() if "/think" in stripped else stripped

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

    default_headers: dict[str, str] = cfg.get("headers") or {}
    client = OpenAI(
        api_key=api_key,
        base_url=cfg["base_url"],
        timeout=timeout,
        default_headers=default_headers,
    )
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

    Chain: MiniMax -> Cerebras -> Kimi K2 -> DeepSeek -> Gemini

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

        for attempt in range(1, 3):  # 2 attempts per model
            try:
                logger.info("LLM call: %s (attempt %d/2)", model_name, attempt)
                resp = client.chat.completions.create(
                    model=cfg["model"],
                    messages=messages,
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

    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=messages,
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content or ""
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
            "minimax": "response...",
            "cerebras": "response...",
            "deepseek": "response...",
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
