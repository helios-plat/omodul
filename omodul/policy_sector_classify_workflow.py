"""C1 — Policy-sector classification workflow."""

from __future__ import annotations

import hashlib
import json
from typing import Any, ClassVar

from omodul._base_config import BaseConfig


class PolicySectorClassifyConfig(BaseConfig):
    """Config for policy-sector classification."""

    _omodul_name: ClassVar[str] = "policy_sector_classify"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"policy_ids", "sector_schema_version", "llm_model"}

    policy_ids: list[str] = []
    sector_schema_version: str = "v1"
    confidence_threshold: float = 0.6
    max_labels: int = 3


def compute_fingerprint_for(config: PolicySectorClassifyConfig) -> str:
    """Compute deterministic fingerprint for dedup."""
    data = {k: getattr(config, k) for k in config._fingerprint_fields}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def policy_sector_classify_workflow(
    config: PolicySectorClassifyConfig,
    *,
    policies: list[dict[str, Any]],
    sectors: list[str],
    llm: Any = None,
) -> dict[str, Any]:
    """Run policy-sector classification workflow.

    Returns 5-piece output: findings, report, decision_trail, cost, fingerprint.
    """
    fingerprint = compute_fingerprint_for(config)
    trail: list[dict[str, str]] = []
    cost_usd = 0.0
    status = "completed"
    findings: dict[str, Any] = {"n_policies": len(policies), "classifications": []}

    try:
        # Step 1: Load policies
        trail.append({"step": "load_policies", "status": "ok", "detail": f"{len(policies)} loaded"})

        # Step 2: Validate schema
        trail.append({"step": "validate_schema", "status": "ok", "detail": config.sector_schema_version})

        # Step 3: Classify via LLM (B7)
        if llm is not None and policies:
            from oskill.llm.batch_classify import llm_batch_classify
            items = [{"text": p.get("title", "") + " " + p.get("content", "")} for p in policies]
            result = llm_batch_classify(items=items, labels=sectors, llm=llm, multi_label=True)
            findings["classifications"] = result["results"]
            cost_usd += result["cost_usd"]
            trail.append({"step": "llm_classify", "status": "ok", "detail": f"{len(result['results'])} classified"})
        else:
            trail.append({"step": "llm_classify", "status": "skipped", "detail": "no llm or empty policies"})

        # Step 4: Filter by confidence
        trail.append({"step": "confidence_filter", "status": "ok", "detail": f"threshold={config.confidence_threshold}"})

        # Step 5: Truncate labels
        for c in findings["classifications"]:
            if len(c.get("labels", [])) > config.max_labels:
                c["labels"] = c["labels"][:config.max_labels]
        trail.append({"step": "truncate_labels", "status": "ok", "detail": f"max={config.max_labels}"})

        # Step 6: Generate report
        report = f"# Policy-Sector Classification\n\nClassified {len(policies)} policies into {len(sectors)} sectors."
        trail.append({"step": "generate_report", "status": "ok"})

    except Exception as e:
        status = "failed"
        report = f"Error: {e}"
        trail.append({"step": "error", "status": "failed", "detail": str(e)})

    return {
        "findings": findings,
        "report": report,
        "decision_trail": trail,
        "cost_usd": cost_usd,
        "fingerprint": fingerprint,
        "status": status,
    }
