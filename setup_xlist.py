# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Create a private Twitter list ("xcurate") and add all followed accounts to it.

Usage:
    python setup_xlist.py --create          # create list + add all accounts
    python setup_xlist.py --add             # add missing accounts to existing list
    python setup_xlist.py --list-id <id>    # use existing list instead of creating

The list ID is saved to xlist_config.json for x_curator.py to use.
"""

import asyncio
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

COOKIES_FILE   = str(Path(__file__).parent / "twitter_cookies.json")
FOLLOWING_CACHE = str(Path(__file__).parent / "twitter_following_cache.json")
XLIST_CONFIG   = str(Path(__file__).parent / "xlist_config.json")

ADD_BATCH      = 20   # members to add before pausing
ADD_PAUSE      = 60   # seconds between batches


async def get_client():
    from twikit import Client
    client = Client("en-US")
    with open(COOKIES_FILE) as f:
        raw = json.load(f)
    if isinstance(raw, list):
        cookies = {c["name"]: c["value"] for c in raw}
    else:
        cookies = raw
    client.set_cookies(cookies)
    print("Cookies loaded.")
    return client


def load_following() -> list[dict]:
    with open(FOLLOWING_CACHE) as f:
        data = json.load(f)
    accounts = data.get("following", [])
    print(f"Loaded {len(accounts)} accounts from cache.")
    return accounts


def load_config() -> dict:
    if Path(XLIST_CONFIG).exists():
        with open(XLIST_CONFIG) as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    with open(XLIST_CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Config saved to {XLIST_CONFIG}")


async def create_list(client) -> str:
    """Create a private list and return its ID."""
    lst = await client.create_list(
        name="xcurate",
        description="Daily alpha feed — all followed accounts",
        is_private=True,
    )
    list_id = lst.id
    print(f"Created list 'xcurate' with ID: {list_id}")
    return str(list_id)


async def add_members(client, list_id: str, accounts: list[dict], existing_ids: set[str] = None):
    """Add all accounts to the list, skipping already-added ones."""
    if existing_ids is None:
        existing_ids = set()

    to_add = [a for a in accounts if str(a.get("id", "")) not in existing_ids]
    print(f"Accounts to add: {len(to_add)} (skipping {len(accounts) - len(to_add)} already present)")

    added   = 0
    failed  = 0
    stalled = 0
    for i, acc in enumerate(to_add, 1):
        uid    = str(acc.get("id", ""))
        handle = acc.get("screen_name", "?")
        if not uid:
            continue
        try:
            await asyncio.wait_for(client.add_list_member(list_id, uid), timeout=15)
            added += 1
        except asyncio.TimeoutError:
            stalled += 1
            print(f"  TIMEOUT @{handle} — rate limited, stopping this batch")
            break
        except Exception as e:
            print(f"  FAIL @{handle}: {e}")
            failed += 1

        print(f"  [{i}/{len(to_add)}] @{handle} added={added} failed={failed}")

        if i % ADD_BATCH == 0 and i < len(to_add):
            print(f"  Pausing {ADD_PAUSE}s (rate limit)...")
            await asyncio.sleep(ADD_PAUSE)
        else:
            await asyncio.sleep(0.3)

    print(f"\nDone. Added: {added}  Failed: {failed}  Stalled: {stalled}")
    return added


async def get_list_member_ids(client, list_id: str) -> set[str]:
    """Fetch current member IDs of the list (paginated)."""
    members = set()
    cursor = None
    while True:
        try:
            batch = await client.get_list_members(list_id, count=200, cursor=cursor)
        except Exception as e:
            print(f"Warning: could not fetch existing members: {e}")
            break
        if not batch:
            break
        for m in batch:
            members.add(str(m.id))
        cursor = getattr(batch, "next_cursor", None)
        if not cursor:
            break
        await asyncio.sleep(1)
    print(f"Existing list members: {len(members)}")
    return members


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--create",  action="store_true", help="Create new list + add all accounts")
    parser.add_argument("--add",     action="store_true", help="Add missing accounts to existing list")
    parser.add_argument("--list-id", help="Use this list ID instead of creating")
    args = parser.parse_args()

    if not args.create and not args.add and not args.list_id:
        parser.print_help()
        return

    client   = await get_client()
    accounts = load_following()
    cfg      = load_config()

    if args.list_id:
        cfg["list_id"] = args.list_id
        save_config(cfg)
        print(f"Saved list ID {args.list_id} to config.")

    if args.create:
        if cfg.get("list_id"):
            print(f"List already exists: {cfg['list_id']}. Use --add to add missing members.")
            return
        list_id = await create_list(client)
        cfg["list_id"] = list_id
        save_config(cfg)
        await add_members(client, list_id, accounts)

    elif args.add or args.list_id:
        list_id = cfg.get("list_id")
        if not list_id:
            print("No list ID found. Run with --create first or provide --list-id <id>.")
            return
        print(f"Using list ID: {list_id}")
        existing = await get_list_member_ids(client, list_id)
        await add_members(client, list_id, accounts, existing_ids=existing)

    print("\nAll done. x_curator.py will now use this list for daily digest.")


if __name__ == "__main__":
    asyncio.run(main())
