"""Auditable market-evidence review transaction (no model or trading side effects)."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class MarketEvidenceReportConfig(BaseConfig):
    """Validated run parameters for an evidence review and durable report."""

    _omodul_name: ClassVar[str] = "market_evidence_report"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"market", "as_of_date", "stale_after_hours", "numeric_tolerance"}

    market: str = "unspecified"
    as_of_date: str
    stale_after_hours: float = 24.0
    numeric_tolerance: float = 0.0


def compute_fingerprint_for(config: MarketEvidenceReportConfig, input_data: dict[str, Any]) -> str:
    """Compute the standard 3O fingerprint; identity keys are filtered by the shared helper."""
    return compute_fingerprint(config, input_data)


def _markdown_findings(findings: dict[str, Any]) -> str:
    review = findings["review"]
    summary = review.get("summary") or {
        "source_count": 0,
        "independent_current_source_count": 0,
        "stale_source_count": 0,
        "unverified_source_count": 0,
        "duplicate_source_count": 0,
        "claim_count": 0,
        "conflict_count": 0,
    }
    lines = [
        "# Market Evidence Review",
        "",
        f"- Run status: **{findings['status']}**",
        f"- Evidence review status: **{review['status']}**",
        f"- Fingerprint: `{findings['fingerprint']}`",
        f"- Ledger: `{findings['ledger_path']}`",
        "",
        "## Summary",
        "",
        f"- Sources assessed: {summary['source_count']}",
        f"- Independent current sources: {summary['independent_current_source_count']}",
        f"- Stale sources: {summary['stale_source_count']}",
        f"- Unverified sources: {summary['unverified_source_count']}",
        f"- Duplicate sources: {summary['duplicate_source_count']}",
        f"- Claims checked: {summary['claim_count']}",
        f"- Conflicts: {summary['conflict_count']}",
        "",
        "## Source assessments",
        "",
    ]
    if not review["sources"]:
        lines.append("No source records were supplied.")
    for row in review["sources"]:
        lines.append(f"### {row['source_id']} — {row['status']}")
        lines.append("")
        lines.append(f"- URI: {row.get('source_uri') or 'missing'}")
        lines.append(f"- Quality index: {row.get('score', 0)} (not a probability of truth)")
        lines.append(f"- Evidence record persisted: {row.get('ledger_persisted', False)}")
        lines.append(f"- Duplicate content/URI: {row.get('duplicate_content_or_uri', False)}")
        if row.get("reasons"):
            lines.append(f"- Reasons: {', '.join(row['reasons'])}")
        lines.append("")
    lines.extend(["## Claim-to-source audit", ""])
    if review.get("claims"):
        for claim in review["claims"]:
            lines.append(
                f"- **{claim['subject']} / {claim['predicate']}** — {claim['status']} "
                f"(source `{claim['source_id']}`, reason: {claim['reason']})."
            )
    else:
        lines.append("No claims were supplied.")
    if review.get("orphan_claims"):
        lines.append("")
        lines.append(f"Unresolved source references: {len(review['orphan_claims'])}.")
    lines.append("")
    lines.extend(["## Conflicts", ""])
    if review["conflicts"]:
        for conflict in review["conflicts"]:
            lines.append(
                f"- **{conflict['subject']} / {conflict['predicate']}** "
                f"({conflict.get('unit') or 'unit not supplied'}): "
                f"{', '.join(conflict['values'])}; source IDs: {', '.join(conflict['source_ids'])}."
            )
    else:
        lines.append("No conflicts were found among the provided claims.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in review["limitations"])
    lines.extend([
        "- This report assesses only supplied data; it does not fetch current market data.",
        "- A completed workflow can legitimately report `unverified` or `conflict` evidence.",
    ])
    return "\n".join(lines) + "\n"


def market_evidence_report(
    config: MarketEvidenceReportConfig,
    input_data: dict[str, Any],
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run source review, persist eligible content-addressed evidence, and write a report.

    `input_data` accepts `sources` (list of source mappings) and `claims` (list of
    normalized claim mappings). Evidence persistence is restricted to the output
    directory; the run does no vendor IO and never takes a trading action.
    """
    from obase.cost_tracker import CostTracker
    from obase.evidence_ledger import EvidenceRecordError, append_evidence_record, build_evidence_record
    from oskill.market_evidence_review import review_market_evidence

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    trail_steps: list[dict[str, Any]] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint_for(config, input_data)
    ledger_path = output_dir / f"evidence_{fingerprint[:12]}.jsonl"
    report_path = output_dir / f"market_evidence_report_{fingerprint[:12]}.md"
    status = "completed"
    error: dict[str, Any] | None = None
    review: dict[str, Any] = {"status": "unverified", "summary": {}, "sources": [], "conflicts": [], "limitations": []}
    persisted: list[dict[str, Any]] = []

    try:
        step_start = datetime.now(UTC)
        if not isinstance(input_data, dict):
            raise ValueError("input_data must be a dict")
        sources = input_data.get("sources", [])
        claims = input_data.get("claims", [])
        if not isinstance(sources, list) or not isinstance(claims, list):
            raise ValueError("sources and claims must be lists")
        if any(not isinstance(source, dict) for source in sources):
            raise ValueError("each source must be a dict")
        record_step(
            trail_steps=trail_steps, on_step=on_step, layer="oprim",
            callable_name="validate_market_evidence_input",
            inputs_summary={"source_count": len(sources), "claim_count": len(claims)},
            outputs_summary={"valid_shapes": True}, started_at=step_start,
        )

        step_start = datetime.now(UTC)
        review = review_market_evidence(
            {"sources": sources, "claims": claims},
            now_utc=input_data.get("now_utc"),
            stale_after_hours=config.stale_after_hours,
            numeric_tolerance=config.numeric_tolerance,
        )
        record_step(
            trail_steps=trail_steps, on_step=on_step, layer="oskill",
            callable_name="review_market_evidence",
            inputs_summary={"source_count": len(sources), "claim_count": len(claims)},
            outputs_summary={"review_status": review["status"], "conflict_count": review["summary"]["conflict_count"]},
            started_at=step_start,
        )

        step_start = datetime.now(UTC)
        persisted_by_id: dict[str, dict[str, Any]] = {}
        ledger_errors: list[str] = []
        for source in sources:
            source_id = str(source.get("source_id") or source.get("evidence_id") or "").strip()
            try:
                retrieved_at = source.get("retrieved_at_utc") or source.get("retrieved_at")
                source_uri = source.get("source_uri") or source.get("url")
                payload = source.get("payload")
                if payload is None:
                    # Store only a curated public-metadata subset when a dedicated payload is absent.
                    payload = {key: source[key] for key in ("symbol", "interval", "unit", "currency", "value", "close", "volume", "status") if key in source}
                safe_metadata = {
                    key: source[key]
                    for key in ("provider", "symbol", "interval", "unit", "currency", "timezone", "tool_name")
                    if key in source and isinstance(source[key], (str, int, float, bool, type(None)))
                }
                record = build_evidence_record(
                    source_type=str(source.get("source_type") or "unknown"),
                    source_uri=source_uri,
                    retrieved_at=retrieved_at,
                    payload=payload,
                    query=source.get("query") if isinstance(source.get("query"), str) else None,
                    tool_name=source.get("tool_name") if isinstance(source.get("tool_name"), str) else None,
                    metadata=safe_metadata,
                )
                append_result = append_evidence_record(ledger_path, record)
                persisted_record = {"source_id": source_id, "evidence_id": record["evidence_id"], **append_result}
                persisted.append(persisted_record)
                persisted_by_id[source_id] = persisted_record
            except (EvidenceRecordError, TypeError, ValueError) as exc:
                ledger_errors.append(f"{source_id or 'unnamed_source'}:{type(exc).__name__}")
        for row in review["sources"]:
            saved = persisted_by_id.get(str(row["source_id"]))
            row["ledger_persisted"] = saved is not None
            if saved:
                row["evidence_id"] = saved["evidence_id"]
            elif sources:
                row.setdefault("reasons", []).append("evidence_record_not_persisted")
        if ledger_errors:
            record_step(
                trail_steps=trail_steps, on_step=on_step, layer="oprim_batch",
                callable_name="persist_evidence_records",
                inputs_summary={"source_count": len(sources)},
                outputs_summary={"persisted_count": len(persisted), "rejected_count": len(ledger_errors)},
                started_at=step_start, status="partial",
                error={"type": "EvidenceRecordError", "message": "Some source records did not meet the evidence-ledger contract."},
            )
        else:
            record_step(
                trail_steps=trail_steps, on_step=on_step, layer="oprim_batch",
                callable_name="persist_evidence_records",
                inputs_summary={"source_count": len(sources)},
                outputs_summary={"persisted_count": len(persisted), "rejected_count": 0},
                started_at=step_start,
            )

    except Exception as exc:
        status = "failed"
        error = {
            "type": type(exc).__name__,
            "message": "Evidence review failed validation or processing; no unsupported claim was promoted.",
        }
        step_start = datetime.now(UTC)
        record_step(
            trail_steps=trail_steps, on_step=on_step, layer="oskill",
            callable_name="market_evidence_report_error",
            inputs_summary={"input_type": type(input_data).__name__},
            outputs_summary={"status": "failed"}, started_at=step_start,
            status="failed", error=error,
        )

    if status == "failed":
        report_path = output_dir / f"market_evidence_report_{fingerprint[:12]}.partial.md"

    findings = {"review": review, "evidence_records": persisted, "ledger_path": str(ledger_path), "status": status, "fingerprint": fingerprint}
    decision_trail = build_decision_trail(
        fingerprint=fingerprint, config=config, input_data=input_data,
        trail_steps=trail_steps, cost_tracker=cost_tracker, started_at=started_at,
        status=status, error=error,
    )
    if "report" in config._enabled_pillars:
        report_path.write_text(_markdown_findings({**findings, "review": review}), encoding="utf-8")
    return {
        "findings": findings,
        "status": status,
        "error": error,
        "fingerprint": fingerprint if "fingerprint" in config._enabled_pillars else None,
        "decision_trail": decision_trail if "decision_trail" in config._enabled_pillars else None,
        "cost_usd": cost_tracker.total_usd if "cost" in config._enabled_pillars else None,
        "report_path": report_path if "report" in config._enabled_pillars else None,
        "ledger_path": ledger_path,
        "evidence_records": persisted,
    }
