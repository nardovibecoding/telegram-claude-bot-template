# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Add cancel-and-restart to bot.py — if new message arrives during processing, restart."""
import re

import os
PATH = os.path.expanduser("~/telegram-claude-bot-template/bot.py")

with open(PATH) as f:
    code = f.read()

# 1. Add tracking dicts after MAX_INPUT_LEN
code = code.replace(
    'MAX_INPUT_LEN = 5000',
    'MAX_INPUT_LEN = 5000\n\n'
    '# Cancel-and-restart: track active Claude process per user\n'
    '_active_task = {}  # user_id -> asyncio.Task\n'
    '_queued_msgs = {}  # user_id -> [str, ...]'
)

# 2. Find handle_text and wrap it
# Strategy: rename current handle_text to _process_text
# New handle_text does: cancel existing task, queue message, start new task

old_def = 'async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):'
code = code.replace(old_def, 'async def _process_text(update: Update, context: ContextTypes.DEFAULT_TYPE, combined_text: str = None):')

# Change `text = (update.message.text or "").strip()` to use combined_text if provided
code = code.replace(
    '    text = (update.message.text or "").strip()\n'
    '    if not text:\n'
    '        return\n'
    '    if len(text) > MAX_INPUT_LEN:',
    '    text = combined_text or (update.message.text or "").strip()\n'
    '    if not text:\n'
    '        return\n'
    '    if len(text) > MAX_INPUT_LEN:'
)

# 3. Add new handle_text wrapper before _process_text
new_handler = '''@_user_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel-and-restart: if processing, kill and combine messages."""
    text = (update.message.text or "").strip()
    if not text:
        return
    user_id = update.effective_user.id

    # If already processing, cancel and queue
    if user_id in _active_task and not _active_task[user_id].done():
        _active_task[user_id].cancel()
        _queued_msgs.setdefault(user_id, []).append(text)
        await update.effective_chat.send_action("typing")
        return

    # Combine any queued messages
    queued = _queued_msgs.pop(user_id, [])
    if queued:
        combined = chr(10).join(queued + [text])
    else:
        combined = text

    # Start processing as a task (so it can be cancelled)
    _active_task[user_id] = asyncio.create_task(
        _process_text(update, context, combined_text=combined)
    )
    try:
        await _active_task[user_id]
    except asyncio.CancelledError:
        pass  # Cancelled by new message — _process_text will restart
    finally:
        _active_task.pop(user_id, None)
        # If messages queued while we were cancelled, process them
        if user_id in _queued_msgs and _queued_msgs[user_id]:
            remaining = chr(10).join(_queued_msgs.pop(user_id))
            _active_task[user_id] = asyncio.create_task(
                _process_text(update, context, combined_text=remaining)
            )
            try:
                await _active_task[user_id]
            except asyncio.CancelledError:
                pass
            finally:
                _active_task.pop(user_id, None)


'''

# Insert before _process_text
code = code.replace(
    'async def _process_text(update: Update',
    new_handler + 'async def _process_text(update: Update'
)

# Remove duplicate @_user_only on _process_text (it's on handle_text now)
code = code.replace(
    '@_user_only\nasync def _process_text',
    'async def _process_text'
)

with open(PATH, 'w') as f:
    f.write(code)

import py_compile
py_compile.compile(PATH, doraise=True)
print("OK — cancel-and-restart added, syntax verified")
