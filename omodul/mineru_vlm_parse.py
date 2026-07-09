"""
omodul.mineru_vlm_parse — parse a document with MinerU VLM (parse-once, fork-twice).

Pillars: fingerprint, decision_trail, report, cost
Fingerprint fields: file_path, mineru_server_url

MINERU-AII-INTEGRATION-SPEC-001 Track B §1: one parse, two forks — rendered
markdown (human-readable, e.g. for a Stratum-style reader) and structured
elements (tables/equations/reading-order, for downstream KU extraction) come
from the SAME parse call, not a lossy re-parse of the flattened markdown.

Deliberately independent of any project-specific caller (e.g. aii's own
math_ocr_convert.py, which manages a specific container-lifecycle/GPU-lock
convention for its own pipeline) — this omodul only needs an already-running
MinerU VLM server URL (OpenAI-compatible, e.g. `vllm serve
opendatalab/MinerU2.5-2509-1.2B --logits-processors
mineru_vl_utils:MinerULogitsProcessor`) and calls it via the `mineru` CLI's
`vlm-http-client` backend. Any 3O consumer can point this at their own
server; it does not assume aii's specific container name/port conventions.

cost: local VLM inference via a self-hosted server has no per-token API
cost — cost_usd stays 0.0 unless a future cloud-backed server URL changes
that (out of scope here; R2 forbids that path anyway for the aii use case
this was built for).

Real field names/output filenames below were verified empirically (not
assumed from docs) — mineru 3.4.3 installed locally, ran end-to-end on a
real single-page PDF with the `pipeline` backend (CPU) 2026-07-09. The
`vlm-http-client` backend's output filenames follow the same
`<stem>/auto/<stem>_middle.json` pattern; not independently re-verified
against a live MinerU VLM server (GPU on the reference host is hardware-
faulted as of this writing), but the CLI's output-path logic is backend-
agnostic (see mineru/cli/common.py), so this is a reasonable, not a blind,
extrapolation — flagged here rather than silently asserted as identical.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig,
    CostTracker,
    Trail,
    build_result,
    compute_fingerprint,
    write_report,
)


class MineruVlmConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "mineru_vlm_parse"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set] = {"fingerprint", "decision_trail", "report", "cost"}
    _fingerprint_fields: ClassVar[set] = {"file_path", "mineru_server_url"}

    file_path: str
    mineru_server_url: str = "http://127.0.0.1:8012"
    timeout_sec: float = 600.0


class MineruVlmInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)


def compute_fingerprint_for_mineru_vlm_parse(file_path: str, mineru_server_url: str) -> str:
    return compute_fingerprint({"file_path": file_path, "mineru_server_url": mineru_server_url})


def _notify(on_step: Any, step: str, state: str) -> None:
    if on_step is not None:
        try:
            on_step(step=step, state=state)
        except Exception:
            pass


async def mineru_vlm_parse(
    config: MineruVlmConfig,
    input_data: MineruVlmInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Parse one document via a running MinerU VLM server, once, into both forks.

    Returns a standard build_result dict with fingerprint, decision_trail,
    report_path (rendered markdown — the human-readable fork), and findings
    including middle_json/content_list (the structured-element fork, for
    downstream KU extraction — see MINERU-AII-INTEGRATION-SPEC-001 §1).
    """
    trail = Trail()
    cost = CostTracker()
    fingerprint = compute_fingerprint_for_mineru_vlm_parse(
        config.file_path, config.mineru_server_url
    )

    try:
        src = Path(config.file_path)
        trail.record(event="start", file_path=config.file_path, fingerprint=fingerprint)
        if not src.exists():
            raise FileNotFoundError(f"file_path does not exist: {src}")

        _notify(on_step, "parse", "started")
        work_dir = Path(tempfile.mkdtemp(prefix="mineru_vlm_", dir=str(output_dir)))
        proc = subprocess.run(
            [
                "mineru",
                "-p",
                str(src),
                "-o",
                str(work_dir),
                "-b",
                "vlm-http-client",
                "-u",
                config.mineru_server_url,
            ],
            capture_output=True,
            text=True,
            timeout=config.timeout_sec,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"mineru CLI failed (exit {proc.returncode}): {proc.stderr[-2000:]}")
        trail.record(event="parse_done")
        _notify(on_step, "parse", "done")

        stem = src.stem
        auto_dir = work_dir / stem / "auto"
        middle_path = auto_dir / f"{stem}_middle.json"
        content_list_path = auto_dir / f"{stem}_content_list.json"
        md_path = auto_dir / f"{stem}.md"
        if not middle_path.exists():
            raise RuntimeError(f"mineru ran but expected output not found: {middle_path}")

        middle_json = json.loads(middle_path.read_text(encoding="utf-8"))
        content_list = (
            json.loads(content_list_path.read_text(encoding="utf-8"))
            if content_list_path.exists()
            else []
        )
        md_content = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        trail.record(
            event="output_read",
            page_count=len(middle_json.get("pdf_info", [])),
            content_blocks=len(content_list),
        )

        # Fork 1 (human-readable): the rendered markdown, written as the report.
        report_path = write_report(
            md_content,
            output_dir=output_dir,
            name=f"mineru_vlm_{fingerprint[:8]}",
            fmt="markdown",
        )

        trail_path = trail.write(output_dir)

        # Fork 2 (structured, for KU extraction): middle_json/content_list
        # returned as findings, not flattened — caller decides what to do
        # with tables/interline_equations/etc. Real field names per
        # verified schema (module docstring): each page's structured
        # elements live as a "type" field on blocks in para_blocks/
        # discarded_blocks, not as separate top-level arrays.
        findings = {
            "middle_json": middle_json,
            "content_list": content_list,
            "backend": "vlm-http-client",
            "page_count": len(middle_json.get("pdf_info", [])),
        }

        return build_result(
            status="completed",
            error=None,
            fingerprint=fingerprint,
            trail=trail,
            trail_path=trail_path,
            report_path=str(report_path),
            cost_usd=cost.total_usd,
            **findings,
        )

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )
