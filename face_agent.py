#!/usr/bin/env python3
"""
Multi-agent orchestrator for face-analysis-app.
Two specialist agents (UI + Backend) with focused contexts — saves tokens vs subagents.

Usage:
  python3 ~/face-agent.py
  python3 ~/face-agent.py "add shareable result cards"
"""

import sys
import os
import json
import re
import anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

APP_DIR = os.path.expanduser("~/face-analysis-app")
BACKEND_FILE = os.path.join(APP_DIR, "app.py")
FRONTEND_FILE = os.path.join(APP_DIR, "templates/index.html")

client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY from env
MODEL = "claude-haiku-4-5-20251001"  # cheap + fast for agents

# ── Agent conversation histories ───────────────────────────────────────────────
ui_history = []
backend_history = []

ORCHESTRATOR_SYSTEM = """You are an orchestrator for a face analysis webapp (爱颜 · Miss AI).
The app has two files:
- templates/index.html  (~9000 lines) — all frontend: HTML, CSS, Three.js 3D face, overlays, UI
- app.py                (~2000 lines) — Flask backend: routes, Claude AI analysis, measurements

Your job:
1. Analyse the user's request
2. Decide: does it touch UI only, backend only, or both?
3. Output a JSON routing plan

Respond ONLY with JSON:
{
  "summary": "one-line description of the task",
  "ui_task": "specific instructions for UI agent, or null if not needed",
  "backend_task": "specific instructions for backend agent, or null if not needed",
  "context": "any shared context both agents need to know"
}"""

UI_SYSTEM = """You are a specialist UI agent for the face analysis webapp 爱颜 · Miss AI.
You only work on: templates/index.html (HTML, CSS, JavaScript, Three.js 3D overlays)

When given a task, output your changes as JSON:
{
  "message": "what you did",
  "edits": [
    {
      "search": "exact string to find (unique, 3-5 lines of context)",
      "replace": "replacement string"
    }
  ]
}

Rules:
- Each "search" must be unique in the file
- Include enough context lines to be unique
- Keep edits minimal — only change what's needed
- If no changes needed, return {"message": "no changes needed", "edits": []}"""

BACKEND_SYSTEM = """You are a specialist backend agent for the face analysis webapp 爱颜 · Miss AI.
You only work on: app.py (Flask routes, Claude AI calls, measurement calculations)

When given a task, output your changes as JSON:
{
  "message": "what you did",
  "edits": [
    {
      "search": "exact string to find (unique, 3-5 lines of context)",
      "replace": "replacement string"
    }
  ]
}

Rules:
- Each "search" must be unique in the file
- Include enough context lines to be unique
- Keep edits minimal — only change what's needed
- If no changes needed, return {"message": "no changes needed", "edits": []}"""


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def apply_edits(filepath, edits):
    """Apply edits transactionally — all-or-nothing. If any search string is
    missing, zero edits are applied to prevent partial-edit corruption."""
    content = read_file(filepath)

    # Pre-validate: ALL search strings must exist
    missing = []
    for i, edit in enumerate(edits):
        search = edit.get("search", "")
        if not search:
            missing.append(f"Edit {i}: empty search string")
        elif search not in content:
            missing.append(f"Edit {i}: '{search[:60]}...' not found")

    if missing:
        print(f"  [ROLLBACK] {len(missing)} edit(s) failed pre-validation:", flush=True)
        for m in missing:
            print(f"    - {m}", flush=True)
        return 0, missing

    # All validated — apply
    for edit in edits:
        content = content.replace(edit["search"], edit["replace"], 1)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return len(edits), []


def extract_json(text):
    """Extract JSON from agent response (handles markdown code blocks)."""
    # Try to find JSON block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    # Try raw JSON
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No JSON found in response: {text[:200]}")


def validate_agent_output(result):
    """Validate agent JSON output has required structure."""
    if not isinstance(result, dict):
        raise ValueError(f"Agent output is {type(result).__name__}, expected dict")
    if "message" not in result:
        raise ValueError("Missing 'message' key in agent output")
    if "edits" not in result:
        result["edits"] = []
    if not isinstance(result["edits"], list):
        raise ValueError(f"'edits' must be a list, got {type(result['edits']).__name__}")
    for i, edit in enumerate(result["edits"]):
        if "search" not in edit or "replace" not in edit:
            raise ValueError(f"Edit {i} missing 'search' or 'replace'")
    return result


def orchestrate(task):
    """Ask orchestrator to route the task."""
    print(f"\n[Orchestrator] Routing: {task}", flush=True)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=ORCHESTRATOR_SYSTEM,
        messages=[{"role": "user", "content": task}],
    )
    plan = extract_json(resp.content[0].text)
    print(f"  → UI task: {plan.get('ui_task') or 'none'}", flush=True)
    print(f"  → Backend task: {plan.get('backend_task') or 'none'}", flush=True)
    return plan


def run_ui_agent(task, context=""):
    """Send task to UI agent (maintains conversation history)."""
    global ui_history
    print(f"\n[UI Agent] Working...", flush=True)

    # First message in this session — load the file
    if not ui_history:
        file_content = read_file(FRONTEND_FILE)
        ui_history.append({
            "role": "user",
            "content": f"Here is the current index.html:\n\n```html\n{file_content}\n```\n\nI'll give you tasks to implement."
        })
        ui_history.append({
            "role": "assistant",
            "content": '{"message": "Ready. Send me tasks.", "edits": []}'
        })

    prompt = f"Context: {context}\n\nTask: {task}" if context else f"Task: {task}"
    ui_history.append({"role": "user", "content": prompt})

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=UI_SYSTEM,
        messages=ui_history,
    )
    reply = resp.content[0].text
    ui_history.append({"role": "assistant", "content": reply})

    result = validate_agent_output(extract_json(reply))
    print(f"  → {result.get('message', '')}", flush=True)

    if result.get("edits"):
        applied, failed = apply_edits(FRONTEND_FILE, result["edits"])
        print(f"  → Applied {applied} edit(s)", flush=True)
        if failed:
            print(f"  → Failed to find: {failed}", flush=True)

    return result


def run_backend_agent(task, context=""):
    """Send task to backend agent (maintains conversation history)."""
    global backend_history
    print(f"\n[Backend Agent] Working...", flush=True)

    if not backend_history:
        file_content = read_file(BACKEND_FILE)
        backend_history.append({
            "role": "user",
            "content": f"Here is the current app.py:\n\n```python\n{file_content}\n```\n\nI'll give you tasks to implement."
        })
        backend_history.append({
            "role": "assistant",
            "content": '{"message": "Ready. Send me tasks.", "edits": []}'
        })

    prompt = f"Context: {context}\n\nTask: {task}" if context else f"Task: {task}"
    backend_history.append({"role": "user", "content": prompt})

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=BACKEND_SYSTEM,
        messages=backend_history,
    )
    reply = resp.content[0].text
    backend_history.append({"role": "assistant", "content": reply})

    result = validate_agent_output(extract_json(reply))
    print(f"  → {result.get('message', '')}", flush=True)

    if result.get("edits"):
        applied, failed = apply_edits(BACKEND_FILE, result["edits"])
        print(f"  → Applied {applied} edit(s)", flush=True)
        if failed:
            print(f"  → Failed to find: {failed}", flush=True)

    return result


def run(task):
    plan = orchestrate(task)
    context = plan.get("context", "")
    ui_task = plan.get("ui_task")
    backend_task = plan.get("backend_task")

    results = {}

    # Backend-first handoff: run backend, then pass its output to UI agent
    if backend_task:
        results["backend"] = run_backend_agent(backend_task, context)
        be = results["backend"]
        backend_context = f"\nBackend changes: {be.get('message', '')}"
        if be.get("edits"):
            snippets = [e.get("replace", "")[:200] for e in be["edits"][:5]]
            backend_context += "\nNew/modified code:\n" + "\n---\n".join(snippets)
        context += backend_context

    if ui_task:
        results["ui"] = run_ui_agent(ui_task, context)

    print(f"\n[Done] Task complete.", flush=True)
    return results


def main():
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        run(task)
        return

    print("爱颜 Multi-Agent — type a task, 'quit' to exit")
    print("Example: add a share button that copies the result URL\n")
    while True:
        try:
            task = input("Task> ").strip()
            if not task or task.lower() in ("quit", "exit", "q"):
                break
            run(task)
        except KeyboardInterrupt:
            break
    print("Bye.")


if __name__ == "__main__":
    main()
