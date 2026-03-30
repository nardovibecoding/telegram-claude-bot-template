#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Global voice-to-text daemon for macOS.
Hold RIGHT OPTION (⌥) key to record, release to transcribe and auto-type.

Run: python3 ~/voice_daemon.py

macOS permissions needed (one-time prompts):
  - Accessibility (for global hotkey + keystroke output)
  - Microphone
"""

import os
import sys
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"  # fix OpenMP conflict with faster-whisper

# Single-instance lock
_LOCK_FILE = "/tmp/voice_daemon.pid"
# Kill ALL existing voice_daemon processes (not just the one in the PID file)
_my_pid = os.getpid()
try:
    import subprocess as _sp
    _result = _sp.run(["pgrep", "-f", "voice_daemon.py"], capture_output=True, text=True)
    for _pid_str in _result.stdout.strip().split("\n"):
        try:
            _pid = int(_pid_str)
            if _pid != _my_pid:
                os.kill(_pid, 15)
                print(f"Killed old daemon (PID {_pid})")
        except (ValueError, ProcessLookupError):
            pass
    if _result.stdout.strip():
        import time as _time_mod
        _time_mod.sleep(1)
except Exception:
    pass
open(_LOCK_FILE, "w").write(str(os.getpid()))

import queue
import threading
import subprocess
import tempfile
import time
import sys

import numpy as np
import sounddevice as sd
import scipy.io.wavfile
import speech_recognition as sr
from pynput import keyboard

SAMPLERATE = 16000
PREFERRED_MIC = "External Microphone"  # use external mic / headset
TRIGGER_KEYS = {keyboard.Key.alt_r}

# ── STT engine: "groq", "apple", "whisper", or "google" ──
STT_ENGINE = "groq"
STT_TOOL = os.path.expanduser("~/stt")  # compiled Swift tool (fallback)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

if STT_ENGINE == "groq":
    from groq import Groq
    _groq_client = Groq(api_key=GROQ_API_KEY, timeout=30.0)
    print("Groq Whisper ready.", flush=True)
elif STT_ENGINE == "whisper":
    from faster_whisper import WhisperModel
    print("Loading Whisper small model...", flush=True)
    whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    print("Whisper ready.", flush=True)
elif STT_ENGINE == "google":
    recognizer = sr.Recognizer()
else:
    print("Apple Speech ready.", flush=True)


def get_input_device():
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if PREFERRED_MIC.lower() in d["name"].lower() and d["max_input_channels"] > 0:
            print(f"  Using mic: {d['name']} (device {i})", flush=True)
            return i
    print("  USB mic not found, using default mic", flush=True)
    return None  # sounddevice default

# Cache device at startup — avoid 100-200ms scan on every recording
_CACHED_DEVICE = get_input_device()


def _is_tts_playing():
    """Check if say or afplay is currently running."""
    r1 = subprocess.run(["pgrep", "-x", "say"], capture_output=True)
    r2 = subprocess.run(["pgrep", "-x", "afplay"], capture_output=True)
    return r1.returncode == 0 or r2.returncode == 0

# ── shared state ───────────────────────────────────────────────────────────────
recording = False
audio_chunks = []
stream = None
lock = threading.Lock()
transcribe_queue = queue.Queue()  # keyboard thread → main thread
_target_app = None  # frontmost app at recording start, restored before typing

# ── VAD state ─────────────────────────────────────────────────────────────────
_vad_enabled = True
_vad_triggered = False
_vad_chunks = []
_vad_silence_count = 0
_vad_speech_count = 0
VAD_ENERGY_THRESHOLD = 0.10  # RMS threshold — well above background noise (~0.05)
VAD_SILENCE_TIMEOUT_FRAMES = 50  # ~1.5s at 30ms/frame
VAD_MIN_SPEECH_FRAMES = 8  # ~240ms minimum continuous speech to trigger


def _get_frontmost_app() -> str:
    """Return name of the currently focused app."""
    result = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def _activate_app(name: str):
    """Bring the named app to front."""
    subprocess.run(
        ["osascript", "-e", f'tell application "{name}" to activate'],
        capture_output=True
    )

# Clean up stale indicator file from previous crash
try:
    os.unlink("/tmp/recording_active")
except FileNotFoundError:
    pass

print("Ready. Hold RIGHT OPTION (⌥) to record, or just speak (VAD auto-detect).\n", flush=True)
open("/tmp/vad_mode", "w").close()  # VAD mode on by default

# ── Persistent audio stream + VAD ─────────────────────────────────────────────
_persistent_stream = None


def _persistent_callback(indata, frames, time_info, status):
    """Single callback for both manual recording and VAD."""
    global _vad_triggered, _vad_silence_count, _vad_speech_count, _target_app

    # Always feed manual recording if active
    with lock:
        if recording:
            audio_chunks.append(indata.copy())
            return  # Manual mode takes priority

    # VAD mode — no subprocess calls in audio callback!
    if not _vad_enabled or _pressed:
        _vad_speech_count = 0
        _vad_silence_count = 0
        return

    rms = float(np.sqrt(np.mean(indata.flatten() ** 2)))

    if rms > VAD_ENERGY_THRESHOLD:
        _vad_speech_count += 1
        _vad_silence_count = 0
    else:
        _vad_silence_count += 1

    if _vad_triggered:
        # Currently recording via VAD
        _vad_chunks.append(indata.copy())
        if _vad_silence_count >= VAD_SILENCE_TIMEOUT_FRAMES:
            # Silence timeout — stop VAD recording
            _vad_triggered = False
            _vad_speech_count = 0
            chunks_copy = list(_vad_chunks)
            _vad_chunks.clear()
            if len(chunks_copy) > 10:
                try:
                    os.unlink("/tmp/recording_active")
                except FileNotFoundError:
                    pass
                open("/tmp/transcribing_active", "w").close()
                transcribe_queue.put(chunks_copy)
                print(f"  🎙️ VAD: Done, transcribing...", flush=True)
            else:
                try:
                    os.unlink("/tmp/recording_active")
                except FileNotFoundError:
                    pass
    else:
        # Not recording — check if speech started
        if _vad_speech_count >= VAD_MIN_SPEECH_FRAMES:
            _vad_triggered = True
            _vad_silence_count = 0
            _vad_chunks.clear()
            _vad_chunks.append(indata.copy())
            open("/tmp/recording_active", "w").close()
            _target_app = _get_frontmost_app()
            if _is_tts_playing():
                interrupt_tts()
            print("  🎙️ VAD: Speech detected...", flush=True)


def _start_persistent_stream():
    """Start one stream that runs forever."""
    global _persistent_stream
    try:
        _persistent_stream = sd.InputStream(
            samplerate=SAMPLERATE, channels=1, dtype="float32",
            callback=_persistent_callback, device=_CACHED_DEVICE,
            blocksize=480,  # 30ms frames
        )
        _persistent_stream.start()
        print("Persistent audio stream started.", flush=True)
    except Exception as e:
        print(f"Failed to start persistent stream: {e}", flush=True)


def start_recording():
    global recording, audio_chunks, stream, _target_app, _was_muted, _transcribe_cancelled
    global _vad_triggered

    _transcribe_cancelled = False
    # Cancel any VAD recording in progress
    _vad_triggered = False
    _vad_chunks.clear()

    _target_app = _get_frontmost_app()
    with lock:
        if recording:
            return
        recording = True
        audio_chunks = []
    _was_muted = _was_muted or os.path.exists(MUTE_FLAG)
    open("/tmp/recording_active", "w").close()
    subprocess.run(["osascript", "-e", "set volume input volume 100"], capture_output=True)

    # No need to start a new stream — persistent stream is already running
    # Just flip recording=True and the callback will collect audio

    if _is_tts_playing():
        interrupt_tts()

    print("  Recording...", flush=True)


def cancel_recording():
    """Cancel recording — discard audio, skip transcription."""
    global recording, _transcribe_cancelled
    with lock:
        if not recording:
            return
        recording = False

    try:
        os.unlink("/tmp/recording_active")
    except FileNotFoundError:
        pass

    _transcribe_cancelled = True
    print("  Recording cancelled ✖", flush=True)


MIN_RECORDING_CHUNKS = 27  # ~0.8s at 30ms/chunk — skip very short recordings

def stop_recording():
    """Stop recording and hand audio off for transcription."""
    global recording
    if _transcribe_cancelled:
        return
    with lock:
        if not recording:
            return
        recording = False
        chunks = list(audio_chunks)

    try:
        os.unlink("/tmp/recording_active")
    except FileNotFoundError:
        pass

    # Skip recordings shorter than 0.5s — saves transcription time
    if len(chunks) < MIN_RECORDING_CHUNKS:
        duration_ms = len(chunks) * 30
        print(f"  Too short ({duration_ms}ms), skipped.", flush=True)
        return

    open("/tmp/transcribing_active", "w").close()  # yellow while transcribing
    transcribe_queue.put(chunks)


def transcribe(chunks):
    global _transcribe_cancelled
    try:
        _transcribe_inner(chunks)
    finally:
        # Always clear transcribing indicator, no matter how we exit
        try:
            os.unlink("/tmp/transcribing_active")
        except FileNotFoundError:
            pass


def _transcribe_inner(chunks):
    global _transcribe_cancelled, recognizer
    if not chunks:
        print("  (no audio captured)", flush=True)
        return

    audio = np.concatenate(chunks, axis=0).flatten()
    duration = len(audio) / SAMPLERATE
    if duration < 0.3:
        print("  (too short, ignored)", flush=True)
        return

    peak = float(np.abs(audio).max())
    print(f"  Transcribing {duration:.1f}s (peak: {peak:.4f})...", flush=True)
    if peak < 0.001:
        print("  (mic silent — check Microphone permission in System Settings > Privacy)", flush=True)
        return

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        scipy.io.wavfile.write(tmp_path, SAMPLERATE, (audio * 32767).astype(np.int16))

        if STT_ENGINE == "groq":
            try:
                with open(tmp_path, "rb") as af:
                    result = _groq_client.audio.transcriptions.create(
                        file=("audio.wav", af.read()),
                        model="whisper-large-v3-turbo",
                        language="zh",
                        response_format="text",
                    )
                text = result.strip() if isinstance(result, str) else result.text.strip()
                text = text or None
            except Exception as e:
                print(f"  (Groq failed: {e}, falling back to Apple Speech)", flush=True)
                result = subprocess.run(
                    [STT_TOOL, tmp_path, "zh-Hans"],
                    capture_output=True, text=True, timeout=15
                )
                text = result.stdout.strip() or None
            if text is None:
                raise sr.UnknownValueError()
        elif STT_ENGINE == "apple":
            result = subprocess.run(
                [STT_TOOL, tmp_path, "zh-Hans"],
                capture_output=True, text=True, timeout=15
            )
            text = result.stdout.strip() or None
            if text is None:
                raise sr.UnknownValueError()
        elif STT_ENGINE == "whisper":
            segments, _ = whisper_model.transcribe(tmp_path, language="zh")
            text = "".join(s.text for s in segments).strip() or None
            if text is None:
                raise sr.UnknownValueError()
        else:
            with sr.AudioFile(tmp_path) as source:
                audio_data = recognizer.record(source)
            text = None
            for lang in ("zh-HK", "en-US"):
                for attempt in range(3):
                    try:
                        text = recognizer.recognize_google(audio_data, language=lang)
                        try:
                            os.unlink("/tmp/transcribe_error")
                        except FileNotFoundError:
                            pass
                        break
                    except sr.UnknownValueError:
                        break
                    except Exception as e:
                        if attempt < 2:
                            print(f"  (retry {attempt+1}/2 [{lang}]: {e})", flush=True)
                            open("/tmp/transcribe_error", "w").close()
                            time.sleep(0.5)
                        else:
                            raise
                if text:
                    break
            if text is None:
                raise sr.UnknownValueError()
    except sr.UnknownValueError:
        print("  (nothing heard)", flush=True)
        return
    except Exception as e:
        print(f"  (error: {e})", flush=True)
        return
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    text = text.strip()
    if not text:
        print("  (nothing heard)", flush=True)
        return

    # Short noise already filtered by 0.8s minimum recording duration
    # No word-count filter — even 1-2 word commands should go through

    # Filter Whisper hallucinations (common on silence/low audio)
    # Filter Whisper hallucinations — only match EXACT known phrases, not single words
    _HALLUCINATION_PHRASES = (
        "请不吝点赞", "订阅转发", "明镜与点点", "欢迎收看订阅",
        "thank you for watching", "please subscribe", "like and subscribe",
    )
    if any(h in text.lower() for h in _HALLUCINATION_PHRASES):
        print(f"  (Whisper hallucination filtered: {text[:50]})", flush=True)
        return

    if _transcribe_cancelled:
        print("  (cancelled, skipping typing)", flush=True)
        return
    print(f"  Typing: {text}", flush=True)
    time.sleep(0.15)
    type_text(text)
    if _was_muted:
        open(MUTE_FLAG, "w").close()
        print("  🔇 Restored mute after typing", flush=True)


def type_text(text):
    """Restore focus to original app, paste via clipboard, then press Enter."""
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
    if _target_app:
        _activate_app(_target_app)
        time.sleep(0.15)  # let the app come to front
    script = '''tell application "System Events"
        keystroke "v" using command down
        delay 0.3
        keystroke return
    end tell'''
    subprocess.run(["osascript", "-e", script], check=False)


_pressed = False
_transcribe_cancelled = False
_shift_held = False
_last_interrupt_time = 0.0
_last_release_time = 0.0
RECORDING_COOLDOWN = 0.5  # seconds between recordings
MUTE_FLAG = "/tmp/tts_muted"
_was_muted = False




def interrupt_tts():
    subprocess.run(["pkill", "-x", "say"], check=False)
    subprocess.run(["pkill", "-x", "afplay"], check=False)
    try:
        old_pid = int(open("/tmp/speak_hook_bg.pid").read())
        os.kill(old_pid, 9)
        os.unlink("/tmp/speak_hook_bg.pid")
    except Exception:
        pass
    print("  TTS interrupted ✋", flush=True)


def reset_state():
    """Force-reset all key state — call if things get stuck."""
    global _pressed, _shift_held, _last_interrupt_time, recording, _vad_triggered
    _pressed = False
    _shift_held = False
    _last_interrupt_time = 0.0
    _vad_triggered = False
    _vad_chunks.clear()
    with lock:
        recording = False
    try:
        os.unlink("/tmp/recording_active")
    except FileNotFoundError:
        pass
    print("  State reset ↺", flush=True)


def on_press(key):
    global _pressed, _shift_held, _last_interrupt_time, _last_release_time
    try:
        if key in (keyboard.Key.shift, keyboard.Key.shift_r):
            _shift_held = True
            if recording and key == keyboard.Key.shift:
                cancel_recording()
            elif key == keyboard.Key.shift_r and not recording:
                # Toggle VAD mode
                global _vad_enabled
                _vad_enabled = not _vad_enabled
                if _vad_enabled:
                    open("/tmp/vad_mode", "w").close()
                else:
                    try:
                        os.unlink("/tmp/vad_mode")
                    except FileNotFoundError:
                        pass
                mode = "🟢 VAD自动" if _vad_enabled else "⌥ 手动按键"
                print(f"  切换模式: {mode}", flush=True)
        elif key == keyboard.Key.cmd_r:
            if os.path.exists(MUTE_FLAG):
                os.unlink(MUTE_FLAG)
                print("  🔊 Unmuted", flush=True)
            else:
                open(MUTE_FLAG, "w").close()
                interrupt_tts()  # stop any ongoing TTS immediately
                print("  🔇 Muted + TTS stopped", flush=True)
        elif key in (keyboard.Key.cmd_l, keyboard.Key.alt):
            now = time.time()
            if now - _last_interrupt_time > 1.0:
                _last_interrupt_time = now
                interrupt_tts()
        elif key in TRIGGER_KEYS:
            now = time.time()
            if not _pressed and (now - _last_release_time) >= RECORDING_COOLDOWN:
                _pressed = True
                threading.Thread(target=start_recording, daemon=True).start()
    except Exception as e:
        print(f"  (on_press error: {e})", flush=True)


def on_release(key):
    global _pressed, _shift_held, _last_release_time
    try:
        if key in (keyboard.Key.shift, keyboard.Key.shift_r):
            _shift_held = False
        elif key in TRIGGER_KEYS:
            _pressed = False
            _last_release_time = time.time()
            threading.Thread(target=stop_recording, daemon=True).start()
    except Exception as e:
        print(f"  (on_release error: {e})", flush=True)


def _alt_r_physically_held():
    """Check if Right Option key is actually physically held via Quartz."""
    try:
        import Quartz
        flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState)
        # NX_DEVICERALTKEYMASK = 0x00000040
        return bool(flags & 0x00000040)
    except Exception:
        return True  # can't check, assume still held


def watchdog():
    """Every 1s, check if on_release was missed (key released but _pressed still True).
    Also check stuck press > 120s, and keep input volume at 100."""
    last_press_time = [0.0]
    volume_ticks = [0]

    while True:
        time.sleep(1)
        if _pressed:
            last_press_time[0] += 1
            # Key physically released but on_release was missed — fix it now
            if not _alt_r_physically_held():
                print("  (watchdog: on_release missed, fixing)", flush=True)
                # Use on_release which sets _pressed=False AND calls stop_recording
                import pynput.keyboard as _kb
                try:
                    on_release(_kb.Key.alt_r)
                except Exception:
                    pass
                last_press_time[0] = 0
            elif last_press_time[0] >= 120:
                print("  (watchdog: stuck press detected, resetting)", flush=True)
                reset_state()
                last_press_time[0] = 0
        else:
            last_press_time[0] = 0

        # Every 30s, force input volume back to 100 in case something lowered it
        volume_ticks[0] += 1
        if volume_ticks[0] >= 30:
            subprocess.run(["osascript", "-e", "set volume input volume 100"], capture_output=True)
            volume_ticks[0] = 0


def main():
    global _transcribe_cancelled
    _start_persistent_stream()  # One stream for both VAD and manual
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    threading.Thread(target=watchdog, daemon=True).start()

    try:
        while True:
            try:
                chunks = transcribe_queue.get(timeout=0.1)
                t = threading.Thread(target=transcribe, args=(chunks,), daemon=True)
                t.start()
                t.join(timeout=60)
                if t.is_alive():
                    _transcribe_cancelled = True
                    print("  (transcription timed out, ready again)", flush=True)
                    if _was_muted:
                        open(MUTE_FLAG, "w").close()
                        print("  🔇 Restored mute after timeout", flush=True)
                    while not transcribe_queue.empty():
                        try:
                            transcribe_queue.get_nowait()
                        except queue.Empty:
                            break
                else:
                    _transcribe_cancelled = False
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        listener.stop()
        try:
            os.unlink(_LOCK_FILE)
        except FileNotFoundError:
            pass
        try:
            os.unlink("/tmp/recording_active")
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
