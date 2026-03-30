---
name: add-cron
description: Create a cron job on VPS with idempotent flag file
trigger: add cron, schedule task, cron job, scheduled
tags: [cron, schedule, vps, automation]
---

# Add Cron Job

## Steps

### 1. Create standalone Python script
- Script must be self-contained (not import from bot framework unless necessary)
- Include idempotent flag file check in PROJECT dir (not /tmp — survives reboots)
- Include retry logic with backoff
- Log to stdout (captured by cron redirect)

```python
#!/usr/bin/env python3
"""Description of what this cron does."""
import os, sys
from datetime import datetime

FLAG_FILE = os.path.join(os.path.dirname(__file__), '.last_<name>_run')

# Idempotent check — skip if already ran today
if os.path.exists(FLAG_FILE):
    with open(FLAG_FILE) as f:
        last_run = f.read().strip()
    if last_run == datetime.now().strftime('%Y-%m-%d'):
        print(f"Already ran today ({last_run}), skipping")
        sys.exit(0)

# ... do the work ...

# Write flag file
with open(FLAG_FILE, 'w') as f:
    f.write(datetime.now().strftime('%Y-%m-%d'))
```

### 2. Verify syntax
```bash
python3 -c "import py_compile; py_compile.compile('script.py', doraise=True)"
```

### 3. Deploy script to VPS
Use deploy-vps skill (git add, commit, push, pull on VPS).

### 4. Add cron entry on VPS
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "crontab -l > /tmp/cron_backup && crontab -l"
# Add new entry:
ssh YOUR_VPS_USER@YOUR_VPS_IP "(crontab -l; echo '0 12 * * * cd ~/telegram-claude-bot && source venv/bin/activate && python script.py >> /tmp/start_all.log 2>&1') | crontab -"
```

### 5. Test manually
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "cd ~/telegram-claude-bot && source venv/bin/activate && python script.py"
```

### 6. Verify flag file written
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "cat ~/telegram-claude-bot/.last_<name>_run"
```

## Time zones
- VPS is UTC. HKT = UTC+8.
- 12:00 HKT = 04:00 UTC in cron
- Always comment cron lines with HKT time for clarity

## Rules
- Flag files go in project dir, NOT /tmp (survives reboot)
- Always backup crontab before editing
- Always redirect output to /tmp/start_all.log (centralized logging)
- Always `cd ~/telegram-claude-bot && source venv/bin/activate` before running
- Commit the script to git — don't leave it untracked
