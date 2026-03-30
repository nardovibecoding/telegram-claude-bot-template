#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Auto-refresh Twitter cookies using Playwright persistent browser profile.

First run: opens a visible browser window — log in to x.com manually.
Subsequent runs: reuses the saved session, visits x.com, dumps fresh cookies.

Usage:
  python refresh_cookies.py          # refresh cookies (headless after first login)
  python refresh_cookies.py --login  # force visible browser for re-login
"""

import asyncio
import json
import sys
from pathlib import Path

COOKIES_FILE = Path(__file__).parent / "twitter_cookies.json"
PROFILE_DIR = Path(__file__).parent / ".playwright_profile"


async def refresh(force_login: bool = False):
    from playwright.async_api import async_playwright

    headless = not force_login and PROFILE_DIR.exists()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)

        if not headless:
            print("Browser opened. Log in to x.com if needed.")
            print("Waiting 120 seconds for you to log in...")
            # Wait for auth_token cookie to appear (poll every 3s, max 120s)
            for _ in range(40):
                await page.wait_for_timeout(3000)
                cookies = await context.cookies(["https://x.com", "https://twitter.com"])
                names = {c["name"] for c in cookies}
                if "auth_token" in names:
                    print("Login detected!")
                    break
            else:
                print("Timed out waiting for login.")
        else:
            await page.wait_for_timeout(5000)

        # Grab cookies from all relevant domains
        cookies = await context.cookies(["https://x.com", "https://twitter.com"])
        # Convert to twikit format: {name: value}
        cookie_dict = {c["name"]: c["value"] for c in cookies}

        print(f"Found {len(cookies)} cookies. Keys: {sorted(cookie_dict.keys())}")

        if "auth_token" not in cookie_dict or "ct0" not in cookie_dict:
            print("ERROR: Missing auth_token or ct0 — not logged in.")
            if headless:
                print("Run with --login to open browser and log in.")
            await context.close()
            return False

        # Atomic write
        tmp = COOKIES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cookie_dict, indent=2))
        tmp.replace(COOKIES_FILE)

        print(f"Cookies refreshed: {len(cookie_dict)} cookies saved to {COOKIES_FILE}")
        print(f"  auth_token: ...{cookie_dict['auth_token'][-8:]}")
        print(f"  ct0: ...{cookie_dict['ct0'][-8:]}")

        await context.close()
        return True


if __name__ == "__main__":
    force = "--login" in sys.argv
    ok = asyncio.run(refresh(force_login=force))
    sys.exit(0 if ok else 1)
