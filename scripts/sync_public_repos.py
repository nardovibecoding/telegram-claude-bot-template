#!/usr/bin/env python3
# Copyright (c) 2026 Nardo (nardovibecoding). AGPL-3.0 — see LICENSE
"""Sync private telegram-claude-bot files to public GitHub repos.

Full pipeline: code files + README counts + GitHub description + topics + license.

Usage:
    python scripts/sync_public_repos.py              # check what's stale
    python scripts/sync_public_repos.py --sync       # full sync + push
    python scripts/sync_public_repos.py --sync guard  # sync only guard repo
    python scripts/sync_public_repos.py --dry        # preview without pushing
"""
import argparse
import glob as _glob
import os
import re
import subprocess
from pathlib import Path

PRIVATE_ROOT = Path(__file__).parent.parent  # telegram-claude-bot/
PUBLIC_ROOT = Path.home()  # public repos cloned alongside
_GH_USER = "nardovibecoding"
_COPYRIGHT = "# Copyright (c) 2026 Nardo (nardovibecoding). AGPL-3.0 — see LICENSE"

# ── Repo configs ────────────────────────────────────────────────────────

SYNC_MAP = {
    "claude-security-guard": {
        "repo": "claude-security-guard",
        "clone_dir": PUBLIC_ROOT / "claude-security-guard",
        "files": {
            "hooks/guard_safety.py": "hooks/guard_safety.py",
            "hooks/auto_test_after_edit.py": "hooks/auto_test_after_edit.py",
            "hooks/auto_review_before_done.py": "hooks/auto_review_before_done.py",
            "hooks/auto_dependency_grep.py": "hooks/auto_dependency_grep.py",
            "hooks/auto_copyright_header.py": "hooks/auto_copyright_header.py",
            "hooks/auto_license.py": "hooks/auto_license.py",
            "hooks/auto_repo_check.py": "hooks/auto_repo_check.py",
            "hooks/auto_pre_publish.py": "hooks/auto_pre_publish.py",
            "hooks/verify_infra.py": "hooks/verify_infra.py",
            "hooks/unicode_grep_warn.py": "hooks/unicode_grep_warn.py",
            "hooks/skill_disable_not_delete.py": "hooks/skill_disable_not_delete.py",
            "hooks/pre_commit_validate.py": "hooks/pre_commit_validate.py",
            "hooks/reasoning_leak_canary.py": "hooks/reasoning_leak_canary.py",
            "hooks/auto_hook_deploy.py": "hooks/auto_hook_deploy.py",
            "hooks/hook_base.py": "hooks/hook_base.py",
            "hooks/test_helpers.py": "hooks/test_helpers.py",
            "hooks/deploy_hooks.sh": "hooks/deploy_hooks.sh",
            "hooks/pull_and_deploy.sh": "hooks/pull_and_deploy.sh",
            "hooks/platform_filter.json": "hooks/platform_filter.json",
            "hooks/hardcoded_model_guard.py": "hooks/hardcoded_model_guard.py",
            "hooks/async_safety_guard.py": "hooks/async_safety_guard.py",
            "hooks/resource_leak_guard.py": "hooks/resource_leak_guard.py",
            "hooks/tg_security_guard.py": "hooks/tg_security_guard.py",
            "hooks/tg_api_guard.py": "hooks/tg_api_guard.py",
            "hooks/admin_only_guard.py": "hooks/admin_only_guard.py",
        },
        "readme": {
            # Badge patterns to update with actual count
            "badge_patterns": [
                (r"hooks-\d+-orange", "hooks-{count}-orange"),
            ],
            # Section headers to update
            "header_patterns": [
                (r"## Hooks \(\d+\)", "## Hooks ({count})"),
            ],
            # What to count
            "count_glob": "hooks/*.py",
            # Files to exclude from count (not hooks)
            "count_exclude": [
                "hook_base.py", "test_helpers.py",
                "vps_config.py",
            ],
        },
        "description": (
            "Claude Code plugin — {hook_count} hooks + "
            "28 MCP tools for enforcement, security scanning, "
            "prompt injection defense, VPS ops, audit logging"
        ),
        "topics": [
            "claude-code", "claude-code-plugin", "mcp",
            "mcp-server", "automation", "devops", "vibecoding",
            "python-hooks", "audit-log", "developer-tools",
            "hooks", "prompt-injection", "python", "security",
        ],
        "license": "AGPL-3.0",
    },
    "claude-social-pipeline": {
        "repo": "claude-social-pipeline",
        "clone_dir": PUBLIC_ROOT / "claude-social-pipeline",
        "files": {
            "skills/x-tweet/tweet_stats.py": "tools/tweet_stats.py",
        },
        "description": (
            "Claude Code plugin — 6 MCP tools + 2 skills "
            "for developer content workflow: capture insights "
            "while coding, draft tweets, track engagement"
        ),
        "topics": [
            "claude-code", "claude-code-plugin",
            "content-pipeline", "mcp", "skills", "twitter",
            "vibecoding", "x-tweet", "developer-tools", "x-api",
        ],
        "license": "AGPL-3.0",
    },
}

# ── Sanitization ────────────────────────────────────────────────────────

_STRIP_PATTERNS = [
    (re.compile(r"/Users/[^/]+/telegram-claude-bot/"), "~/telegram-claude-bot/"),
    (re.compile(r"/home/[^/]+/telegram-claude-bot/"), "~/telegram-claude-bot/"),
    (re.compile(r"\w+@\d+\.\d+\.\d+\.\d+"), "<user>@<vps-ip>"),
    (re.compile(r"nardovibecoding"), "<github-user>"),
]

_PRIVATE_CANARY = [
    re.compile(r"/Users/\w+/telegram-claude-bot"),
    re.compile(r"/home/\w+/telegram-claude-bot"),
    re.compile(r"\d+\.\d+\.\d+\.\d+"),
    re.compile(r"YOUR_EMAIL@gmail"),
    re.compile(r"YOUR_BOT_EMAIL@gmail"),
    re.compile(r"personal\.name@company"),
    re.compile(r"TELEGRAM_BOT_TOKEN_\w+"),
    re.compile(r"MINIMAX_API_KEY|KIMI_API_KEY|DEEPSEEK_API_KEY"),
    re.compile(r"GROQ_API_KEY|CEREBRAS_API_KEY|GEMINI_API_KEY"),
]


def _sanitize(content: str) -> str:
    for pattern, replacement in _STRIP_PATTERNS:
        content = pattern.sub(replacement, content)
    return content


def _check_privacy(content: str, filename: str) -> list[str]:
    violations = []
    for pattern in _PRIVATE_CANARY:
        matches = pattern.findall(content)
        if matches:
            violations.append(f"  {filename}: leaked `{matches[0]}`")
    return violations


# ── README auto-update ──────────────────────────────────────────────────

def _count_hooks(clone_dir: Path, cfg: dict) -> int:
    """Count actual hook files in the public repo."""
    readme_cfg = cfg.get("readme")
    if not readme_cfg:
        return 0
    count_glob = readme_cfg.get("count_glob", "hooks/*.py")
    exclude = set(readme_cfg.get("count_exclude", []))
    matched = _glob.glob(str(clone_dir / count_glob))
    return sum(1 for f in matched
               if Path(f).name not in exclude
               and not Path(f).name.startswith("__"))


def _update_readme(clone_dir: Path, cfg: dict, dry_run: bool) -> bool:
    """Update README badge counts and section headers. Returns True if changed."""
    readme_cfg = cfg.get("readme")
    if not readme_cfg:
        return False

    readme_path = clone_dir / "README.md"
    if not readme_path.exists():
        return False

    count = _count_hooks(clone_dir, cfg)
    if count == 0:
        return False

    content = readme_path.read_text()
    original = content

    # Update badge patterns
    for pattern, template in readme_cfg.get("badge_patterns", []):
        replacement = template.format(count=count)
        content = re.sub(pattern, replacement, content)

    # Update section headers
    for pattern, template in readme_cfg.get("header_patterns", []):
        replacement = template.format(count=count)
        content = re.sub(pattern, replacement, content)

    if content == original:
        return False

    if dry_run:
        print(f"    WOULD UPDATE README.md (hook count → {count})")
    else:
        readme_path.write_text(content)
        print(f"    UPDATED README.md (hook count → {count})")
    return True


# ── License verification ────────────────────────────────────────────────

def _verify_license(clone_dir: Path, cfg: dict) -> list[str]:
    """Check LICENSE, NOTICE, and copyright headers. Returns warnings."""
    warnings = []
    license_type = cfg.get("license", "AGPL-3.0")

    # Check LICENSE file
    license_file = clone_dir / "LICENSE"
    if not license_file.exists():
        warnings.append("  LICENSE file missing")
    elif license_type not in license_file.read_text()[:200]:
        warnings.append(f"  LICENSE doesn't mention {license_type}")

    # Check NOTICE file
    notice_file = clone_dir / "NOTICE"
    if not notice_file.exists():
        warnings.append("  NOTICE file missing")

    # Check copyright headers on .py files
    missing_header = []
    for py in _glob.glob(str(clone_dir / "**/*.py"), recursive=True):
        try:
            first_lines = Path(py).read_text()[:200]
            if "Copyright" not in first_lines:
                missing_header.append(Path(py).name)
        except Exception:
            pass
    if missing_header:
        warnings.append(
            f"  Missing copyright header: "
            f"{', '.join(missing_header[:5])}"
            + (f" +{len(missing_header)-5} more"
               if len(missing_header) > 5 else ""))

    return warnings


# ── GitHub metadata ─────────────────────────────────────────────────────

def _update_github_meta(cfg: dict, hook_count: int, dry_run: bool):
    """Update GitHub repo description and topics."""
    repo = cfg["repo"]
    full_repo = f"{_GH_USER}/{repo}"

    # Update description
    desc_tpl = cfg.get("description")
    if desc_tpl:
        desc = desc_tpl.format(hook_count=hook_count)
        if dry_run:
            print(f"    WOULD SET description: {desc[:80]}...")
        else:
            subprocess.run(
                ["gh", "repo", "edit", full_repo,
                 "--description", desc],
                capture_output=True, timeout=15)
            print(f"    SET description: {desc[:80]}...")

    # Update topics
    topics = cfg.get("topics")
    if topics:
        topics_json = ",".join(topics)
        if dry_run:
            print(f"    WOULD SET {len(topics)} topics")
        else:
            # gh api requires JSON array
            import json
            subprocess.run(
                ["gh", "api", f"repos/{full_repo}/topics",
                 "-X", "PUT",
                 "-f", f"names={json.dumps(topics)}"],
                capture_output=True, timeout=15)
            print(f"    SET {len(topics)} topics")


# ── Core sync ───────────────────────────────────────────────────────────

def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0


def check_staleness(target: str = None):
    """Check which public repo files are behind the private source."""
    stale = {}
    for name, cfg in SYNC_MAP.items():
        if target and name != target:
            continue
        clone_dir = cfg["clone_dir"]
        if not clone_dir.exists():
            print(f"  {name}: NOT CLONED at {clone_dir}")
            continue
        repo_stale = []
        for src_rel, dst_rel in cfg["files"].items():
            src = PRIVATE_ROOT / src_rel
            dst = clone_dir / dst_rel
            if not src.exists():
                continue
            if not dst.exists():
                repo_stale.append((src_rel, "MISSING"))
            elif _file_mtime(src) > _file_mtime(dst):
                repo_stale.append((src_rel, "STALE"))
        if repo_stale:
            stale[name] = repo_stale
            print(f"\n  {name}: {len(repo_stale)} files need sync")
            for f, status in repo_stale:
                print(f"    [{status}] {f}")
        else:
            print(f"  {name}: up to date")
    return stale


def sync_repo(name: str, cfg: dict, dry_run: bool = False):
    """Full sync: files + README + license + description + topics."""
    clone_dir = cfg["clone_dir"]
    if not clone_dir.exists():
        print(f"  Cloning {cfg['repo']}...")
        subprocess.run(
            ["gh", "repo", "clone",
             f"{_GH_USER}/{cfg['repo']}", str(clone_dir)],
            check=True)

    # Pull latest
    subprocess.run(
        ["git", "-C", str(clone_dir), "pull", "--ff-only"],
        capture_output=True)

    # ── Step 1: Copy + sanitize + privacy check ──
    copied = 0
    for src_rel, dst_rel in cfg["files"].items():
        src = PRIVATE_ROOT / src_rel
        dst = clone_dir / dst_rel
        if not src.exists():
            print(f"    SKIP {src_rel} (not found)")
            continue

        content = src.read_text()
        sanitized = _sanitize(content)

        violations = _check_privacy(sanitized, src_rel)
        if violations:
            print(f"    BLOCKED {src_rel} — private content:")
            for v in violations:
                print(f"      {v}")
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.read_text() == sanitized:
            continue

        if dry_run:
            print(f"    WOULD COPY {src_rel} → {dst_rel}")
        else:
            dst.write_text(sanitized)
            if os.access(src, os.X_OK):
                os.chmod(dst, 0o755)
            print(f"    COPIED {src_rel} → {dst_rel}")
        copied += 1

    # ── Step 2: Update README counts/badges ──
    readme_changed = _update_readme(clone_dir, cfg, dry_run)

    # ── Step 3: Verify license ──
    license_warns = _verify_license(clone_dir, cfg)
    if license_warns:
        print(f"    LICENSE warnings:")
        for w in license_warns:
            print(f"      {w}")

    if copied == 0 and not readme_changed:
        print(f"  {name}: nothing to sync")
        # Still update GitHub meta if needed
        hook_count = _count_hooks(clone_dir, cfg)
        _update_github_meta(cfg, hook_count, dry_run)
        return

    if dry_run:
        total = copied + (1 if readme_changed else 0)
        print(f"  {name}: {total} files would be synced")
        return

    # ── Step 4: Commit + push ──
    subprocess.run(
        ["git", "-C", str(clone_dir), "add", "-A"],
        check=True)
    result = subprocess.run(
        ["git", "-C", str(clone_dir), "diff",
         "--cached", "--quiet"])
    if result.returncode == 0:
        print(f"  {name}: no changes to commit")
    else:
        hook_count = _count_hooks(clone_dir, cfg)
        subprocess.run(
            ["git", "-C", str(clone_dir), "commit", "-m",
             f"sync: {copied} files updated, {hook_count} hooks"],
            check=True)
        subprocess.run(
            ["git", "-C", str(clone_dir), "push"],
            check=True)
        print(f"  {name}: pushed ({copied} files)")

    # ── Step 5: Update GitHub description + topics ──
    hook_count = _count_hooks(clone_dir, cfg)
    _update_github_meta(cfg, hook_count, dry_run)


def main():
    parser = argparse.ArgumentParser(
        description="Sync private files to public repos")
    parser.add_argument(
        "--sync", action="store_true",
        help="Full sync: copy + README + description + push")
    parser.add_argument(
        "--dry", action="store_true",
        help="Preview what would change")
    parser.add_argument(
        "repo", nargs="?",
        help="Sync only this repo (e.g. 'claude-security-guard')")
    args = parser.parse_args()

    if not args.sync:
        print("Checking public repo staleness...\n")
        stale = check_staleness(args.repo)
        if stale:
            print("\nRun with --sync to update"
                  " (or --dry to preview)")
        return

    for name, cfg in SYNC_MAP.items():
        if args.repo and name != args.repo:
            continue
        print(f"\nSyncing {name}...")
        sync_repo(name, cfg, dry_run=args.dry)


if __name__ == "__main__":
    main()
