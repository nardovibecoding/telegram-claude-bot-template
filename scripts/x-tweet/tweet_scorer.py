#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""tweet_scorer.py — scores tweet drafts 0-100 for @<github-user>.

Redesigned scoring: each dimension maps to a real engagement driver.
No filler dimensions. Every point earned means something.

Usage:
    python tweet_scorer.py "Your tweet text here"
    python tweet_scorer.py "Your tweet text" --json
    echo "tweet text" | python tweet_scorer.py -
"""

import sys
import re
import json
import argparse
from pathlib import Path

# Import humanizer for AI detection (broader word list + hedging phrases)
# humanizer_scorer.py co-located on VPS, sibling skill on Mac
_mac_humanizer = Path(__file__).resolve().parent.parent.parent / "content-humanizer" / "scripts"
_vps_humanizer = Path(__file__).resolve().parent
sys.path.insert(0, str(_mac_humanizer if _mac_humanizer.exists() else _vps_humanizer))
from humanizer_scorer import score_humanity

# ── Dynamic weights from style_patterns.json ─────────────────────────────

_style_a = Path(__file__).parent / "data" / "style_patterns.json"
_style_b = Path(__file__).parent.parent / "data" / "style_patterns.json"
STYLE_PATH = _style_a if _style_a.exists() else _style_b

DEFAULT_WEIGHTS = {
    "hook_weight": 25,
    "specificity_weight": 20,
    "voice_weight": 20,
    "rhythm_weight": 15,
    "stranger_weight": 10,
    "length_weight": 10,
    "length_sweet_spot": [180, 270],
}


def load_weights() -> dict:
    """Load scorer weights from style_patterns.json, fall back to defaults."""
    if STYLE_PATH.exists():
        try:
            data = json.loads(STYLE_PATH.read_text())
            adj = data.get("scorer_adjustments", {})
            # Merge with defaults so missing keys don't break
            return {**DEFAULT_WEIGHTS, **adj}
        except (json.JSONDecodeError, KeyError):
            pass
    return DEFAULT_WEIGHTS.copy()


# ── Voice rules (hard failures — these should never ship) ────────────────────

DONT_SAY = [
    "shipped", "leverage", "leveraging", "robust", "game-changer",
    "game changer", "zero manual intervention", "can't believe this worked",
]

BAD_OPENERS = [
    r"^so\b",
    r"^here's the thing",
]

AI_FILLER = [
    "delve", "crucial", "vital", "holistic", "foster", "facilitate",
    "navigate", "utilize", "furthermore", "moreover", "seamless",
    "empower", "streamline", "ecosystem", "paradigm", "synergy",
    "comprehensive", "innovative", "cutting-edge",
]

# ── Generic take detection ───────────────────────────────────────────────────

GENERIC_PATTERNS = [
    r"ai is (?:changing|transforming|revolutionizing) (?:everything|the world|how we)",
    r"the future (?:of|is) (?:ai|coding|tech)",
    r"(?:we're|we are) (?:just|only) (?:getting started|at the beginning)",
    r"this is (?:just the beginning|only the start)",
    r"(?:buckle up|hold on tight|brace yourself)",
    r"(?:hot take|unpopular opinion|controversial take):",
    r"(?:let that sink in|read that again|i'll say it louder)",
    r"(?:thread|a thread)\s*[:\U0001F9F5]",
]

# ── Scoring ──────────────────────────────────────────────────────────────────


def score_tweet(text: str) -> dict:
    """Score a tweet draft 0-100 with meaningful dimensions."""

    W = load_weights()
    text_lower = text.lower()
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    char_len = len(text)
    dims = {}

    # ── 1. HOOK — does the first line stop scrolling? ─────────────────────
    # The opener is everything. Most people see only the first ~60 chars.
    hook_max = W["hook_weight"]
    hook_score = 0
    first_sentence = sentences[0] if sentences else ""
    first_words = first_sentence.split()

    # Short punchy opener (under 8 words) = strong hook
    if 2 <= len(first_words) <= 8:
        hook_score += 8

    # Starts with tension/conflict/problem
    tension_openers = [
        r"^(?:thought|tried|spent|broke|lost|failed|wasted|couldn't|nobody)",
        r"^(?:every|most|half|none|zero)\b",
        r"^(?:stop|don't|never|forget)\b",
        r"^\d",  # leads with a number
        r"^\$",  # leads with price
        r'^"',   # leads with a quote
    ]
    for pattern in tension_openers:
        if re.search(pattern, text_lower):
            hook_score += 7
            break

    # Curiosity gap — first line makes you need the second
    if len(sentences) >= 2:
        # Short opener + longer follow-up = curiosity gap
        if len(first_words) <= 6 and len(sentences[1].split()) > first_words.__len__():
            hook_score += 5
        # Opener ends with something unexpected or incomplete
        elif re.search(r'(?:twice|wrong|fine|nothing|backwards)', first_sentence.lower()):
            hook_score += 5

    # Penalty: generic/boring opener
    for pattern in GENERIC_PATTERNS:
        if re.search(pattern, first_sentence.lower()):
            hook_score -= 10
            break

    hook_score = max(0, min(hook_max, hook_score))
    dims["hook"] = {"score": hook_score, "max": hook_max, "first_line": first_sentence[:60]}

    # ── 2. SPECIFICITY — concrete > abstract ──────────────────────────────
    spec_max = W["specificity_weight"]
    spec_score = 0

    # Has a concrete number
    numbers = re.findall(r'\b\d[\d,\.]*\b', text)
    meaningful_numbers = [n for n in numbers if n not in ("0", "1", "2")]
    if meaningful_numbers:
        spec_score += 8

    # Names a specific tool/technology (not just "AI")
    specific_tools = re.findall(
        r'\b(?:claude code|headroom|cursor|copilot|react|python|docker|git|'
        r'uvicorn|httpx|tweepy|twikit|minimax|haiku|opus|sonnet|gpt-4|'
        r'gemini|windsurf|v0|bolt|lovable|vercel|supabase|redis)\b',
        text_lower,
    )
    if specific_tools:
        spec_score += 6

    # Describes what something DOES, not what it IS
    action_verbs = re.findall(
        r'\b(?:built|made|fixed|broke|found|discovered|debugged|'
        r'runs?|freeze[sd]?|hang[sd]?|crash(?:es|ed)?|bypass(?:es|ed)?|'
        r'skip(?:s|ped)?|stream(?:s|ed)?|retr(?:y|ies|ied))\b',
        text_lower,
    )
    if len(action_verbs) >= 2:
        spec_score += 6

    spec_score = min(spec_max, spec_score)
    dims["specificity"] = {
        "score": spec_score, "max": spec_max,
        "numbers": len(meaningful_numbers),
        "tools": specific_tools[:3],
        "actions": len(action_verbs),
    }

    # ── 3. VOICE — sounds like Bernard, not AI ────────────────────────────
    voice_max = W["voice_weight"]
    voice_score = voice_max
    voice_issues = []

    # Layer 1: Humanizer base check (40+ AI words, hedging, passive, em-dashes)
    humanity = score_humanity(text)
    humanity_raw = humanity["humanity_score"]  # 0-100

    # Humanity below 70 = AI smell. Subtract proportionally.
    if humanity_raw < 70:
        penalty = round((70 - humanity_raw) / 10)  # 1pt per 10 below 70
        voice_score -= penalty
        voice_issues.append(f"humanity={humanity_raw}/100")

        # Surface the worst humanizer dimension
        worst_section = min(
            humanity.get("sections", {}).items(),
            key=lambda x: x[1].get("score", 100) / max(x[1].get("max", 1), 1),
            default=("", {}),
        )
        if worst_section[0]:
            voice_issues.append(f"worst: {worst_section[0]}")

    # Layer 2: @<github-user>-specific voice rules
    # "Don't say" list from reference.md
    for word in DONT_SAY:
        if word in text_lower:
            voice_score -= 4
            voice_issues.append(f"don't say: '{word}'")

    # Bad openers
    for pattern in BAD_OPENERS:
        if re.search(pattern, text_lower):
            voice_score -= 5
            voice_issues.append(f"bad opener: '{pattern}'")

    # Exclamation marks (enthusiasm ≠ engagement)
    excl_count = text.count('!')
    if excl_count > 0:
        voice_score -= min(6, excl_count * 3)
        voice_issues.append(f"{excl_count} exclamation mark(s)")

    # More than 2 hashtags
    hashtag_count = len(re.findall(r'#\w+', text))
    if hashtag_count > 2:
        voice_score -= 4
        voice_issues.append(f"{hashtag_count} hashtags (max 2)")

    # More than 1 em-dash
    em_count = text.count('\u2014') + text.count(' -- ')
    if em_count > 1:
        voice_score -= 3
        voice_issues.append(f"{em_count} em-dashes (max 1)")

    # Generic takes (penalty stacks with hook penalty — intentional)
    for pattern in GENERIC_PATTERNS:
        if re.search(pattern, text_lower):
            voice_score -= 5
            voice_issues.append("generic take detected")
            break

    voice_score = max(0, voice_score)
    dims["voice"] = {
        "score": voice_score, "max": voice_max,
        "issues": voice_issues,
        "humanity_raw": humanity_raw,
    }

    # ── 4. RHYTHM — sentence variety = readability ────────────────────────
    rhythm_max = W["rhythm_weight"]
    rhythm_score = 0

    if len(sentences) >= 2:
        lengths = [len(s.split()) for s in sentences]

        # Sentence length variance (uniform = robotic)
        if len(lengths) >= 3:
            avg_len = sum(lengths) / len(lengths)
            variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
            if variance > 8:
                rhythm_score += 5  # good variety
            elif variance > 4:
                rhythm_score += 3

        # Has at least one punchy sentence (1-4 words)
        punchy = [l for l in lengths if 1 <= l <= 4]
        if punchy:
            rhythm_score += 4

        # Contrast: short sentence after long one (the punchline move)
        for i in range(1, len(lengths)):
            if lengths[i - 1] >= 8 and lengths[i] <= 5:
                rhythm_score += 4
                break

        # Staccato: 3+ short sentences in a row
        streak = 0
        max_streak = 0
        for l in lengths:
            if l <= 5:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        if max_streak >= 3:
            rhythm_score += 2

    rhythm_score = min(rhythm_max, rhythm_score)
    dims["rhythm"] = {"score": rhythm_score, "max": rhythm_max, "sentences": len(sentences)}

    # ── 5. STRANGER TEST — makes sense without context ────────────────────
    stranger_max = W["stranger_weight"]
    stranger_score = stranger_max

    # References "my bot" / "my proxy" / "my system" without explaining what it does
    vague_refs = re.findall(r'\bmy (?:bot|proxy|system|pipeline|setup|thing)\b', text_lower)
    if vague_refs and not re.search(r'(?:which|that|—|:)\s', text):
        stranger_score -= 4

    # Uses acronyms/jargon without context
    acronyms = re.findall(r'\b[A-Z]{2,5}\b', text)
    known_acronyms = {"AI", "API", "CLI", "URL", "WiFi", "VPN", "SDK", "IDE", "PR", "UI", "UX", "OS", "TG"}
    unknown_acronyms = [a for a in acronyms if a not in known_acronyms]
    if unknown_acronyms:
        stranger_score -= 3

    # Assumes shared context ("as I mentioned", "like I said", "you know")
    if re.search(r'\b(?:as (?:i|we) (?:mentioned|said|showed)|you know|remember when)\b', text_lower):
        stranger_score -= 4

    stranger_score = max(0, stranger_score)
    dims["stranger_test"] = {
        "score": stranger_score, "max": stranger_max,
        "unknown_acronyms": unknown_acronyms,
    }

    # ── 6. LENGTH — fits the format ───────────────────────────────────────
    length_max = W["length_weight"]
    sweet_lo, sweet_hi = W["length_sweet_spot"]
    if char_len > 4000:
        length_score = 0  # X Premium limit
    elif sweet_lo <= char_len <= sweet_hi:
        length_score = length_max  # sweet spot
    elif 100 <= char_len <= 500:
        length_score = round(length_max * 0.7)
    elif 50 <= char_len < 100:
        length_score = round(length_max * 0.5)
    else:
        length_score = round(length_max * 0.2)

    dims["length"] = {"score": length_score, "max": length_max, "chars": char_len}

    # ── TOTAL ────────────────────────────────────────────────────────────
    total = sum(d["score"] for d in dims.values())
    total = min(100, max(0, total))

    # Find weakest dimension (lowest % of max)
    all_dims = [(name, d["score"], d["max"]) for name, d in dims.items()]
    weakest = min(all_dims, key=lambda d: d[1] / d[2] if d[2] > 0 else 1)

    # Label
    if total >= 85:
        label = "Ready to post"
    elif total >= 70:
        label = "Good, light polish needed"
    elif total >= 55:
        label = "Decent, needs work on weakest dimension"
    elif total >= 40:
        label = "Weak, significant rework needed"
    else:
        label = "Start over"

    # Rewrite hints
    hints = {
        "hook": "Rewrite the opener. Lead with tension, a number, or a short punchy statement. Cut generic takes.",
        "specificity": "Add a concrete number, name a specific tool, or describe what something does (not what it is).",
        "voice": f"Fix voice issues: {', '.join(voice_issues) if voice_issues else 'none'}.",
        "rhythm": "Vary sentence lengths. Add a punchy 2-4 word sentence. Use contrast: long setup, short punchline.",
        "stranger_test": "Would a stranger understand this? Explain jargon, cut insider references, add context.",
        "length": f"Adjust length ({char_len} chars). Sweet spot: 180-400 chars.",
    }

    return {
        "score": total,
        "label": label,
        "dimensions": dims,
        "weakest": {"dimension": weakest[0], "score": weakest[1], "max": weakest[2]},
        "rewrite_hint": hints[weakest[0]],
        "char_count": char_len,
    }


def print_report(result: dict) -> None:
    bar_filled = int(result["score"] / 5)
    bar = "\u2588" * bar_filled + "\u2591" * (20 - bar_filled)

    print()
    print(f"  TWEET SCORE:  {result['score']}/100  [{bar}]")
    print(f"  {result['label']}  ({result['char_count']} chars)")
    print()

    for name, dim in result["dimensions"].items():
        extras = ""
        if name == "voice" and dim.get("issues"):
            extras = f"  [{', '.join(dim['issues'][:3])}]"
        elif name == "hook":
            extras = f"  [{dim.get('first_line', '')[:40]}...]"
        elif name == "specificity":
            extras = f"  [nums={dim['numbers']}, tools={len(dim.get('tools', []))}, actions={dim['actions']}]"
        elif name == "stranger_test" and dim.get("unknown_acronyms"):
            extras = f"  [unknown: {', '.join(dim['unknown_acronyms'])}]"
        print(f"  {name:<16} {dim['score']:>2}/{dim['max']}{extras}")

    print()
    w = result["weakest"]
    print(f"  Weakest: {w['dimension']} ({w['score']}/{w['max']})")
    print(f"  Hint: {result['rewrite_hint']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Score tweet drafts for @<github-user>")
    parser.add_argument("text", nargs="?", help="Tweet text (or '-' for stdin)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.text == "-" or (args.text is None and not sys.stdin.isatty()):
        text = sys.stdin.read().strip()
    elif args.text:
        text = args.text
    else:
        parser.print_help()
        sys.exit(1)

    result = score_tweet(text)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
