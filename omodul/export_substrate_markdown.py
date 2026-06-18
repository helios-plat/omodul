"""omodul.export_substrate_markdown — 端到端导出 substrate 为 markdown 文件。

Pillars: fingerprint + decision_trail + report
Composition:
  - oprim.text_clean_publish_noise
  - oprim.markdown_frontmatter_build
"""
from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report


class ExportSubstrateMarkdownConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "export_substrate_markdown"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {
        "fingerprint", "decision_trail", "report"
    }
    _fingerprint_fields: ClassVar[set[str]] = {"substrate_id", "doc_type"}

    substrate_id: str
    doc_type: str = "book"
    clean_noise: bool = True


class ExportSubstrateMarkdownInput(BaseModel):
    content: str
    metadata: dict[str, Any] = {}


class ExportSubstrateMarkdownFindings(BaseModel):
    substrate_id: str
    file_path: str
    file_size_bytes: int
    cleaned: bool


async def export_substrate_markdown(
    config: ExportSubstrateMarkdownConfig,
    input_data: ExportSubstrateMarkdownInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    """Export substrate content as markdown file with frontmatter.

    Internal oprim composition:
      - oprim.text_clean_publish_noise (optional)
      - oprim.markdown_frontmatter_build

    Example:
        >>> result = await export_substrate_markdown(config, input_data, output_dir)
        >>> result["status"]
        'completed'
    """
    started_at = datetime.now(UTC)
    enabled = config._enabled_pillars
    fingerprint = compute_fingerprint(config, input_data) if "fingerprint" in enabled else None
    cost_tracker = CostTracker(budget_usd=0.0)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings: ExportSubstrateMarkdownFindings | None = None
    dt: dict[str, Any] = {}

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        content = input_data.content

        if config.clean_noise:
            step_start = datetime.now(UTC)
            from oprim.text_clean_publish_noise import text_clean_publish_noise
            content = text_clean_publish_noise(content)
            record_step(
                trail_steps=trail_steps, on_step=on_step,
                layer="oprim", callable_name="text_clean_publish_noise",
                inputs_summary={"chars_before": len(input_data.content)},
                outputs_summary={"chars_after": len(content)},
                started_at=step_start,
            )

        step_start = datetime.now(UTC)
        from oprim.markdown_frontmatter_build import markdown_frontmatter_build
        fm_meta = {"substrate_id": config.substrate_id, "doc_type": config.doc_type,
                   **input_data.metadata}
        frontmatter = markdown_frontmatter_build(fm_meta)
        record_step(
            trail_steps=trail_steps, on_step=on_step,
            layer="oprim", callable_name="markdown_frontmatter_build",
            inputs_summary={"fields": list(fm_meta.keys())},
            outputs_summary={"frontmatter_lines": frontmatter.count("\n")},
            started_at=step_start,
        )

        step_start = datetime.now(UTC)
        title_slug = (input_data.metadata.get("title", config.substrate_id)
                      .replace(" ", "_")[:40])
        out_file = output_dir / f"{title_slug}_{config.substrate_id[:8]}.md"
        out_file.write_text(frontmatter + "\n" + content, encoding="utf-8")
        record_step(
            trail_steps=trail_steps, on_step=on_step,
            layer="oprim", callable_name="file_write",
            inputs_summary={"file": out_file.name},
            outputs_summary={"size_bytes": out_file.stat().st_size},
            started_at=step_start,
        )

        findings = ExportSubstrateMarkdownFindings(
            substrate_id=config.substrate_id,
            file_path=str(out_file),
            file_size_bytes=out_file.stat().st_size,
            cleaned=config.clean_noise,
        )

    except Exception as e:
        error_info = {"error_class": type(e).__name__, "error_message": str(e),
                      "traceback": traceback.format_exc()}
        status = "failed"

    finally:
        if "decision_trail" in enabled:
            dt = build_decision_trail(
                fingerprint=fingerprint or "", config=config, input_data=input_data,
                trail_steps=trail_steps, started_at=started_at,
                status=status, error=error_info, cost_tracker=cost_tracker,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "decision_trail.json").write_text(
                json.dumps(dt, indent=2, ensure_ascii=False, default=str))

    report_path: Path | None = None
    if "report" in enabled and status == "completed" and findings:
        report_path = write_markdown_report(
            output_dir=output_dir, omodul_name=config._omodul_name,
            fingerprint=fingerprint or "", config=config, findings=findings,
            decision_trail=dt, cost_tracker=cost_tracker, status=status,
        )

    return {
        "findings": findings, "status": status, "error": error_info,
        "fingerprint": fingerprint,
        "decision_trail": dt if "decision_trail" in enabled else None,
        "report_path": report_path, "cost_usd": 0.0,
    }


def compute_fingerprint_for(
    config: ExportSubstrateMarkdownConfig,
    input_data: ExportSubstrateMarkdownInput,
) -> str:
    return compute_fingerprint(config, input_data)
