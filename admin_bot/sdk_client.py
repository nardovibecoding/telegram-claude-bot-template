# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Persistent Claude Code client via Agent SDK.

Keeps ONE subprocess alive across messages. Cold start ~6s once,
then 2-3s per message. Auto-reconnects on crash.
"""
import asyncio
import logging
import os

# Unset CLAUDECODE to prevent "nested session" error
for _k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
    os.environ.pop(_k, None)

from claude_agent_sdk import (
    ClaudeSDKClient, ClaudeAgentOptions,
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
)

from .config import PROJECT_DIR, SYSTEM_PROMPTS
from .chat import MODEL_MAP
from .domains import _load_sessions

log = logging.getLogger("admin")

# Singleton clients per domain — each domain gets its own persistent session
_clients: dict[str, ClaudeSDKClient] = {}
_client_locks: dict[str, asyncio.Lock] = {}
_creation_locks: dict[str, asyncio.Lock] = {}


def _get_lock(domain: str) -> asyncio.Lock:
    if domain not in _client_locks:
        _client_locks[domain] = asyncio.Lock()
    return _client_locks[domain]


def _get_creation_lock(key: str) -> asyncio.Lock:
    if key not in _creation_locks:
        _creation_locks[key] = asyncio.Lock()
    return _creation_locks[key]


async def _get_or_create_client(domain: str, model: str, cwd: str = None, session_key: str = None) -> ClaudeSDKClient:
    """Get existing client or create new one for this domain.

    If reconnecting after a crash, resumes the saved session_id so
    Claude retains conversation context.
    Uses a per-key lock to prevent duplicate client creation.
    """
    key = f"{domain}:{model}"

    # Fast path — no lock needed if client exists and is alive
    if key in _clients:
        client = _clients[key]
        # Check if still alive
        try:
            if client._transport and client._transport.is_ready():
                return client
        except Exception:
            pass
        # Dead — clean up
        try:
            await client.disconnect()
        except Exception:
            pass
        del _clients[key]

    # Serialize creation per domain:model to prevent duplicate clients
    async with _get_creation_lock(key):
        # Double-check after acquiring lock — another coroutine may have created it
        if key in _clients:
            client = _clients[key]
            try:
                if client._transport and client._transport.is_ready():
                    return client
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
            del _clients[key]

        # Create new client
        model_id = MODEL_MAP.get(model, "claude-sonnet-4-6")
        sys_prompt = SYSTEM_PROMPTS.get(domain, "")

        # Try to resume saved session for conversation continuity
        resume_id = None
        if session_key:
            sessions = _load_sessions()
            resume_id = sessions.get(session_key)
            if resume_id:
                log.info("SDK client resuming session %s for %s", resume_id[:12], key)

        # Prefer globally installed CLI (npm) over bundled (may be newer)
        import shutil
        global_cli = shutil.which("claude")

        options = ClaudeAgentOptions(
            model=model_id,
            permission_mode="bypassPermissions",
            system_prompt=sys_prompt if sys_prompt else None,
            resume=resume_id,
            cwd=cwd or PROJECT_DIR,
            cli_path=global_cli,  # use latest npm-installed CLI if available
            setting_sources=["user", "project"],  # load skills from ~/.claude/skills/
            allowed_tools=["Skill", "Read", "Write", "Edit", "Bash", "Glob", "Grep",
                            "WebFetch", "WebSearch", "Agent", "Notebook"],
        )

        from .helpers import timed_await
        client = ClaudeSDKClient(options)
        await timed_await(client.connect(), f"SDK connect ({domain}:{model})")
        _clients[key] = client
        log.info("SDK client created: domain=%s model=%s", domain, model)
        return client


async def sdk_query(
    prompt: str,
    domain: str,
    model: str = "sonnet",
    cwd: str = None,
    on_text: callable = None,
    on_tool: callable = None,
) -> str:
    """Send a message via persistent SDK client. Returns result text.

    Args:
        prompt: User message
        domain: Domain for system prompt selection
        model: haiku/sonnet/opus
        cwd: Working directory
        on_text: Callback for streaming text blocks (async)
        on_tool: Callback for tool use blocks (async)

    Returns:
        Final result text
    """
    lock = _get_lock(f"{domain}:{model}")

    async with lock:
        try:
            client = await _get_or_create_client(domain, model, cwd)
        except Exception as e:
            log.error("SDK client creation failed: %s", e)
            raise

        result_text = ""
        text_chunks = []

        try:
            from .helpers import timed_await
            await timed_await(client.query(prompt), f"SDK query start ({domain})")

            async for msg in client.receive_messages():
                if isinstance(msg, ResultMessage):
                    result_text = msg.result or ""
                    break
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text:
                            text_chunks.append(block.text)
                            if on_text:
                                await on_text(block.text)
                        elif isinstance(block, ToolUseBlock):
                            if on_tool:
                                await on_tool(block.name, block.input)

            if not result_text and text_chunks:
                result_text = text_chunks[-1]

            return result_text

        except Exception as e:
            log.error("SDK query failed: %s — reconnecting", e)
            # Kill dead client (with timeout to prevent deadlock)
            key = f"{domain}:{model}"
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5)
            except Exception:
                pass
            _clients.pop(key, None)
            raise


async def sdk_disconnect_all():
    """Disconnect all persistent clients. Call on bot shutdown."""
    for key, client in list(_clients.items()):
        try:
            await client.disconnect()
            log.info("SDK client disconnected: %s", key)
        except Exception:
            pass
    _clients.clear()
