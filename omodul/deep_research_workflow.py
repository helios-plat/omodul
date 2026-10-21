"""omodul.deep_research_workflow — deep research over search+synthesis."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel


class DeepResearchConfig(BaseModel):
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    output_format: str = "markdown"
    budget_usd: float = 5.0
    overwrite: bool = True
    max_iterations: int = 3

    _omodul_name: str = "deep_research_workflow"
    _omodul_version: str = "0.1.0"
    _fingerprint_fields: set = set()
    _enabled_pillars: set = {"fingerprint", "decision_trail", "report", "cost"}


def compute_fingerprint_for(config: DeepResearchConfig, input_data: dict) -> str:
    import hashlib, json
    return hashlib.sha256(json.dumps({"c": config.model_dump(), "i": input_data}, sort_keys=True, default=str).encode()).hexdigest()


async def deep_research_workflow(
    config: DeepResearchConfig,
    input_data: dict[str, Any],
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = []
    def step(d):
        trail.append(d)
        if on_step: on_step(d)
    query = input_data.get("query", "")
    search_fn = input_data.get("search_fn")
    synthesize_fn = input_data.get("synthesize_fn")
    llm_caller = input_data.get("llm_caller")
    status = "completed"
    error = None
    findings: dict[str, Any] = {}
    try:
        try:
            from oskill import query_expansion, search_synthesize_loop  # type: ignore
        except Exception:
            from oskill._query_expansion import query_expansion  # type: ignore
            from oskill._search_synthesize_loop import search_synthesize_loop  # type: ignore
        subqs = query_expansion(query=query, llm_caller=llm_caller)
        step({"step": "expand", "subqueries": subqs})
        results = search_synthesize_loop(
            query=query,
            search_fn=search_fn,
            synthesize_fn=synthesize_fn,
            max_iterations=config.max_iterations,
            llm_caller=llm_caller,
        )
        findings = {"subqueries": subqs, "research": results}
        step({"step": "research", "iterations": len(results.get("iterations", []))})
    except Exception as exc:
        status = "failed"
        error = {"message": str(exc)}
    fp = compute_fingerprint_for(config, input_data) if "fingerprint" in config._enabled_pillars else None
    report_path = None
    if "report" in config._enabled_pillars:
        report_path = output_dir / "deep_research_report.md"
        report_path.write_text(f"# Deep Research\n\n{findings.get('research', {}).get('final', '')}\n", encoding="utf-8")
    return {
        "findings": findings,
        "status": status,
        "error": error,
        "fingerprint": fp,
        "decision_trail": {"steps": trail} if "decision_trail" in config._enabled_pillars else None,
        "cost_usd": 0.0,
        "report_path": report_path,
    }
