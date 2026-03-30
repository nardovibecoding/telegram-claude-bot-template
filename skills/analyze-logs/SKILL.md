---
name: analyze-logs
description: Analyze start_all.log for recurring errors and patterns
trigger: analyze logs, log analysis, error patterns, what's broken
tags: [logs, analysis, errors, monitoring]
---

# Analyze Logs

## Primary log
`/tmp/start_all.log` — ALL bot output goes here. No other logs matter.

## Steps

### 1. Get error frequency
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "grep 'ERROR\|WARNING' /tmp/start_all.log | sort | uniq -c | sort -rn | head -20"
```

### 2. Get recent errors (last few hours only)
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "grep 'ERROR\|WARNING' /tmp/start_all.log | tail -20"
```

### 3. Check for tracebacks
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "grep -B2 'Traceback\|Exception\|Error' /tmp/start_all.log | tail -30"
```

### 4. Check restart frequency
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "grep 'Starting\|stopped.*restarting' /tmp/start_all.log | tail -20"
```
- Frequent restarts = crash loop, investigate the error before the restart

### 5. Group by type
Categorize found errors:
- **Fatal**: crashes, unhandled exceptions → fix immediately
- **Recurring**: same error repeating → systematic issue, fix root cause
- **Transient**: network timeouts, API errors → add retry if not present
- **Historical**: old errors that stopped → already resolved, ignore

### 6. Propose fixes for top 3
For each top error:
1. Identify source file and line number
2. Understand the root cause
3. Propose a targeted fix (not a band-aid)

## Rules
- ONLY report recent issues — filter by recency
- Clearly separate CURRENT problems from HISTORICAL (resolved) ones
- Don't just list errors — propose actionable fixes
- If root cause is environmental (blocked IP, missing dependency), say so immediately
- Never read /tmp/admin_bot.log or other bot-specific logs — they're stale
