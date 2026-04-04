---
name: add-persona
description: Add a new bot persona with config, thread, and deployment
trigger: add persona, new bot, new persona, create bot
tags: [persona, bot, config, telegram]
---

# Add New Persona Bot

## Existing personas
daliu, sbf, devasini, twitter, xcn, xai, xniche, reddit

## Steps

### 1. Create persona config
Create `personas/<id>.json`:
```json
{
    "token": "BOT_TOKEN_FROM_BOTFATHER",
    "name": "Display Name",
    "chat_id": -100XXXX,
    "topic_id": THREAD_NUMBER,
    "system_prompt": "You are... personality description...",
    "whisper_language": "en",
    "model": "MiniMax-M2.5-highspeed",
    "api_base": "https://api.minimaxi.com/v1"
}
```

### 2. Add to config.py
In `admin_bot/config.py`, add:
- Bot ID to `BOTS` dict
- Thread ID to `BOT_THREADS` mapping

### 3. Add to start_all.sh
Add a new line:
```bash
run_with_restart "NewBot" python run_bot.py <id> &
```

### 4. Set up Telegram
- Create bot via @BotFather
- Get token → add to persona JSON
- Add bot to the target group
- Note the topic/thread ID
- Convert chat link: `t.me/c/XXXX` -> `-100XXXX`

### 5. Verify syntax
```bash
python3 -c "import json; json.load(open('personas/<id>.json'))"
python3 -c "import py_compile; py_compile.compile('admin_bot/config.py', doraise=True)"
```

### 6. Deploy
Use deploy-vps skill. After deploy:
```bash
# Restart start_all.sh to pick up new bot
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && ./start_all.sh stop && sleep 10 && nohup ./start_all.sh >> /tmp/start_all.log 2>&1 &"
```

### 7. Verify
```bash
ssh <user>@<vps-ip> "pgrep -af 'run_bot.py <id>'"
ssh <user>@<vps-ip> "tail -10 /tmp/start_all.log"
```
Send a test message in the persona's TG thread and verify response.

## Rules
- Persona configs are JSON, NOT yaml/toml/ini
- MiniMax plan ONLY covers M2.5 models — Text-01/M1 fail with insufficient_balance
- MiniMax M2.5-highspeed outputs `<think>...</think>` blocks — must strip before JSON parsing
- Token goes in persona JSON, NOT in .env
- Must restart start_all.sh (not just kill one bot) to add new entries
