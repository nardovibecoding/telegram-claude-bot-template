# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Voice message handler — transcribe via Groq Whisper, route to Claude bridge."""
import asyncio
import logging
import os
import tempfile

from groq import Groq
from telegram import Update
from telegram.ext import ContextTypes

from .domains import _detect_domain
from .helpers import admin_only
from sanitizer import sanitize_external_content

log = logging.getLogger("admin")

_groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))


@admin_only
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe voice message via Groq Whisper, then pass to Claude bridge."""
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id

    domain = _detect_domain(chat_id, thread_id)
    if domain is None:
        return

    await update.effective_chat.send_action("typing")

    ogg_path = None
    try:
        file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            ogg_path = tmp.name
        await file.download_to_drive(ogg_path)

        # Groq accepts ogg directly — no ffmpeg needed
        loop = asyncio.get_running_loop()

        def _transcribe():
            with open(ogg_path, "rb") as af:
                result = _groq_client.audio.transcriptions.create(
                    file=("audio.ogg", af.read()),
                    model="whisper-large-v3-turbo",
                    language="zh",
                    response_format="text",
                )
            return result.strip() if isinstance(result, str) else result.text.strip()

        transcript = await asyncio.wait_for(
            loop.run_in_executor(None, _transcribe), timeout=30)
        os.unlink(ogg_path)
        ogg_path = None

        if not transcript:
            await update.message.reply_text("(couldn't transcribe)")
            return

        # Security: sanitize voice transcript (injection via spoken text)
        transcript = sanitize_external_content(transcript)

        await update.message.reply_text(f"🎤 {transcript}")

        # Route transcript through claude_bridge via context
        from .bridge import claude_bridge
        context.user_data["_voice_transcript"] = transcript
        await claude_bridge(update, context)

    except Exception as e:
        log.error("Voice error: %s", e, exc_info=True)
        await update.message.reply_text(f"⚠️ Voice processing failed: {type(e).__name__}: {e}")
        if ogg_path:
            try:
                os.unlink(ogg_path)
            except Exception:
                pass
