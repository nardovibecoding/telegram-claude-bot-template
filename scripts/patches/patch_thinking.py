# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Add thinking indicator to bot handle_text."""
import os
PATH = os.path.expanduser("~/telegram-claude-bot-template/bot.py")

with open(PATH) as f:
    code = f.read()

# Replace typing action with thinking message
code = code.replace(
    '    await update.effective_chat.send_action("typing")\n'
    '    _add_message(USER_ID, "user", text)',
    '    thinking_msg = await update.message.reply_text("思考中...")\n'
    '    _add_message(USER_ID, "user", text)'
)

# After response is sent, delete the thinking message
# Find where chunks are sent
code = code.replace(
    '    # Send reply (split if too long)\n'
    '    chunks = split_message(reply)',
    '    # Delete thinking indicator\n'
    '    try:\n'
    '        await thinking_msg.delete()\n'
    '    except Exception:\n'
    '        pass\n\n'
    '    # Send reply (split if too long)\n'
    '    chunks = split_message(reply)'
)

with open(PATH, 'w') as f:
    f.write(code)

import py_compile
py_compile.compile(PATH, doraise=True)
print("OK — thinking indicator added")
