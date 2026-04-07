#!/usr/bin/env python3
# Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 — see LICENSE
"""Shared sanitization + privacy scanning for sync scripts.

Used by sync_public_repos.py and sync_template.py.
Strips known private identifiers, then runs canary checks for generic secrets.
"""
import re

# ── Strip patterns (applied before canary check) ────────────────────────
# Replace known private identifiers with safe placeholders.
STRIP_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"~/\b/?"), "~/"),
    (re.compile(r"~/\b/?"), "~/"),
    (re.compile(r"bernard@157\.180\.28\.14"), "<user>@<vps-ip>"),
    (re.compile(r"157\.180\.28\.14"), "<vps-ip>"),
    (re.compile(r"<github-user>"), "<github-user>"),
    (re.compile(r"bernard\.ngb@gmail\.com"), "<your-email>"),
    (re.compile(r"fromedwin@gmail\.com"), "<admin-email>"),
    (re.compile(r"stevie\.ong@mexc\.com"), "<mexc-email>"),
]

# ── Canary patterns (block file if matched after sanitization) ───────────
# Ordered from most specific to most generic.
_CANARY: list[tuple[re.Pattern, str]] = [
    # Residual personal paths (sanitization should have caught these)
    (re.compile(r"~/\b"), "personal Mac path"),
    (re.compile(r"~/\b"), "personal Linux path"),
    (re.compile(r"157\.180\.28\.14"), "VPS IP"),

    # Email addresses (any)
    (re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    ), "email address"),

    # Telegram bot token format: 10digits:35chars
    (re.compile(r"\d{8,12}:[A-Za-z0-9_-]{30,}"), "Telegram bot token"),

    # Generic API key / secret assignments with literal values
    # Matches: token = "abc123...", API_KEY: "sk-...", secret="xyz..."
    (re.compile(
        r'(?:token|api_key|apikey|api_secret|access_token|secret_key|'
        r'auth_token|client_secret|private_key|password|passwd)\s*'
        r'[=:]\s*["\'][^"\'${\s][^"\']{8,}["\']',
        re.IGNORECASE,
    ), "hardcoded secret assignment"),

    # OpenAI / Anthropic / common provider key formats
    (re.compile(r'\bsk-[A-Za-z0-9]{20,}'), "API key (sk- prefix)"),
    (re.compile(r'\bxai-[A-Za-z0-9]{20,}'), "API key (xai- prefix)"),
    (re.compile(r'\bsk-ant-[A-Za-z0-9\-]{20,}'), "Anthropic key"),

    # Generic high-entropy strings assigned to variables
    # e.g. FOO = "aB3kP9mQwXzR7nL2vY5tH8cJ1dG6eI4s"  (32+ alphanum, no spaces)
    (re.compile(
        r'=\s*["\'][A-Za-z0-9+/]{32,}["\']'
    ), "high-entropy string literal"),

    # Connection strings
    (re.compile(
        r'(?:mongodb|postgres|postgresql|mysql|redis|amqp)://'
        r'[^@\s]+:[^@\s]+@',
        re.IGNORECASE,
    ), "database connection string"),

    # SSH private key markers
    (re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
     "SSH/TLS private key"),

    # Webhook URLs with likely token segments (path component 30+ chars)
    # Exclude GitHub commit/blob/tree URLs (hashes are expected).
    (re.compile(
        r'https?://(?!github\.com/[^\s"\']+/(?:commit|blob|tree|pull)/)'
        r'[^\s"\']+/[A-Za-z0-9_\-]{30,}[^\s"\']*'
    ), "URL with token-like path"),
]


def sanitize(content: str) -> str:
    """Apply strip patterns to replace known private identifiers."""
    for pattern, replacement in STRIP_PATTERNS:
        content = pattern.sub(replacement, content)
    return content


# ── False-positive allowlist ─────────────────────────────────────────────
# (filename_substring, canary_label) pairs that are known safe.
_ALLOW: list[tuple[str, str]] = [
    ("sanitize.py", "personal Mac path"),      # regex definitions reference paths
    ("sanitize.py", "personal Linux path"),
    ("sanitize.py", "hardcoded secret assignment"),  # example patterns in comments
    ("sanitize.py", "high-entropy string literal"),
    ("feedback_loop.py", "hardcoded secret assignment"),  # env parsing logic
    ("test_", "hardcoded secret assignment"),   # test fixtures use dummy tokens
]

# Example/placeholder emails that are safe to publish.
_SAFE_EMAILS = re.compile(
    r"(?:john|jane|user|example|test|admin|noreply|no-reply)"
    r"@(?:example\.com|test\.com|localhost)",
    re.IGNORECASE,
)


def check_privacy(content: str, filename: str) -> list[str]:
    """Return list of violation strings if private content is detected."""
    violations = []
    for pattern, label in _CANARY:
        match = pattern.search(content)
        if match:
            # Check allowlist
            if any(fn in filename and label == al
                   for fn, al in _ALLOW):
                continue
            # Skip known-safe example emails
            if label == "email address" and _SAFE_EMAILS.match(match.group(0)):
                continue
            snippet = match.group(0)[:60].replace("\n", " ")
            violations.append(f"{filename}: {label} — `{snippet}`")
    return violations
