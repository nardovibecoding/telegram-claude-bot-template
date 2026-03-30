#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
A/B test: Claude Sonnet at LOW vs MEDIUM effort levels.
Uses claude CLI (Claude Max) with different system prompts to simulate effort levels.
Measures response time, token usage, and compares answer quality.
"""

import subprocess
import time
import json
import os

# Remove invalid API key so Claude Max auth is used
env = os.environ.copy()
env.pop("ANTHROPIC_API_KEY", None)

questions = [
    "Can 2 Rimowa Classic Cabin suitcases fit in the trunk of a BMW 320i?",
    "What's the weather in Marbella this weekend?",
    "帮我翻译：I would like to extend my lease for another 6 months",
    "Is it safe to take ibuprofen with blood pressure medication?",
    "Best restaurants near Plaza Mayor Madrid, with ratings",
]

short_labels = [
    "Rimowa + BMW trunk",
    "Marbella weather",
    "Chinese translation",
    "Ibuprofen + BP meds",
    "Madrid restaurants",
]

# System prompts to control effort level
LOW_SYSTEM = "You are a helpful assistant. Give SHORT, direct answers. Maximum 2-3 sentences. No disclaimers, no caveats. Just answer the question directly."
MED_SYSTEM = "You are a helpful assistant for a 73-year-old man. Give THOROUGH, detailed, actionable answers. Include specific numbers, recommendations, and practical advice. Be comprehensive but clear."


def call_claude(question, system_prompt, label=""):
    """Call claude CLI and measure time."""
    cmd = [
        "claude", "-p", question,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--max-turns", "1",
    ]

    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    elapsed = time.time() - t0

    if result.returncode != 0:
        return elapsed, f"ERROR: {result.stderr[:300]}", 0, 0

    try:
        data = json.loads(result.stdout)
        text = data.get("result", "")
        out_tokens = data.get("usage", {}).get("output_tokens", 0)
        in_tokens = data.get("usage", {}).get("input_tokens", 0)
        if data.get("is_error"):
            text = f"ERROR: {text}"
    except (json.JSONDecodeError, KeyError):
        text = result.stdout[:2000]
        out_tokens = len(text.split())
        in_tokens = 0

    return elapsed, text, out_tokens, in_tokens


results = []

for i, question in enumerate(questions):
    print(f"\n{'='*70}")
    print(f"Q{i+1}: {short_labels[i]}")
    print(f"    {question}")
    print(f"{'='*70}")

    # LOW effort
    print("  [LOW] Running...")
    t_low, text_low, tok_low, in_low = call_claude(question, LOW_SYSTEM, "LOW")
    print(f"  [LOW] {t_low:.1f}s | {tok_low} out tokens")
    print(f"  [LOW] Preview: {text_low[:100]}...")

    # Small pause to avoid rate limiting
    time.sleep(1)

    # MEDIUM effort
    print("  [MED] Running...")
    t_med, text_med, tok_med, in_med = call_claude(question, MED_SYSTEM, "MED")
    print(f"  [MED] {t_med:.1f}s | {tok_med} out tokens")
    print(f"  [MED] Preview: {text_med[:100]}...")

    results.append({
        "q_num": i + 1,
        "label": short_labels[i],
        "question": question,
        "low_time": round(t_low, 1),
        "low_tokens": tok_low,
        "low_response": text_low,
        "med_time": round(t_med, 1),
        "med_tokens": tok_med,
        "med_response": text_med,
    })

    time.sleep(1)

# Save full results
with open(os.path.expanduser("~/telegram-claude-bot/ab_test_results.json"), "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

# ─── SUMMARY TABLE ───
print("\n\n")
print("=" * 110)
print("  A/B TEST: Claude Sonnet — LOW effort (brief) vs MEDIUM effort (thorough)")
print("  Target user: 73-year-old man asking practical questions")
print("=" * 110)

print(f"\n{'#':<3} {'Topic':<22} {'Low time':>9} {'Low tok':>8} {'Med time':>9} {'Med tok':>8} {'Time +/-':>10} {'Token +/-':>10}")
print("─" * 83)

total_low_t = 0
total_med_t = 0
total_low_tok = 0
total_med_tok = 0

for r in results:
    diff_t = r['med_time'] - r['low_time']
    diff_tok = r['med_tokens'] - r['low_tokens']
    total_low_t += r['low_time']
    total_med_t += r['med_time']
    total_low_tok += r['low_tokens']
    total_med_tok += r['med_tokens']
    print(f"{r['q_num']:<3} {r['label']:<22} {r['low_time']:>8.1f}s {r['low_tokens']:>7} {r['med_time']:>8.1f}s {r['med_tokens']:>7} {diff_t:>+8.1f}s {diff_tok:>+9}")

print("─" * 83)
print(f"{'AVG':<25} {total_low_t/5:>8.1f}s {total_low_tok//5:>7} {total_med_t/5:>8.1f}s {total_med_tok//5:>7} {(total_med_t-total_low_t)/5:>+8.1f}s {(total_med_tok-total_low_tok)//5:>+9}")

# ─── FULL RESPONSES ───
print(f"\n\n{'='*110}")
print("  FULL RESPONSES")
print(f"{'='*110}")

for r in results:
    print(f"\n{'━'*110}")
    print(f"  Q{r['q_num']}: {r['question']}")
    print(f"{'━'*110}")

    print(f"\n  ▸ LOW EFFORT ({r['low_time']}s, {r['low_tokens']} tokens):")
    print(f"  {'─'*100}")
    for line in r['low_response'].split('\n'):
        print(f"    {line}")

    print(f"\n  ▸ MEDIUM EFFORT ({r['med_time']}s, {r['med_tokens']} tokens):")
    print(f"  {'─'*100}")
    for line in r['med_response'].split('\n'):
        print(f"    {line}")

print(f"\n\n{'='*110}")
print("  Test complete. Results saved to ab_test_results.json")
print(f"{'='*110}")
