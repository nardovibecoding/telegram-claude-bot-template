---
name: add-domain
description: Add a Telegram group/topic to domain_groups.json for routing
trigger: add domain, add group, new telegram group, domain routing
tags: [telegram, domain, routing, config]
---

# Add Domain Group

## Config file
`domain_groups.json` — maps domain names to Telegram chat/thread IDs

## Steps

### 1. Extract chat_id from Telegram link
- Link format: `t.me/c/XXXX/YYY`
- Chat ID: `-100XXXX` (prepend -100)
- Thread ID: `YYY` (the topic number)

Example: `t.me/c/XXXXXXXXXX/42` → chat_id: `-100XXXXXXXXXX`, thread_id: `42`

### 2. Read current config
```bash
cat ~/telegram-claude-bot/domain_groups.json
```

### 3. Add new entry
Edit `domain_groups.json` to add the new domain:
```json
{
    "existing_domain": { ... },
    "new_domain": {
        "chat_id": -100XXXX,
        "thread_id": YYY,
        "description": "Human-readable description"
    }
}
```

### 4. Verify JSON is valid
```bash
python3 -c "import json; json.load(open('domain_groups.json')); print('Valid JSON')"
```

### 5. Commit and deploy
```bash
git add domain_groups.json
git commit -m "add <domain> to domain_groups"
git push origin main
ssh YOUR_VPS_USER@YOUR_VPS_IP "cd ~/telegram-claude-bot && git fetch && git reset --hard origin/main"
```

### 6. Verify with test message
Send a test message through the domain routing to confirm it arrives in the correct TG thread.

## Rules
- ALWAYS verify chat_id by checking logs (`update.effective_chat.id`) — don't guess
- t.me/c/ URL uses different number than API chat_id (prefix -100)
- domain_groups.json is modified at runtime — MUST be committed to git
- `git reset --hard` will revert uncommitted changes to this file
- After editing, restart admin_bot to pick up changes
