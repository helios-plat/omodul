"""omodul.orchestrator_creation_workflow — multi-agent orchestrator codegen.

Creates an orchestrator agent that chains sub-agents (name → input → output) in
sequence, like AutoAgent's ``create_orchestrator_agent``.

3O element: ``omodul.orchestrator_creation_workflow``.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from pathlib import Path
from typing import Any


def orchestrator_creation_workflow(config: dict[str, Any], input_data: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Create a multi-agent orchestrator.

    Input data keys:
      * ``agent_name`` — orchestrator display name
      * ``description`` — one-line purpose
      * ``sub_agents`` — [{name, input, output}, ...]
      * ``instructions`` — orchestrator system prompt
      * ``goal`` — the end-user goal (injected as first message)

    Returns:
      {status, orchestrator_name, file_path, registered, sub_agent_count}
    """
    name = str(input_data.get("agent_name") or input_data.get("name") or "Orchestrator")
    desc = str(input_data.get("description") or input_data.get("goal") or "")
    subs = list(input_data.get("sub_agents") or [])
    instructions = str(input_data.get("instructions") or f"你是 {name}。将用户任务分发给子 Agent 并汇总结果。")
    goal = str(input_data.get("goal") or "")

    if not subs:
        return {"status": "failed", "error": "sub_agents list is empty", "registered": False}

    sub_calls = []
    for s in subs:
        n = s.get("name", "?")
        inp = s.get("input", "the task")
        out = s.get("output", "result")
        sub_calls.append(f"#  step: call {n} → input: {inp}, output: {out}")

    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower()).strip("_")
    source = f'''"""Auto-generated orchestrator: {name} (3O omodul.orchestrator_creation_workflow)."""
from obase.agent_registry import register_agent

SUB_AGENTS = {[s["name"] for s in subs]!r}

def get_{safe_name}(model: str = {config.get("model", "claude-sonnet-4-6")!r}):
    """{desc}"""
    def instructions(context_variables):
        return {instructions!r}
    return {{
        "name": {name!r},
        "model": model,
        "description": {desc!r},
        "instructions": instructions,
        "tools": [],
        "handoffs": {{}},
        "sub_agents": SUB_AGENTS,
    }}

@register_agent(name={name!r}, func_name="get_{safe_name}")
def _factory(model: str = {config.get("model", "claude-sonnet-4-6")!r}):
    return get_{safe_name}(model)
'''

    # write
    agents_dir = output_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    orch_file = agents_dir / f"{safe_name}_orchestrator.py"
    orch_file.write_text(source, encoding="utf-8")

    # verify + register
    ast_valid = False
    try:
        ast.parse(source)
        ast_valid = True
    except SyntaxError as exc:
        return {"status": "failed", "error": f"syntax: {exc}", "registered": False}

    registered = False
    agents_str = str(agents_dir)
    if agents_str not in sys.path:
        sys.path.insert(0, agents_str)
    try:
        importlib.import_module(f"{safe_name}_orchestrator")
        registered = True
    except Exception as exc:
        return {"status": "failed", "error": f"register: {exc}", "file_path": str(orch_file), "ast_valid": True, "registered": False}

    return {
        "status": "completed",
        "orchestrator_name": name,
        "file_path": str(orch_file),
        "registered": registered,
        "ast_valid": ast_valid,
        "sub_agent_count": len(subs),
    }
