# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""PID file lock — prevents multiple instances of the same bot.

Usage:
    from pidlock import acquire_lock

    if not acquire_lock("example_bot"):
        print("Another instance is running. Exiting.")
        sys.exit(1)
"""

import os
import sys
import signal
import atexit

_LOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".locks")
os.makedirs(_LOCK_DIR, exist_ok=True)


def acquire_lock(name: str, kill_existing: bool = False) -> bool:
    """Try to acquire a PID lock. Returns True if successful, False if another instance is running.

    If kill_existing=True, send SIGTERM to the old process (then SIGKILL after 3s)
    instead of returning False.
    """
    pidfile = os.path.join(_LOCK_DIR, f"{name}.pid")

    # Check if another instance is running
    if os.path.exists(pidfile):
        try:
            with open(pidfile) as f:
                old_pid = int(f.read().strip())
            # Check if that PID is still alive
            os.kill(old_pid, 0)  # Doesn't kill, just checks
            if not kill_existing:
                return False
            # Graceful shutdown: SIGTERM, wait up to 3s, then SIGKILL
            import time
            os.kill(old_pid, signal.SIGTERM)
            for _ in range(30):  # 3s in 0.1s increments
                time.sleep(0.1)
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    break
            else:
                try:
                    os.kill(old_pid, signal.SIGKILL)
                    time.sleep(0.5)
                except ProcessLookupError:
                    pass
        except (ProcessLookupError, ValueError):
            # Process is dead — stale PID file, safe to overwrite
            pass
        except PermissionError:
            # Process exists but we can't signal it — assume running
            return False

    # Write our PID
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))

    # Clean up on exit
    def _cleanup():
        try:
            os.unlink(pidfile)
        except OSError:
            pass

    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(0)))

    return True
