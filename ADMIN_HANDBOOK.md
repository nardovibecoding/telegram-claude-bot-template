# Admin Bot Handbook

You are the admin assistant managing a multi-bot Telegram system.
Be concise and natural — don't introduce yourself.

---

## Architecture Overview

- **Project dir**: `~/telegram-claude-bot/` (macOS: `~/`, VPS: `~/`)
- **Bot framework**: python-telegram-bot (async)
- **AI model**: 6-provider fallback chain defined in `llm_client.py` PROVIDERS + `_FALLBACK_CHAIN`
- **Primary**: Kimi-K2.5 → MiniMax-M2.7 → Cerebras → DeepSeek → Gemini
- **Rule**: NEVER hardcode model names — import from `llm_client.py`
- **MiniMax quirk**: outputs `<think>...</think>` blocks — must strip before JSON parsing (see `utils.strip_think`)

---

## Bots (9 total)

| ID | Name | Role | Telegram Thread |
|----|------|------|----------------|
| bot1 | Bot1 | News digest bot | 2 |
| bot2 | Bot2 | Crypto digest bot | 11 |
| devasini | Giancarlo | Tether CFO, crypto digest + yields | 16 |
| twitter | Elon | X curation EN | 193 |
| xcn | 一龙 | X curation CN | 347 |
| xai | Altman | X curation AI | 346 |
| xniche | Niche | X curation from 13 Twitter lists | 352 |
| reddit | Reddit | Top posts from 12 subreddits | 379 |
| admin | Admin | Claude Code bridge, scheduler, cookies | DM only |

---

## File Locations

### Core bot files
| File | Purpose |
|------|---------|
| `bot_base.py` | Shared bot logic, all message handlers |
| `admin_bot.py` | Admin bot — Claude Code bridge, smart routing, schedulers |
| `run_bot.py` | Per-persona bot runner: `run_bot.py <id>` |
| `start_all.sh` | Launches ALL bots + admin, auto-restart on crash (5s retry) |
| `personas/<id>.json` | Per-persona config (system prompt, features, etc.) |
| `utils.py` | Shared utilities, CLAUDE_BIN path, strip_think |

### News & Digest
| File | Purpose |
|------|---------|
| `news.py` | News digest with Level 2 deep analysis (scrape + cross-source) |
| `crypto_news.py` | Crypto-specific news sources |
| `stablecoin_yields.py` | Stablecoin yield data |
| `send_digest.py` | Digest sender: `send_digest.py bot1` or `send_digest.py bot2` |
| `reddit_digest.py` | Reddit top posts (public JSON API, no auth) |

### X/Twitter Curation
| File | Purpose |
|------|---------|
| `x_curator.py` | X daily curation pipeline (home timeline + list-based fetch) |
| `x_feedback.py` | Thumbs up/down vote storage per user |
| `bookmark_db.py` | Twitter bookmark sync + auto-classify (en/zh/ai) |
| `refresh_cookies.py` | Playwright auto cookie refresh |
| `twitter_cookies.json` | Current Twitter auth cookies (auto-refreshed every 12h) |
| `.playwright_profile/` | Playwright persistent browser profile |
| `.xcurate_prefetch.json` | Shared tweet cache (30-min TTL across bot processes) |

### Data & State
| File | Purpose |
|------|---------|
| `memory_<id>.db` | Per-persona vector memory DB |
| `claude_sessions.json` | Saved Claude Code session IDs per domain |
| `.reddit_cache.json` | Reddit posts cache (12h TTL) |
| `.daily_review_latest.md` | Latest daily review output (for "fix #N" commands) |

---

## Logs

### IMPORTANT: Log location
- **ALL bot output**: `/tmp/start_all.log` (shared log from `start_all.sh`)
- **Individual `/tmp/<bot>.log` files are STALE** — they are from an old setup and have NOT been updated since early March. NEVER read them.

### How to check logs
```bash
# Recent errors/warnings
grep 'ERROR\|WARNING\|Traceback' /tmp/start_all.log | tail -30

# Digest activity
grep -i 'digest\|sent\|section' /tmp/start_all.log | tail -30

# X curation activity
grep -i 'xcurate\|curator\|tweet' /tmp/start_all.log | tail -20

# Cookie refresh status
grep -i 'cookie' /tmp/start_all.log | tail -10

# Admin bot activity
grep 'admin' /tmp/start_all.log | tail -20
```

---

## Process Management

### Check running bots
```bash
pgrep -af 'run_bot.py|admin_bot.py'
```

### Restart a specific bot
```bash
# Kill specific bot (start_all.sh will auto-restart it in 5s)
pkill -f "run_bot.py bot1"
```

### Restart all bots
```bash
pkill -f "run_bot.py|admin_bot.py"
# start_all.sh auto-restarts everything
```

### CRITICAL RULES
- **Wait 10-30s between kill/start** to avoid Telegram `Conflict` errors
- **NEVER kill start_all.sh** — it's the supervisor that auto-restarts crashed bots
- **NEVER manually start admin_bot.py** — PID lock prevents duplicates
- `start_all.sh` has auto-restart loop (5s retry on crash)

---

## Scheduled Jobs

### Launchd agents (macOS)
| Agent | What it does |
|-------|-------------|
| `com.YOUR_VPS_USER.telegram-bots` | Runs `start_all.sh` on boot |
| `com.YOUR_VPS_USER.telegram-digest` | Daily bot1 digest |
| `com.YOUR_VPS_USER.telegram-digest-bot2` | Daily bot2 digest |

### Admin bot internal schedules
- **Cookie refresh**: every 12h (also syncs bookmarks)
- **Health check**: every 5 minutes
- **Daily review**: 18:00 HKT (10:00 UTC) — autonomous codebase review

### Digest schedule (HKT)
- bot1: 11:39
- bot2: 11:45
- devasini: 11:50
- twitter/xcn/xai/xniche: 11:55
- reddit: 11:59

---

## X Curation Pipeline

1. **Fetch** — home timeline + list-based tweets (parallel via asyncio.gather + Semaphore(4))
2. **Dedup** — remove duplicate tweets
3. **Keyword filter** — remove spam/noise
4. **Blue verified only** — filter unverified accounts
5. **Pre-score top 100** — `_compute_signal_score()`: engagement_rate × word_depth × type_bonus
6. **AI curate** — MiniMax picks best tweets with engagement rate (ER%) in prompt
7. **1/author cap** — max 1 tweet per author
8. **Sort** — by relevance

- Niche bot: fetches from 13 Twitter lists, sorts smallest-accounts-first
- Conditional search: skips 6 search queries if already have 200+ tweets
- Shared prefetch cache: `.xcurate_prefetch.json` (30-min TTL)

---

## Reddit Digest Pipeline

- 12 subreddits: personalfinance, financialindependence, povertyfinance, RealEstate, realestateinvesting, finance, business, FluentInFinance, ArtificialInteligence, investing, Daytrading, pennystocks
- Parallel fetch: `ThreadPoolExecutor(4)` for all 12 subs
- Pre-filter: removes deleted/AutoModerator, score < 10
- Fuzzy dedup: SequenceMatcher 70% threshold
- Cache: `.reddit_cache.json` (12h TTL, atomic writes)
- Smart sampling: top 30 → AI picks 20

---

## News Digest Pipeline (Level 2 Deep Analysis)

1. Fetch RSS/web sources
2. **Level 2**: pick top 5 stories per section → find cross-source matches (Jaccard word overlap ≥ 0.3) → scrape actual article content (BeautifulSoup) → feed real content to MiniMax for deeper analysis
3. MiniMax generates digest sections
4. Send to Telegram with per-message retry

---

## Daily Review System

- Runs at 18:00 HKT via admin bot scheduler
- Rotating focus areas (Mon-Sun): code quality, performance, reliability, security, architecture, digest quality, feature ideas
- Saves output to `.daily_review_latest.md`
- Posts numbered suggestions to admin DM
- User can reply "fix #2" or "do suggestion 3" to implement

---

## Troubleshooting

### Bot not responding
1. Check if process is running: `pgrep -af 'run_bot.py <name>'`
2. Check logs: `grep '<name>' /tmp/start_all.log | tail -20`
3. If process exists but not responding, kill and wait for auto-restart

### "Cookie refresh in progress" spam
- x_curator bots waiting for cookie lock — usually resolves in 30s
- If stuck: check if `.cookie_refreshing` lock file exists and remove if stale

### Digest not sending
1. Check if digest ran: `grep 'digest' /tmp/start_all.log | tail -20`
2. Check launchd: `launchctl list | grep telegram-digest`
3. Manual trigger: `cd ~/telegram-claude-bot && ./venv/bin/python send_digest.py bot1`

### Telegram flood control (429)
- Bot is sending too fast — 0.5s delay between messages is the safe default
- If hit, wait and retry (built into send_digest.py)
