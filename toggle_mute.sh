#!/bin/bash
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
# Toggle Claude TTS mute — call from Siri Shortcut or keyboard
MUTE_FLAG="/tmp/tts_muted"

if [ -f "$MUTE_FLAG" ]; then
    rm "$MUTE_FLAG"
    osascript -e 'display notification "Claude voice ON 🔊" with title "Claude TTS"'
else
    touch "$MUTE_FLAG"
    pkill -x afplay 2>/dev/null  # stop any playing TTS immediately
    osascript -e 'display notification "Claude voice OFF 🔇" with title "Claude TTS"'
fi
