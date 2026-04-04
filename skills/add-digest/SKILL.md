---
name: add-digest
description: Create a new automated digest sender with flag file and retry logic
trigger: add digest, new digest, digest sender, scheduled digest
tags: [digest, telegram, schedule, cron]
---

# Add Digest Sender

## Existing digests
- `send_digest.py` — daliu/sbf news digest (staggered 11:39-12:00 HKT)
- `send_xdigest.py` — X curation digest (Elon/一龙/Altman/Niche)
- `send_reddit_digest.py` — Reddit top posts (11:59 HKT)

## Steps

### 1. Create standalone send_<name>.py
Key requirements:
- Flag file in project dir (not /tmp) for idempotent daily runs
- Per-message retry (3 attempts with backoff)
- 0.5s delay between sends (Telegram rate limit)
- Send to correct chat_id and thread_id

```python
#!/usr/bin/env python3
"""Send <name> digest to Telegram."""
import os, sys, time, asyncio
from datetime import datetime

FLAG = os.path.join(os.path.dirname(__file__), f'.last_<name>_digest')

# Idempotent check
if os.path.exists(FLAG):
    with open(FLAG) as f:
        if f.read().strip() == datetime.now().strftime('%Y-%m-%d'):
            print("Already sent today, skipping")
            sys.exit(0)

async def send_with_retry(bot, chat_id, text, thread_id=None, retries=3):
    """Send message with retry and backoff."""
    for attempt in range(retries):
        try:
            await bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
                                   text=text, parse_mode='HTML')
            return True
        except Exception as e:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 2)
            else:
                print(f"Failed after {retries} attempts: {e}")
                return False

async def main():
    # ... generate digest content ...
    # ... send messages with send_with_retry ...
    # ... 0.5s delay between messages ...

    # Write flag
    with open(FLAG, 'w') as f:
        f.write(datetime.now().strftime('%Y-%m-%d'))

if __name__ == '__main__':
    asyncio.run(main())
```

### 2. Determine Telegram target
- Chat ID from TG link: `t.me/c/XXXX` -> chat_id `-100XXXX`
- Thread ID: the topic/thread number in the group

### 3. Add cron entry (use add-cron skill)
```bash
# Example: 12:00 HKT = 04:00 UTC
ssh <user>@<vps-ip> "(crontab -l; echo '0 4 * * * cd ~/telegram-claude-bot && source venv/bin/activate && python send_<name>.py >> /tmp/start_all.log 2>&1  # 12:00 HKT') | crontab -"
```

### 4. Test manually
```bash
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && source venv/bin/activate && python send_<name>.py"
```

### 5. Verify
- Check message arrived in correct TG thread
- Check flag file was written
- Check /tmp/start_all.log for errors

## Rules
- Stagger digest times to avoid overloading (see existing schedule)
- Always use per-message retry with backoff
- 0.5s minimum delay between TG messages
- Flag files in project dir, not /tmp
- Commit script and deploy via git
