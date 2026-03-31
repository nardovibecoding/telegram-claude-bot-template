# telegram-claude-bot-template

Run multiple AI personas as Telegram bots — each with its own character, memory, and content feeds — all managed through a single Claude Code-powered admin bot.

Built and battle-tested as a personal AI assistant system running 8 bots 24/7 on a €5/month VPS.

---

## What you get

**Multi-persona bot framework**
- Each persona is a JSON config — define character, system prompt, and what content it receives
- All bots share the same base (`bot_base.py`) with per-persona memory (SQLite vector DB)
- Bots auto-restart on crash via `start_all.sh`

**Claude Code admin bot**
- Control your entire bot system through a Telegram chat
- Full Claude Code tool access: read/edit files, run commands, commit code
- Animated progress spinner, queue system, photo/PDF support, `/panel` control panel
- Stuck-process watchdog with heartbeat monitoring

**Auto-healer**
- Runs every 3 hours, detects failures across all components
- Self-diagnoses and spawns Claude Code to auto-fix where possible
- Alerts to Telegram with severity levels (🔴 CRITICAL → 🟢 LOW)
- Issue deduplication — only re-alerts after 24h

**LLM fallback chain**
- Primary: Kimi → Cerebras → DeepSeek → Gemini → (any OpenAI-compatible)
- Configurable in `llm_client.py` — one place, all bots pick it up
- Per-model timeout + graceful degradation on API failures

**Content digest pipelines** (optional, use what you need)
- News aggregation with multi-source cross-checking (`news.py`)
- X/Twitter curation with bookmark taste profile (`x_curator.py`)
- Reddit digest with fuzzy dedup (`reddit_digest.py`)
- YouTube, podcast, crypto, China trends digests
- Fetch watchdog monitors source health daily

**Voice** (optional)
- STT: Whisper large-v3 via faster-whisper, per-persona language setting
- TTS: MiniMax T2A v2

---

## Architecture

```
start_all.sh
├── run_bot.py <id>   ← one process per persona
│   └── bot_base.py   ← shared handlers, memory, LLM calls
├── admin_bot.py      ← Claude Code bridge
│   └── admin_bot/    ← handlers, scheduler, cognitive
└── admin_watchdog    ← heartbeat monitor (built into start_all.sh)

personas/<id>.json    ← character + routing config per bot
llm_client.py         ← all LLM providers + fallback chain
auto_healer.py        ← self-healing cron (every 3h)
memory.py             ← vector DB per persona
```

---

## Quick start

**1. Clone and set up**
```bash
git clone https://github.com/nardovibecoding/telegram-claude-bot-template
cd telegram-claude-bot-template
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

**2. Configure**
```bash
cp .env.example .env
# Fill in .env — at minimum: TELEGRAM_BOT_TOKEN_ADMIN, ADMIN_USER_ID, KIMI_API_KEY (or any LLM key)
```

**3. Add a persona**
```bash
cp personas/example.json personas/mybot.json
# Edit mybot.json: set id, display_name, system_prompt, routing targets
```

**4. Start**
```bash
./start_all.sh
# Logs: tail -f /tmp/start_all.log
```

**5. Stop**
```bash
./start_all.sh stop
```

---

## Adding a persona

Each persona is a JSON file in `personas/`. Copy `personas/example.json` and customize:

| Field | Description |
|---|---|
| `id` | Must match filename (e.g. `mybot` → `personas/mybot.json`) |
| `display_name` | Name shown in logs and admin panel |
| `system_prompt` | Character and instructions for the LLM |
| `xcurate_target` | Telegram chat+thread to post X curation |
| `digest_enabled` | Enable news digest for this persona |
| `voice_enabled` | Enable STT/TTS |
| `twitter_accounts` | X accounts to monitor for this persona |

Register the persona in `start_all.sh`:
```bash
run_with_restart "MyBot" python run_bot.py mybot &
```

---

## Deploying to a VPS

The system is designed for a cheap VPS (tested on YOUR_VPS_PLAN, €4.35/mo):

```bash
# On VPS: clone repo, set up .env, install deps
git clone https://github.com/your-org/telegram-claude-bot-template
cd telegram-claude-bot && python -m venv venv && source venv/bin/activate
pip install -r requirements.txt && cp .env.example .env

# Start bots
./start_all.sh

# Auto-pull updates (add to crontab)
*/1 * * * * cd ~/telegram-claude-bot && git pull --ff-only >> /tmp/gitpull.log 2>&1

# Auto-healer (add to crontab)
0 */3 * * * cd ~/telegram-claude-bot && source venv/bin/activate && python auto_healer.py >> /tmp/healer.log 2>&1
```

---

## Key files

| File | Purpose |
|---|---|
| `bot_base.py` | Shared bot logic — all Telegram handlers |
| `run_bot.py` | Entry point: `python run_bot.py <persona_id>` |
| `admin_bot.py` | Claude Code admin bridge |
| `llm_client.py` | All LLM providers + fallback chain config |
| `auto_healer.py` | Self-healing system diagnostics |
| `memory.py` | Per-persona vector memory (SQLite) |
| `start_all.sh` | Process manager with auto-restart |
| `personas/` | One JSON per bot persona |
| `admin_bot/handoff.py` | Agent-to-agent context sharing |

---

## Team Agents

The template includes a multi-agent team system where specialized AI agents collaborate on a project through Telegram group topics.

### How it works

Create a Telegram group with topics (threads). Each topic maps to an agent role:

| Thread | Agent | Role |
|---|---|---|
| Market Research | Scout | Find opportunities, competitor analysis, trend scanning |
| Growth | Growth | Distribution strategy, user acquisition, viral loops |
| Challenge | Critic | Stress-test ideas, find flaws, kill bad ideas early |
| Build | Builder | Write production code, implement features |

Register the group: `/domain team_a`

### Agent workflow

```
Scout researches → /approve unlocks other agents → Builder/Growth/Critic work
                                                  ↓
                                          /resetphase starts fresh
```

### Handoffs

Agents automatically share context. When Scout finds an opportunity, Builder sees that context when you send it a task. No manual copy-paste needed.

- Agent outputs saved to `.handoffs/` (file-based, 7-day TTL)
- Each agent sees other agents' output, not its own
- `/resetphase` clears all handoff context for a fresh start

### Guardrails

- Builder output gets auto-reviewed by Opus before committing
- Up to 3 fix rounds (Sonnet fixes issues Opus finds)
- Commit requires explicit approval via inline button

### Adding more teams

1. Add system prompts to `admin_bot/config.py` (e.g. `"team_b:scout"`)
2. Add thread mappings to `admin_bot/domains.py`
3. Add domain entries to `admin_bot/handoff.py` `TEAM_DOMAINS` dict
4. Register the new group: `/domain team_b`

---

## License

AGPL-3.0 — see [LICENSE](LICENSE)
