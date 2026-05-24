import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from obase.cost_tracker import CostTracker


def write_markdown_report(
    *,
    output_dir: Path,
    omodul_name: str,
    fingerprint: str,
    config: Any,
    findings: Any,
    decision_trail: dict[str, Any],
    cost_tracker: CostTracker,
    status: Literal["completed", "failed"],
    custom_findings_section: Callable[[Any], str] | None = None,
) -> Path:
    """写 markdown 报告. 7 sections 顺序固定."""
    suffix = ".md" if status == "completed" else ".partial.md"
    report_path = output_dir / f"{omodul_name}_{fingerprint[:12]}{suffix}"

    sections = [
        _section_header(omodul_name, fingerprint, status),
        _section_executive_summary(findings, status),
        _section_configuration(config),
        custom_findings_section(findings) if custom_findings_section else _section_findings_default(findings),
        _section_decision_trail_summary(decision_trail),
        _section_cost_breakdown(cost_tracker),
        _section_reproducibility(omodul_name, config, fingerprint),
    ]
    report_path.write_text("\n\n".join(sections), encoding="utf-8")
    return report_path


def _section_header(omodul_name: str, fingerprint: str, status: str) -> str:
    status_icon = "✅" if status == "completed" else "❌"
    return f"# {status_icon} Omodul Report: {omodul_name}\n\n**Fingerprint**: `{fingerprint}`"


def _section_executive_summary(findings: Any, status: str) -> str:
    summary = "Analysis completed successfully." if status == "completed" else "Analysis failed or was interrupted."
    return f"## 1. Executive Summary\n\n{summary}"


def _section_configuration(config: Any) -> str:
    config_data = config.model_dump() if hasattr(config, "model_dump") else str(config)
    config_json = json.dumps(config_data, indent=2, ensure_ascii=False)
    return f"## 2. Configuration\n\n```json\n{config_json}\n```"


def _section_findings_default(findings: Any) -> str:
    if findings is None:
        return "## 3. Findings\n\nNo findings available."
    findings_data = findings.model_dump() if hasattr(findings, "model_dump") else str(findings)
    findings_json = json.dumps(findings_data, indent=2, ensure_ascii=False)
    return f"## 3. Findings\n\n```json\n{findings_json}\n```"


def _section_decision_trail_summary(decision_trail: dict[str, Any]) -> str:
    steps = decision_trail.get("steps", [])
    summary_lines = [f"- Step {s['step_no']}: {s['layer']}.{s['callable']} ({s['status']})" for s in steps]
    summary = "\n".join(summary_lines)
    return f"## 4. Decision Trail Summary\n\nTotal steps: {len(steps)}\n\n{summary}"


def _section_cost_breakdown(cost_tracker: CostTracker) -> str:
    return f"## 5. Cost Breakdown\n\nTotal Cost: ${cost_tracker.total_usd:.4f} USD"


def _section_reproducibility(omodul_name: str, config: Any, fingerprint: str) -> str:
    return (
        f"## 6. Reproducibility\n\n"
        f"To reproduce this analysis, use omodul `{omodul_name}` version `{getattr(config, '_omodul_version', '1.0.0')}` "
        f"with the configuration provided above. Fingerprint `{fingerprint}` matches this specific execution."
    )
