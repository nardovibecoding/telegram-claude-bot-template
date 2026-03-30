# Code Review Checklist

Every review MUST check ALL categories below. For each issue found:
- Cite file:line
- Classify severity: **CRITICAL** / **HIGH** / **MEDIUM** / **LOW**
- Explain WHY it's a problem
- Give the exact fix (code snippet)

---

## 1. Runtime Errors & Crashes (CRITICAL)
- [ ] Unhandled exceptions in async handlers (missing try/except)
- [ ] Missing `await` on coroutines
- [ ] Undefined variables / UnboundLocalError paths
- [ ] Division by zero, IndexError, KeyError without guards
- [ ] File operations without existence checks on critical paths
- [ ] Processes spawned but never awaited or cleaned up

## 2. Security (CRITICAL / HIGH)
- [ ] Hardcoded secrets, tokens, API keys in source code
- [ ] Shell injection via unsanitized user input in subprocess calls
- [ ] Telegram chat_id / user_id authorization bypasses
- [ ] File paths constructed from user input without sanitization
- [ ] Sensitive data logged to /tmp/start_all.log
- [ ] Permissions on state files (.env, cookies, sessions)

## 3. Silent Failures (HIGH)
- [ ] Bare `except: pass` swallowing real errors
- [ ] API calls with no error logging on failure
- [ ] Digest/cron jobs that fail silently (no flag file, no notification)
- [ ] Network timeouts with no retry or user notification
- [ ] JSON parse failures that return empty results instead of errors

## 4. Performance (MEDIUM)
- [ ] Blocking I/O in async handlers (sync file reads, subprocess without async)
- [ ] Unnecessary API calls (duplicate fetches, uncached results)
- [ ] Large file reads that could use streaming/pagination
- [ ] Missing connection pooling or session reuse
- [ ] O(n^2) loops on data that could be O(n) with sets/dicts

## 5. Code Quality (MEDIUM / LOW)
- [ ] Dead code (unreachable branches, unused imports, stale functions)
- [ ] Dead FILES — whole .py files that are no longer imported or run by anything (check: grep for imports, check start_all.sh, check cron). Examples: old bot scripts replaced by new systems, one-off patch scripts left behind, agent-created files never committed.
- [ ] Uncommitted files on VPS (`git status` on VPS) — agents may have scp'd files that conflict with git pull
- [ ] Duplicated logic across files (copy-paste patterns)
- [ ] Magic numbers without named constants
- [ ] Inconsistent error message formats
- [ ] Missing type hints on public function signatures

## 6. Reliability (HIGH / MEDIUM)
- [ ] Race conditions in shared state (concurrent writes to same file)
- [ ] Missing retry logic on flaky external APIs
- [ ] No timeout on network operations
- [ ] Flag files not atomic (partial writes on crash)
- [ ] Process management: orphan processes, PID file stale checks

## 7. Operational Health (check /tmp/start_all.log)
- [ ] Recurring errors in last 24h (grep ERROR/WARNING/Traceback)
- [ ] Bot restart loops (same bot dying repeatedly)
- [ ] Digest failures (empty or incomplete digests sent)
- [ ] Memory/disk warnings
- [ ] Stale heartbeat or watchdog triggers
