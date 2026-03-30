# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Entry point: python run_bot.py <persona_id>"""
import sys
from bot_base import run_persona

if __name__ == "__main__":
    persona_id = sys.argv[1] if len(sys.argv) > 1 else "bot1"
    run_persona(persona_id)
