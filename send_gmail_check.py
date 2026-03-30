#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Daily Gmail check — reads inbox via Google API, uses MiniMax to summarize,
posts to TG Team E Email thread."""
import asyncio
import base64
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from email.utils import parseaddr
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from openai import OpenAI

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("gmail_check")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
CHAT_ID = int(os.environ.get("TEAM_E_GROUP_ID", "0"))   # Team E
THREAD_ID = 2              # Email topic
HKT = timezone(timedelta(hours=8))
SENT_FLAG = str(BASE_DIR / ".gmail_check_sent")
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_FILE = str(BASE_DIR / "gmail_token.json")
CREDS_FILE = str(BASE_DIR / "gmail_credentials.json")

# Contacts to check
CONTACT_RULES = [
    # Add your contacts here. Each entry: name, Gmail query, and filter instructions.
    # Examples:
    # {"name": "Alice", "query": "from:alice@example.com newer_than:1d is:inbox", "filter": "personal only, not automated notifications"},
    # {"name": "Banks", "query": "(from:bank OR from:hsbc) newer_than:1d is:inbox", "filter": "only transactional emails requiring response"},
]


def get_gmail_service():
    """Get authenticated Gmail API service."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        else:
            log.error("Gmail token invalid and can't refresh — need re-auth")
            return None
    return build("gmail", "v1", credentials=creds)


def fetch_emails(service, query: str, max_results: int = 10) -> list[dict]:
    """Fetch emails matching query, return list of {from, subject, snippet, body_preview}."""
    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    except Exception as e:
        log.error("Gmail search failed for %s: %s", query, e)
        return []

    messages = results.get("messages", [])
    emails = []
    for m in messages:
        try:
            msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}

            # Extract body
            body = ""
            payload = msg["payload"]
            if "parts" in payload:
                for part in payload["parts"]:
                    if part["mimeType"] == "text/plain" and "data" in part.get("body", {}):
                        body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                        break
            elif "data" in payload.get("body", {}):
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

            emails.append({
                "from": headers.get("From", "?"),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "body_preview": body[:500] if body else msg.get("snippet", ""),
            })
        except Exception as e:
            log.warning("Failed to read message %s: %s", m["id"], e)

    return emails


def summarize_with_ai(all_emails: dict[str, list]) -> str:
    """Use MiniMax to determine which emails need replies."""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        # Fallback: simple text summary without AI
        return _simple_summary(all_emails)

    # Build prompt
    email_text = ""
    for contact, emails in all_emails.items():
        if not emails:
            continue
        email_text += f"\n### {contact}\n"
        for e in emails:
            email_text += f"- From: {e['from']}\n  Subject: {e['subject']}\n  Preview: {e['body_preview'][:200]}\n\n"

    if not email_text.strip():
        return "All clear — no emails from key contacts in the last 24 hours."

    from utils import strip_think
    client = OpenAI(api_key=api_key, base_url="https://api.minimaxi.com/v1")
    try:
        resp = client.chat.completions.create(
            model="MiniMax-M2.5-highspeed",
            messages=[
                {"role": "system", "content": (
                    "You analyze emails and determine which ones need a personal reply. "
                    "Filter out automated notifications, promos, newsletters, mass mailings, OTPs, regulatory notices. "
                    "Only flag emails where a real person expects a response. "
                    "Reply in concise format: sender, subject, one-line summary. "
                    "If nothing needs a reply, say so clearly."
                )},
                {"role": "user", "content": f"Check these emails and tell me which need a reply:\n{email_text}"},
            ],
            max_tokens=1000,
        )
        result = resp.choices[0].message.content or ""
        return strip_think(result)
    except Exception as e:
        log.error("MiniMax summary failed: %s", e)
        return _simple_summary(all_emails)


def _simple_summary(all_emails: dict[str, list]) -> str:
    """Fallback summary without AI."""
    lines = []
    for contact, emails in all_emails.items():
        if emails:
            lines.append(f"<b>{contact}</b>: {len(emails)} email(s)")
            for e in emails:
                lines.append(f"  • {e['subject']} — from {e['from']}")
    if not lines:
        return "All clear — no emails from key contacts in the last 24 hours."
    return "\n".join(lines)


async def main():
    today = datetime.now(HKT).strftime("%Y-%m-%d")

    if os.path.exists(SENT_FLAG):
        with open(SENT_FLAG) as f:
            if today in f.read():
                log.info("Already sent for %s", today)
                return

    log.info("Running Gmail check...")
    service = get_gmail_service()
    if not service:
        log.error("Cannot get Gmail service")
        return

    # Fetch emails for each contact
    all_emails = {}
    for rule in CONTACT_RULES:
        emails = fetch_emails(service, rule["query"])
        if emails:
            all_emails[rule["name"]] = emails
            log.info("%s: %d emails", rule["name"], len(emails))
        else:
            log.info("%s: no emails", rule["name"])

    # Summarize
    result = summarize_with_ai(all_emails)

    # Send to TG
    from telegram import Bot
    now_str = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    try:
        async with Bot(token=BOT_TOKEN) as bot:
            await bot.send_message(
                chat_id=CHAT_ID,
                message_thread_id=THREAD_ID,
                text=f"📧 <b>Gmail Check — {now_str}</b>\n\n{result[:3800]}",
                parse_mode="HTML",
            )
        log.info("Gmail check sent to TG")

        with open(SENT_FLAG, "a") as f:
            f.write(today + "\n")

    except Exception as e:
        log.error("TG send failed: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
