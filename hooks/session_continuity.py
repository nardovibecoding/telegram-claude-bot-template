#!/usr/bin/env python3
"""UserPromptSubmit hook: inject recent work context after /clear or session start.

When context usage is very low (<8%), we just started fresh. Read today's
librarian-log entries and inject as systemMessage so Claude has continuity.

Uses a done marker to avoid re-injecting every message. The marker is
cleared when context goes above 15% (meaning we're mid-session), so it
resets naturally after the next /clear drops context back down.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

STATUSLINE_JSON = Path("/tmp/claude_statusline.json")
LIBRARIAN_LOG = Path.home() / "NardoWorld/meta/librarian-log.md"
DONE_MARKER = Path("/tmp/claude_continuity_done")
LOW_THRESHOLD = 8    # below this = just cleared/started
RESET_THRESHOLD = 15  # above this = mid-session, reset marker for next clear


def get_today_entries(log_path: Path) -> str:
    """Extract today's entries from librarian log."""
    if not log_path.exists():
        return ""

    today = datetime.now().strftime("%Y-%m-%d")
    lines = log_path.read_text().splitlines()
    entries = []
    capturing = False

    for line in lines:
        if line.startswith("## ") and today in line:
            capturing = True
            entries.append(line)
        elif line.startswith("## ") and capturing:
            break
        elif capturing:
            entries.append(line)

    return "\n".join(entries).strip()


def main():
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    if not STATUSLINE_JSON.exists():
        print("{}")
        return

    try:
        data = json.loads(STATUSLINE_JSON.read_text())
        ctx_pct = float(data.get("context_window", {}).get("used_percentage", 0))
    except (ValueError, OSError, KeyError):
        print("{}")
        return

    # Mid-session: clear done marker so it's ready for next /clear
    if ctx_pct >= RESET_THRESHOLD:
        DONE_MARKER.unlink(missing_ok=True)
        print("{}")
        return

    # Low context but already injected this cycle
    if ctx_pct < LOW_THRESHOLD and DONE_MARKER.exists():
        print("{}")
        return

    # Low context, haven't injected yet: this is a fresh start
    if ctx_pct < LOW_THRESHOLD:
        today_log = get_today_entries(LIBRARIAN_LOG)

        if not today_log:
            DONE_MARKER.write_text("no-entries")
            print("{}")
            return

        # Truncate if too long (keep it concise)
        if len(today_log) > 1500:
            today_log = today_log[:1500] + "\n... (truncated)"

        DONE_MARKER.write_text("injected")
        sys.stderr.write(f"[continuity] Injected today's librarian log at {ctx_pct:.0f}%\n")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    "SESSION CONTINUITY: Here's what was worked on today "
                    "(from librarian-log.md). Use this as context if the user "
                    "references earlier work or runs skills like /story that need "
                    "session history.\n\n" + today_log
                )
            }
        }))
        return

    print("{}")


if __name__ == "__main__":
    main()
