---
name: debug-xcurate
description: Debug X curation pipeline issues (cookies, prefetch, lock files)
trigger: xcurate broken, x digest failed, twitter curation, xdigest not working
tags: [xcurate, twitter, debug, cookies, curation]
---

# Debug X Curation

## Components
- `x_curator.py` — main curation pipeline
- `twitter_cookies.json` — auth cookies (auto-refreshed every 12h from Mac)
- `.xcurate_prefetch.json` — shared prefetch cache (30-min TTL)
- `refresh_cookies.py` — Playwright cookie refresh (Mac only)
- `bookmark_db.py` — bookmark taste profile

## Steps

### 1. Check cookie age
```bash
ssh <user>@<vps-ip> "stat -c '%Y' ~/telegram-claude-bot/twitter_cookies.json && echo 'now:' && date +%s"
```
- Should be refreshed every 12h
- If >36h old: `touch .cookies_need_refresh` (Mac picks up in 10 min)

### 2. Check prefetch cache
```bash
ssh <user>@<vps-ip> "python3 -c \"
import json, time, os
f = os.path.expanduser('~/telegram-claude-bot/.xcurate_prefetch.json')
if os.path.exists(f):
    d = json.load(open(f))
    age = time.time() - d.get('timestamp', 0)
    print(f'Age: {age/60:.0f} min, TTL: 30 min')
    print(f'Tweets cached: {len(d.get(\"tweets\", []))}')
else:
    print('No prefetch cache found')
\""
```
- TTL: 30 minutes across bot processes
- If stale: will auto-refresh on next curation run

### 3. Check cookie lock file
```bash
ssh <user>@<vps-ip> "ls -la ~/telegram-claude-bot/.cookie_refresh_lock 2>/dev/null || echo 'No lock'"
```
- If lock stuck >5 minutes: delete it
```bash
ssh <user>@<vps-ip> "rm -f ~/telegram-claude-bot/.cookie_refresh_lock"
```

### 4. Test manual curation
```bash
# Via TG: send /xdigest to admin bot
# Or manually:
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && source venv/bin/activate && python send_xdigest.py"
```

### 5. Check logs for pipeline errors
```bash
ssh <user>@<vps-ip> "grep -i 'xcurat\|curator\|twikit\|twitter' /tmp/start_all.log | tail -20"
```

## Pipeline flow
1. Fetch (home timeline + lists via twikit)
2. Dedup (remove seen tweets)
3. Keyword filter
4. Blue-verified only
5. Pre-score top 100 (`_compute_signal_score()`)
6. AI curate (MiniMax M2.5-highspeed)
7. 1/author cap
8. Sort and format

## Niche bot specifics
- Fetches from 13 Twitter lists via `_fetch_single_list()`
- Parallel via `asyncio.gather()` + `Semaphore(4)`
- Sorts smallest accounts first (captures `followers_count`)

## Known issues
- VPS IP blocked by Cloudflare — cookie refresh MUST happen on Mac
- MiniMax `<think>` blocks must be stripped before JSON parsing
- Conditional search: skips 6 search queries if already have 200+ tweets
