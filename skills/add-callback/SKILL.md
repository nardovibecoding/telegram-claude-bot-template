---
name: add-callback
description: Add a new callback query handler for inline buttons in admin bot
trigger: add callback, inline button, callback handler, button handler
tags: [telegram, callback, inline, button, admin-bot]
---

# Add Callback Query Handler

## File locations
- Callback handlers: `admin_bot/callbacks.py`
- Handler registration: `admin_bot/__main__.py`

## Steps

### 1. Create handler function in callbacks.py
```python
async def handle_myprefix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle myprefix: callback queries."""
    query = update.callback_query
    await query.answer()  # Always answer to remove loading spinner

    data = query.data  # e.g., "myprefix:some_value"
    _, value = data.split(":", 1)

    # ... implementation ...

    await query.edit_message_text(f"Result: {value}")
```

### 2. Register in __main__.py
Find the callback handler registration section and add:
```python
app.add_handler(CallbackQueryHandler(handle_myprefix, pattern="^myprefix:"))
```
Import the function:
```python
from admin_bot.callbacks import handle_myprefix
```

### 3. Create buttons that trigger this callback
In whatever command sends the message:
```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

keyboard = [[
    InlineKeyboardButton("Label", callback_data="myprefix:value1"),
    InlineKeyboardButton("Other", callback_data="myprefix:value2"),
]]
reply_markup = InlineKeyboardMarkup(keyboard)
await update.message.reply_text("Choose:", reply_markup=reply_markup)
```

### 4. Verify syntax
```bash
python3 -c "import py_compile; py_compile.compile('admin_bot/callbacks.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('admin_bot/__main__.py', doraise=True)"
```

### 5. Deploy and test
Use deploy-vps skill. Then trigger the command that shows the inline buttons and click one.

## Rules
- Always `await query.answer()` first — removes the loading spinner
- Callback data max 64 bytes — keep prefixes short
- Pattern must start with `^` for proper regex matching
- py_compile both files before deploying
- Never manually start admin_bot after deploy
