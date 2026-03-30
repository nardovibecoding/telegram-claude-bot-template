# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
content_broadcaster.py — Adapt one content piece for multiple platforms.
Usage: from content_broadcaster import adapt_content
"""

PLATFORM_RULES = {
    "twitter": {
        "max_chars": 280,
        "hashtags": "1-3, inline or end",
        "format": "punchy, hook in first line, thread if long",
        "no": "long paragraphs"
    },
    "xhs": {  # xiaohongshu
        "max_chars": 1000,
        "hashtags": "5-10 Chinese tags at end, format: #tag#",
        "format": "visual-first, Chinese, emoji-friendly, personal tone, beauty/lifestyle angle",
        "no": "political content, competitor mentions"
    },
    "reddit": {
        "max_chars": 40000,
        "hashtags": "none",
        "format": "descriptive title, substantive body, subreddit-matched tone, markdown ok",
        "no": "hashtags, promotional language, self-promotion without disclosure"
    },
    "telegram": {
        "max_chars": 4096,
        "hashtags": "optional, 1-3",
        "format": "conversational, markdown supported, can be longer",
        "no": "none"
    },
    "linkedin": {
        "max_chars": 3000,
        "hashtags": "3-5 footer hashtags",
        "format": "professional, thought leadership, first line is hook",
        "no": "casual slang, controversial"
    }
}

ADAPT_PROMPT_TEMPLATE = """You are a content adapter. Adapt the following content for {platform}.

Platform rules for {platform}:
- Max chars: {max_chars}
- Hashtags: {hashtags}
- Format: {format}
- Avoid: {no}

Original content:
{content}

Output ONLY the adapted content for {platform}. No explanation."""


async def adapt_content(content: str, platforms: list, claude_client=None) -> dict:
    """
    Adapt content for multiple platforms.
    Returns dict: {platform: adapted_content}
    If claude_client is None, returns rule-based truncation only.
    """
    results = {}

    for platform in platforms:
        if platform not in PLATFORM_RULES:
            results[platform] = content
            continue

        rules = PLATFORM_RULES[platform]

        if claude_client:
            prompt = ADAPT_PROMPT_TEMPLATE.format(
                platform=platform,
                content=content,
                **rules
            )
            # Use caller's claude client to adapt
            results[platform] = await claude_client.adapt(prompt)
        else:
            # Fallback: truncate to max chars
            max_c = rules["max_chars"]
            results[platform] = content[:max_c] if len(content) > max_c else content

    return results


def get_platform_rules(platform: str) -> dict:
    """Get rules for a specific platform."""
    return PLATFORM_RULES.get(platform, {})


def list_platforms() -> list:
    """List all supported platforms."""
    return list(PLATFORM_RULES.keys())
