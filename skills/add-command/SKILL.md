---
name: add-command
description: Add a new Telegram command to admin bot
trigger: add command, new command, bot command, slash command
tags: [telegram, command, admin-bot, handler]
---

# Add Telegram Command

## File locations
- Command handlers: `admin_bot/commands.py`
- Handler registration: `admin_bot/__main__.py`
- Config/constants: `admin_bot/config.py`

## Steps

### 1. Create handler function in commands.py
```python
async def cmd_mycommand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mycommand."""
    chat_id = update.effective_chat.id
    # ... implementation ...
    await update.message.reply_text("Response")
```

### 2. Register in __main__.py
Find the command handler registration section and add:
```python
app.add_handler(CommandHandler("mycommand", cmd_mycommand))
```
Import the function if needed:
```python
from admin_bot.commands import cmd_mycommand
```

### 3. Verify syntax
```bash
python3 -c "import py_compile; py_compile.compile('admin_bot/commands.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('admin_bot/__main__.py', doraise=True)"
```

### 4. Deploy
Use deploy-vps skill:
```bash
git add admin_bot/commands.py admin_bot/__main__.py
git commit -m "add /mycommand command"
git push origin main
ssh YOUR_VPS_USER@YOUR_VPS_IP "cd ~/telegram-claude-bot && git fetch && git reset --hard origin/main"
ssh YOUR_VPS_USER@YOUR_VPS_IP "kill \$(pgrep -f 'python admin_bot' | head -1)"
# Wait 5s for start_all.sh auto-restart
```

### 5. Test
Send `/mycommand` in the admin Telegram chat and verify response.

### 6. Verify in logs
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "tail -10 /tmp/start_all.log"
```

## Rules
- Always py_compile BOTH files before deploying
- Never manually start admin_bot — let start_all.sh restart it
- Keep handler functions focused — put business logic in separate modules
- Use `update.effective_chat.id` for chat context
- Add error handling with try/except and log to stdout
