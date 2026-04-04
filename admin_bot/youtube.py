# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""YouTube video summarizer — /yt command.

Extracts transcript via youtube-transcript-api, falls back to
yt-dlp audio download + Groq Whisper, then summarizes via MiniMax.
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile

from groq import Groq
from openai import OpenAI
from telegram import Update
from telegram.ext import ContextTypes

from .helpers import admin_only

log = logging.getLogger("admin")

_groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))

MAX_TRANSCRIPT_CHARS = 15000


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _get_transcript(video_id: str) -> str | None:
    """Try to get transcript via youtube-transcript-api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)
        text = " ".join(snippet.text for snippet in transcript)
        return text.strip() if text.strip() else None
    except Exception as e:
        log.info("youtube-transcript-api failed for %s: %s", video_id, e)
        return None


def _download_and_transcribe(url: str) -> str | None:
    """Fall back: download audio via yt-dlp, transcribe via Groq Whisper."""
    audio_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
            audio_path = tmp.name

        # Download audio only, max 25MB (Groq limit)
        cmd = [
            "yt-dlp", "-f", "ba[ext=m4a]/ba",
            "--max-filesize", "25M",
            "-o", audio_path,
            "--no-playlist",
            "--quiet",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log.warning("yt-dlp failed: %s", result.stderr[:200])
            return None

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            log.warning("yt-dlp produced empty file")
            return None

        # Transcribe via Groq Whisper
        with open(audio_path, "rb") as af:
            transcript = _groq_client.audio.transcriptions.create(
                file=("audio.m4a", af.read()),
                model="whisper-large-v3-turbo",
                response_format="text",
            )
        text = transcript.strip() if isinstance(transcript, str) else transcript.text.strip()
        return text if text else None

    except subprocess.TimeoutExpired:
        log.warning("yt-dlp timed out")
        return None
    except Exception as e:
        log.error("Download+transcribe failed: %s", e, exc_info=True)
        return None
    finally:
        if audio_path:
            try:
                os.unlink(audio_path)
            except OSError:
                pass


def _summarize(transcript: str) -> str:
    """Summarize transcript via MiniMax M2.5."""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        return "Error: MINIMAX_API_KEY not set"

    # Truncate if needed
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n\n[... truncated]"

    client = OpenAI(api_key=api_key, base_url="https://api.minimaxi.com/v1", timeout=60)
    resp = client.chat.completions.create(
        model="MiniMax-M2.5-highspeed",
        max_tokens=3000,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a concise video summarizer. Given a video transcript, "
                    "produce a clear summary with: key points, main arguments, and conclusions. "
                    "Reply in the same language as the transcript. "
                    "Use bullet points for clarity. Keep it concise but comprehensive."
                ),
            },
            {
                "role": "user",
                "content": f"Summarize this video transcript:\n\n{transcript}",
            },
        ],
    )
    from utils import strip_think
    return strip_think(resp.choices[0].message.content.strip())


@admin_only
async def cmd_yt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /yt <youtube_url> — summarize a YouTube video."""
    if not context.args:
        await update.message.reply_text("Usage: /yt <youtube_url>")
        return

    url = context.args[0]
    video_id = _extract_video_id(url)
    if not video_id:
        await update.message.reply_text("Could not parse YouTube URL.")
        return

    status_msg = await update.message.reply_text("Fetching transcript...")
    loop = asyncio.get_event_loop()

    # Step 1: Try youtube-transcript-api
    transcript = await loop.run_in_executor(None, _get_transcript, video_id)
    method = "transcript API"

    # Step 2: Fall back to yt-dlp + Groq Whisper
    if not transcript:
        await status_msg.edit_text("No transcript found. Downloading audio for transcription...")
        transcript = await loop.run_in_executor(None, _download_and_transcribe, f"https://youtu.be/{video_id}")
        method = "audio transcription (Groq Whisper)"

    if not transcript:
        await status_msg.edit_text("Failed to get transcript. Video may be too long, private, or unavailable.")
        return

    # Step 3: Summarize via MiniMax
    await status_msg.edit_text(f"Got transcript via {method} ({len(transcript)} chars). Summarizing...")

    try:
        summary = await loop.run_in_executor(None, _summarize, transcript)
    except Exception as e:
        log.error("Summarization failed: %s", e, exc_info=True)
        await status_msg.edit_text(f"Summarization failed: {type(e).__name__}: {e}")
        return

    # Send result
    header = f"YT Summary (via {method}):\n\n"
    result = header + summary

    # Delete status message and send final result
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Split long messages
    from .helpers import _send_msg
    await _send_msg(
        update.get_bot(),
        update.effective_chat.id,
        result,
        thread_id=update.message.message_thread_id,
    )
