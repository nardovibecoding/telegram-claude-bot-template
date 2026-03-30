#!/usr/bin/env python3
"""
auto_review_before_done.py — Stop hook
Fires before Claude ends its turn. If files were edited this turn,
injects a self-review checklist so Claude reasons about logic correctness
BEFORE reporting "done" to Owner.

This is the logic-bug layer that syntax/lint tools can't catch:
- Did the fix actually solve the root cause (not just silence the error)?
- Are there edge cases unhandled?
- Does the change break any callers / dependents?
- Is the behavior change what Owner asked for?

The hook outputs to stdout → Claude sees it as context and must address it.
Exit 2 = block Claude from ending turn (forces review).
Exit 0 = allow turn to end normally.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# Track edits per session in a temp file
_EDIT_LOG = Path("/tmp/claude_edits_this_turn.json")


def load_edits():
    try:
        if _EDIT_LOG.exists():
            data = json.loads(_EDIT_LOG.read_text())
            # Only count edits from the last 10 minutes (one turn)
            now = datetime.now().timestamp()
            recent = [e for e in data if now - e.get("ts", 0) < 600]
            return recent
    except Exception:
        pass
    return []


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        sys.exit(0)

    # Only fire on Stop event
    if data.get("event") not in ("Stop", None):
        # Also works when called as generic stop hook (no event field)
        pass

    edits = load_edits()
    if not edits:
        sys.exit(0)  # Nothing was edited this turn — skip

    # Build file list
    edited_files = list(dict.fromkeys(e["file"] for e in edits))  # dedup, preserve order
    file_list = "\n".join(f"  • {f}" for f in edited_files)

    # Output self-review prompt — Claude MUST reason through this before saying done
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  🔍 LOGIC REVIEW — before you say "done"                 ║
╚══════════════════════════════════════════════════════════╝

You edited these files this turn:
{file_list}

Before reporting complete, verify each change:

1. ROOT CAUSE — Did you fix the actual cause, or just suppress the symptom?
2. EDGE CASES — Empty input? None values? Concurrent calls? Large data?
3. CALLERS — Do all callers of changed functions still work correctly?
4. SIDE EFFECTS — Any shared state, globals, or DB writes affected?
5. BEHAVIOR MATCH — Does the result match exactly what Owner asked for?

If any answer is "not sure" → investigate before saying done.
If all clear → proceed.
""".strip())

    # Clear the edit log after review
    try:
        _EDIT_LOG.write_text("[]")
    except Exception:
        pass

    # Exit 2 = block Claude from ending turn, forcing it to address the checklist
    sys.exit(2)


if __name__ == "__main__":
    main()
