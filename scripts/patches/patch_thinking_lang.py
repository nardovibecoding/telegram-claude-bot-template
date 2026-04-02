# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Fix thinking indicator language — detect user's language."""
import os
PATH = os.path.expanduser("~/telegram-claude-bot-template/bot.py")

with open(PATH) as f:
    code = f.read()

# Replace static "思考中..." with language-aware version
code = code.replace(
    '    thinking_msg = await update.message.reply_text("思考中...")',
    '    # Detect language for thinking indicator\n'
    '    _has_cn = any("\\u4e00" <= c <= "\\u9fff" for c in text)\n'
    '    _thinking_text = "思考中..." if _has_cn else "Thinking..."\n'
    '    thinking_msg = await update.message.reply_text(_thinking_text)'
)

with open(PATH, 'w') as f:
    f.write(code)

import py_compile
py_compile.compile(PATH, doraise=True)
print("OK — language-aware thinking indicator")
