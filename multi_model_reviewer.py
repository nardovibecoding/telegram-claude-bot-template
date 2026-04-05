# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Multi-model batch reviewer — 6 models review Claude's responses.

Every 10 responses, batch-send to all available models.
Each checks against active_rules.md. Violations are logged
to self_review.md and active_rules.md gets updated.

Usage:
    from multi_model_reviewer import batch_review
    violations = batch_review(responses_list)
"""
import logging
from pathlib import Path

from llm_client import chat_completion

log = logging.getLogger("reviewer")

PROJECT_DIR = Path(__file__).parent
ACTIVE_RULES = PROJECT_DIR / "memory" / "active_rules.md"
SELF_REVIEW = PROJECT_DIR / "memory" / "self_review.md"


def _load_rules() -> str:
    if ACTIVE_RULES.exists():
        return ACTIVE_RULES.read_text()
    return "No active rules found."


def _build_prompt(
    responses: list[str],
    rules: str,
) -> str:
    resp_text = "\n---\n".join(
        f"Response {i+1}:\n{r[:500]}"
        for i, r in enumerate(responses)
    )
    return (
        "You are reviewing an AI assistant's responses "
        "for rule violations.\n\n"
        f"RULES:\n{rules}\n\n"
        f"RESPONSES TO CHECK:\n{resp_text}\n\n"
        "For each violation found, output:\n"
        "- VIOLATION: rule number, which response, "
        "what was wrong\n"
        "If no violations: output CLEAN\n"
        "Be strict. Only flag genuine violations, "
        "not style preferences."
    )


def batch_review(
    responses: list[str],
) -> dict[str, str]:
    """Review a batch of responses via llm_client fallback chain.

    Args:
        responses: List of assistant response texts

    Returns:
        Dict with single key "llm_client" -> review result
    """
    rules = _load_rules()
    prompt = _build_prompt(responses, rules)
    results = {}

    try:
        result = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
        )
        log.info("Reviewer llm_client: %s", result[:100])
        results["llm_client"] = result
    except Exception as e:
        log.warning("Reviewer llm_client failed: %s", e)

    return results


def log_violations(
    results: dict[str, str],
) -> list[str]:
    """Extract violations and log to self_review.md.

    Returns list of unique violations found.
    """
    violations = []
    flagged_by = {}

    for model, result in results.items():
        if "CLEAN" in result.upper():
            continue
        for line in result.split("\n"):
            line = line.strip()
            if line.startswith("VIOLATION") or (
                "rule" in line.lower()
                and "violat" in line.lower()
            ):
                violations.append(f"[{model}] {line}")
                # Track which models flag which rules
                for i in range(1, 21):
                    if f"rule {i}" in line.lower() or (
                        f"#{i}" in line
                    ):
                        flagged_by.setdefault(
                            i, []
                        ).append(model)

    if violations and SELF_REVIEW.exists():
        from datetime import datetime

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = (
            f"\n### Multi-model review ({ts})\n"
        )
        for v in violations[:10]:
            entry += f"- {v}\n"
        if flagged_by:
            entry += "Rules flagged by multiple models: "
            multi = {
                k: v
                for k, v in flagged_by.items()
                if len(v) >= 2
            }
            if multi:
                for rule, models in multi.items():
                    entry += (
                        f"Rule {rule} "
                        f"[{', '.join(models)}] "
                    )
            entry += "\n"

        with open(SELF_REVIEW, "a") as f:
            f.write(entry)

    return violations


if __name__ == "__main__":
    # Test with dummy responses
    from dotenv import load_dotenv
    load_dotenv()

    test_responses = [
        "Good question! Let me check that.",
        "I'll use edge-tts for now, we can "
        "upgrade later.",
        "The sync runs every 10 minutes.",
    ]

    print("Running batch review...")
    results = batch_review(test_responses)
    for model, result in results.items():
        print(f"\n=== {model} ===")
        print(result[:300])

    violations = log_violations(results)
    print(f"\n{len(violations)} violations found")
