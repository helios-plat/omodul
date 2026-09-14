"""Engineering qualification transaction; no merge or deployment authority."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from oskill.engineering_qualification import (
    evaluate_contract,
    evaluate_proven_red,
    evaluate_ratchet,
)

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint, write_report


class ChangeQualificationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "qualify_change"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}


async def qualify_change(config, input_data, output_dir, *, on_step=None) -> dict:
    """Evaluate contract, proven-red, test evidence, and ratchet in order."""
    config = (
        config
        if isinstance(config, ChangeQualificationConfig)
        else ChangeQualificationConfig.model_validate(config or {})
    )
    data = input_data if isinstance(input_data, dict) else dict(input_data)
    trail = Trail()
    fingerprint = compute_fingerprint(
        {"config": config.model_dump(), "change": data.get("change", data)}
    )
    common = {
        "qualified": False,
        "contract": {},
        "proven_red": {},
        "ratchet": {},
        "tests": data.get("tests", {}),
        "blockers": [],
    }
    try:
        trail.record(event="contract")
        contract = evaluate_contract(
            contract=data.get("contract", {}),
            observed=data.get("observed", {}),
            evidence=data.get("evidence", []),
            policy=data.get("policy"),
        )
        if not contract["passed"]:
            common["blockers"].append("contract")
        trail.record(event="proven_red")
        proven = evaluate_proven_red(
            claim=data.get("claim", {}),
            pre_change_evidence=data.get("pre_change_evidence", []),
            failure_contract=data.get("failure_contract", {}),
        )
        if not proven["proven_red"]:
            common["blockers"].append("proven_red")
        trail.record(event="ratchet")
        ratchet = evaluate_ratchet(
            baseline=data.get("baseline", {}),
            candidate=data.get("candidate", {}),
            rules=data.get("rules", {}),
        )
        if not ratchet["passed"]:
            common["blockers"].append("ratchet")
        tests = data.get("tests", {})
        if isinstance(tests, dict) and (tests.get("unknown") or tests.get("failed")):
            common["blockers"].append("tests")
        qualified = not common["blockers"]
        status = "qualified" if qualified else "not_qualified"
        if on_step:
            on_step({"status": status, "qualified": qualified})
        trail.record(event=status)
        result = build_result(
            status="completed",
            error=None,
            qualified=qualified,
            contract=contract,
            proven_red=proven,
            ratchet=ratchet,
            tests=tests,
            blockers=common["blockers"],
            fingerprint=fingerprint,
            trail=trail,
        )
        if output_dir and "report" in config._enabled_pillars:
            result["report_path"] = write_report(
                str(result), output_dir=Path(output_dir), name="change_qualification"
            )
        return result
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": "execution_failed", "message": str(exc)},
            **common,
            fingerprint=fingerprint,
            trail=trail,
        )


__all__ = ["ChangeQualificationConfig", "qualify_change"]
