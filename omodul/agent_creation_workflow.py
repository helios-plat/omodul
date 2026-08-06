"""omodul.agent_creation_workflow — (config, input_data, output_dir) workflow.

AutoAgent zero-code agent creation: NL form → codegen → write .py file →
ast.parse verify → import register.  This is ``create_agent`` from AutoAgent
mapped to the 3O omodul triplet convention.

3O element: ``omodul.agent_creation_workflow``.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from typing import Any


def agent_creation_workflow(config: dict[str, Any], input_data: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Create an agent from a form spec and register it.

    Config keys:
      * ``model`` — default LLM model id
      * ``output_dir_override`` — where to write agent files (default: output_dir/agents)

    Input data keys:
      * ``form`` — AgentSpec dict (from ``oskill.agent_form_synthesize``)
      * ``llm_caller`` — optional LLM callable for form synthesis

    Returns:
      {status, agent_name, file_path, registered, ast_valid, source_preview}
    """
    form = dict(input_data.get("form") or input_data)
    if not form.get("name"):
        return {"status": "failed", "error": "agent form missing 'name'", "registered": False}

    # 1. NL → form (if raw request)
    user_llm = config.get("llm_caller") or input_data.get("llm_caller")
    if isinstance(input_data, dict) and "goal" in input_data and not form.get("description"):
        try:
            from oskill.agent_form_synthesize import agent_form_synthesize
            user_request = str(input_data.get("goal", ""))
            form = agent_form_synthesize(user_request, llm_caller=user_llm, context={})
        except Exception:
            pass

    # 2. codegen
    try:
        from oprim.agent_codegen import agent_codegen
        source = agent_codegen(form)
    except Exception as exc:
        return {"status": "failed", "error": f"codegen: {exc}", "registered": False}

    # 3. write file
    agents_dir = output_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    import re
    safe_name = "auto_agent_" + (re.sub(r"[^a-zA-Z0-9_]", "_", form.get("name", "agent")).lower().strip("_") or "agent")
    agent_file = agents_dir / f"{safe_name}.py"
    try:
        agent_file.write_text(source, encoding="utf-8")
    except Exception as exc:
        return {"status": "failed", "error": f"file write: {exc}", "registered": False}

    # 4. ast verify
    ast_valid = False
    try:
        ast.parse(source)
        ast_valid = True
    except SyntaxError as exc:
        return {"status": "failed", "error": f"syntax error: {exc}", "registered": False, "ast_valid": False}

    # 5. register — dynamic import (add agents_dir to sys.path temporarily)
    registered = False
    agents_dir_str = str(agents_dir)
    if agents_dir_str not in sys.path:
        sys.path.insert(0, agents_dir_str)
    try:
        importlib.import_module(safe_name)
        registered = True
    except Exception as exc:
        return {"status": "failed", "error": f"registration: {exc}", "registered": False, "file_path": str(agent_file), "ast_valid": True}

    return {
        "status": "completed",
        "agent_name": form.get("name", "?") or "?",
        "file_path": str(agent_file),
        "registered": registered,
        "ast_valid": ast_valid,
        "source_preview": source[:500],
    }
