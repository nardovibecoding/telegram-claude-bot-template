#!/bin/bash
# Create the ClaudeGPT ProMax topic in Nardo AI World group
# Run on VPS where python-telegram-bot is installed
set -e

cd ~/telegram-claude-bot
source .env
source venv/bin/activate

python3 -c "
import os, asyncio
from telegram import Bot
async def create():
    token = os.environ['TELEGRAM_BOT_TOKEN_ADMIN']
    bot = Bot(token)
    topic = await bot.create_forum_topic(
        chat_id=-1003892866004, name='ClaudeGPT ProMax'
    )
    print(f'thread_id={topic.message_thread_id}')
asyncio.run(create())
"
