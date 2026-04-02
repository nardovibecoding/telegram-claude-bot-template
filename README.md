# telegram-claude-bot-template

Run multiple AI personas as Telegram bots — each with its own character, memory, and content feeds — all managed through a single Claude Code-powered admin bot.

Built and battle-tested as a personal AI assistant system running 8 bots 24/7 on a €5/month VPS.

---

## What you get

### Multi-persona bot framework

Most bot systems give you one identity. But different contexts need different voices — a research assistant, a crypto analyst, a personal coach. Building separate codebases for each is wasteful, and manually keeping them in sync is worse. This framework runs every persona from a single shared codebase. Each bot is just a JSON config file — define the character, the system prompt, and what content it receives. Add a new persona in minutes, not days.

### Claude Code admin bot

Your bots are running on a VPS. When something breaks, or you want to push a change, you'd normally need to SSH in, edit files, restart processes. That's fine at a desk — impossible on a phone at 2am. The admin bot gives you full Claude Code tool access directly in Telegram: read and edit files, run terminal commands, commit and deploy code. A `/panel` control panel shows all bot statuses with restart buttons. You manage the entire system from your phone without touching a terminal.

### Auto-healer

Bots crash. APIs go down. Processes hang. On a €5 VPS running 8 bots, something will go wrong overnight and you won't know until morning. The auto-healer runs every 3 hours, checks every component, and when it finds a failure it spawns Claude Code to diagnose and fix it automatically. If it can't fix it, it alerts you in Telegram with the exact severity and what it tried. Issues don't re-alert for 24 hours so you're not spammed. Most problems resolve before you wake up.

### LLM fallback chain

Any single LLM API will go down — rate limits, outages, billing issues. A bot that depends on one provider is one failure away from going silent. The fallback chain routes through Kimi → Cerebras → DeepSeek → Gemini automatically. If the primary fails mid-conversation, the next one picks it up. All bots share one config in `llm_client.py` — change the chain order in one place and every persona picks it up instantly.

### Content digest pipelines

Your bots can be more than chat — they can push curated content to you on a schedule. News with multi-source cross-checking, X/Twitter curation filtered by your bookmark taste profile, Reddit digests with fuzzy dedup so the same story doesn't appear twice, YouTube and podcast summaries, crypto and China trends. A fetch watchdog runs daily and alerts if any source goes stale. All optional — use the ones that fit your setup.

### Team agents

Working solo on a complex product means wearing every hat at once — researcher, critic, builder, growth strategist. Context gets lost between sessions and between roles. The team agent system maps specialized AI agents to threads in a Telegram group: Scout researches, Critic stress-tests, Builder implements, Growth strategizes. They share context automatically through file-based handoffs — Builder sees what Scout found without you copy-pasting anything. One `/approve` unlocks the team after Scout reports. `/resetphase` wipes the slate for the next problem.

### Voice

Typing out a complex bug or a half-formed idea is slow and loses nuance. When you're stuck on something hard, speaking it out loud is faster and more natural — you can describe the problem, think through it, give long instructions without switching to a keyboard. Voice input sends your audio straight to the admin bot via Whisper `large-v3`. Per-persona language settings mean each bot transcribes in the right language. TTS lets bots speak responses back when you'd rather listen than read.

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

### Option A: Interactive setup (recommended)

```bash
git clone https://github.com/nardovibecoding/telegram-claude-bot-template
cd telegram-claude-bot-template
./setup.sh
```

The setup script walks you through everything: Python check, dependency install, Telegram token setup, and creating your first persona bot.

### Option B: Manual setup

```bash
git clone https://github.com/nardovibecoding/telegram-claude-bot-template
cd telegram-claude-bot-template
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in your tokens and keys
cp personas/example.json personas/mybot.json  # customize your bot
./start_all.sh
```

### Option C: Docker

```bash
git clone https://github.com/nardovibecoding/telegram-claude-bot-template
cd telegram-claude-bot-template
cp .env.example .env       # fill in your tokens and keys
cp personas/example.json personas/mybot.json
docker compose up -d
```

Logs: `tail -f /tmp/start_all.log` | Stop: `./start_all.sh stop` (or `docker compose down`)

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

Persona bots are auto-discovered on startup -- just drop the JSON file and run `./start_all.sh`. No need to edit any scripts.

---

## Getting your Telegram IDs

### Bot token
1. Open Telegram, search for **@BotFather**
2. Send `/newbot`, follow the prompts
3. Copy the token (looks like `123456:ABC-DEF...`)
4. Create a separate bot for each persona

### Your user ID (ADMIN_USER_ID)
1. Search for **@userinfobot** on Telegram
2. Send it any message -- it replies with your numeric user ID

### Group chat ID (GROUP_ID)
1. Create a Telegram group and add your admin bot
2. Send any message in the group
3. Run:
   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | python3 -m json.tool | grep '"id"' | head -5
   ```
4. The negative number (like `-100...`) is your group ID

---

## Deploying to a VPS

The system is designed for a cheap Linux VPS (tested on Hetzner CX22, €4.35/mo):

```bash
# On VPS: clone repo, set up .env, install deps
git clone https://github.com/nardovibecoding/telegram-claude-bot-template
cd telegram-claude-bot-template && python -m venv venv && source venv/bin/activate
pip install -r requirements.txt && cp .env.example .env

# Start bots
./start_all.sh

# Auto-pull updates (add to crontab)
*/1 * * * * cd ~/telegram-claude-bot-template && git pull --ff-only >> /tmp/gitpull.log 2>&1

# Auto-healer (add to crontab)
0 */3 * * * cd ~/telegram-claude-bot-template && source venv/bin/activate && python auto_healer.py >> /tmp/healer.log 2>&1
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

## Team agents — how handoffs work

Create a Telegram group with topics (threads). Each topic maps to an agent role:

| Thread | Agent | Role |
|---|---|---|
| Market Research | Scout | Find opportunities, competitor analysis, trend scanning |
| Growth | Growth | Distribution strategy, user acquisition, viral loops |
| Challenge | Critic | Stress-test ideas, find flaws, kill bad ideas early |
| Build | Builder | Write production code, implement features |

Register the group: `/domain team_a`

**Workflow:**
```
Scout researches → /approve unlocks other agents → Builder/Growth/Critic work
                                                  ↓
                                          /resetphase starts fresh
```

Agent outputs are saved to `.handoffs/` (7-day TTL). Each agent sees the other agents' context prepended to its prompt — no manual copy-paste. Builder's output gets auto-reviewed by Opus before committing, with up to 3 Sonnet fix rounds. Commit requires explicit approval via inline button.

**Adding more teams:**
1. Add system prompts to `admin_bot/config.py` (e.g. `"team_b:scout"`)
2. Add thread mappings to `admin_bot/domains.py`
3. Add domain entries to `admin_bot/handoff.py` `TEAM_DOMAINS` dict
4. Register: `/domain team_b`

---

## License

AGPL-3.0 — see [LICENSE](LICENSE)
