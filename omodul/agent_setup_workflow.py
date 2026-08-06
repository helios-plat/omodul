"""omodul.agent_setup_workflow — DeerFlow-style interactive agent setup wizard.

Bootstraps a new agent: generates SOUL.md, registers the agent, optionally
installs skills, and validates the result.  Mirrors DeerFlow's
``setup_agent`` tool + ``configure.py`` bootstrap flow.

3O element: ``omodul.agent_setup_workflow``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def agent_setup_workflow(
    config: dict[str, Any],
    input_data: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Setup a new agent with SOUL.md + config bootstrap.

    Config keys:
      * ``model`` — default LLM
      * ``interactive`` — if True, simulates interactive prompts from input_data

    Input data keys:
      * ``agent_name`` — required
      * ``description`` — one-line purpose
      * ``soul`` — full SOUL.md content (or generated from description)
      * ``skills`` — optional list of skill names
      * ``model_settings`` — optional {temperature, max_tokens, ...}
      * ``thinking_enabled`` — bool

    Returns:
        {status, agent_name, soul_path, config_path, registered, setup_steps}
    """
    name = str(input_data.get("agent_name") or input_data.get("name") or "")
    if not name:
        return {"status": "failed", "error": "agent_name is required"}

    desc = str(input_data.get("description") or f"Agent: {name}")
    soul = str(input_data.get("soul") or "")
    skills = list(input_data.get("skills") or [])
    model_settings = dict(input_data.get("model_settings") or {})
    thinking = input_data.get("thinking_enabled", True)

    steps: list[str] = []

    # 1. generate SOUL.md if not provided
    if not soul.strip():
        soul = _generate_soul(name, desc, skills, model_settings, thinking, config)
        steps.append("generated_soul")
    else:
        steps.append("soul_provided")

    # 2. write SOUL.md
    agents_dir = output_dir / "agents" / name
    agents_dir.mkdir(parents=True, exist_ok=True)
    soul_path = agents_dir / "SOUL.md"
    try:
        from oprim.soul_config_rewrite import _atomic_write
        _atomic_write(soul_path, soul)
        steps.append("wrote_soul")
    except Exception as exc:
        return {"status": "failed", "error": f"write SOUL.md: {exc}", "setup_steps": steps}

    # 3. write config
    cfg = {
        "name": name, "description": desc, "skills": skills,
        "model_settings": model_settings or {"temperature": 0.7},
        "thinking_enabled": thinking,
    }
    config_path = agents_dir / "config.json"
    try:
        config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        steps.append("wrote_config")
    except Exception as exc:
        return {"status": "failed", "error": f"write config: {exc}", "setup_steps": steps}

    # 4. register
    registered = False
    try:
        from obase.agent_registry import registry
        registry.register("agent", name, _make_agent_factory(name, agents_dir))
        registered = True
        steps.append("registered")
    except Exception as exc:
        steps.append(f"register_skipped: {exc}")

    return {
        "status": "completed",
        "agent_name": name,
        "soul_path": str(soul_path),
        "config_path": str(config_path),
        "registered": registered,
        "setup_steps": steps,
    }


def _generate_soul(
    name: str, desc: str, skills: list[str], model_settings: dict, thinking: bool, config: dict,
) -> str:
    skills_text = ", ".join(skills) if skills else "general-purpose"
    thinking_text = "enabled" if thinking else "disabled"
    return (
        f"# {name} — SOUL.md\n\n"
        f"## Identity\n{desc}\n\n"
        f"## Skills\n{skills_text}\n\n"
        f"## Model Settings\n{json.dumps(model_settings or {'temperature': 0.7}, indent=2)}\n\n"
        f"## Thinking\n{thinking_text}\n\n"
        f"## Guidelines\n"
        f"- Be helpful and precise.\n"
        f"- Use tools when appropriate.\n"
        f"- Learn from feedback and self-evolve.\n"
    )


def _make_agent_factory(name: str, agents_dir: Path) -> Any:
    def factory(model: str = "claude-sonnet-4-6") -> dict[str, Any]:
        return {"name": name, "model": model, "dir": str(agents_dir)}
    return factory
