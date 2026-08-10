"""Deterministic KU health linting."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result
from omodul.ku_health import persist_issues


class KuLintConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "ku_lint"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}
    unverified_grade_ratio_warn: float = 0.6


class KuLintInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    knowledge_units: list[dict]
    backend: Any | None = None


def _nodes(input_data: KuLintInput) -> dict[str, dict]:
    nodes = {str(ku.get("ku_id", ku.get("id", ""))): ku for ku in input_data.knowledge_units}
    backend = input_data.backend
    if backend is not None:
        if isinstance(getattr(backend, "nodes", None), dict):
            nodes.update({str(k): v for k, v in backend.nodes.items()})
        elif hasattr(backend, "list_nodes") and hasattr(backend, "get_node"):
            nodes.update({str(k): backend.get_node(k) for k in backend.list_nodes()})
        elif hasattr(backend, "_nodes") and hasattr(backend, "get_node"):
            nodes.update({str(k): backend.get_node(k) for k in backend._nodes})
    return {k: v for k, v in nodes.items() if k and isinstance(v, dict)}


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return value


def _fingerprint(ku: dict) -> str:
    if ku.get("fingerprint"):
        return str(ku["fingerprint"])
    basis = {k: v for k, v in ku.items() if k not in {"ku_id", "id", "fingerprint"}}
    canonical = json.dumps(_canonical(basis), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        return [str(value.get("ku_id", value.get("id", value.get("target", ""))))]
    if isinstance(value, (list, tuple, set)):
        return [str(item.get("ku_id", item.get("id", item.get("target", ""))) if isinstance(item, dict) else item) for item in value]
    return []


def _issue(rule: str, ku_id: str, severity: str, detail: str) -> dict:
    return {"rule": rule, "ku_id": ku_id, "severity": severity, "detail": detail}


def lint_knowledge_units(config: KuLintConfig, input_data: KuLintInput) -> list[dict]:
    nodes = _nodes(input_data)
    issues: list[dict] = []
    now = datetime.now(UTC)
    unverified = 0
    fingerprints: dict[str, list[str]] = {}
    for ku_id, ku in nodes.items():
        if ku.get("grade", "unverified") == "unverified":
            unverified += 1
        if not any(_ids(ku.get(field)) for field in ("substrate", "substrate_id", "sources")):
            issues.append(_issue("orphan_ku", ku_id, "warn", "KU has no substrate reference"))
        for field in ("superseded_by", "same_as", "related_to"):
            for target in _ids(ku.get(field)):
                if target not in nodes:
                    issues.append(_issue("broken_relation", ku_id, "error", f"{field} references missing KU {target}"))
        if ku.get("status") != "deprecated":
            fingerprints.setdefault(_fingerprint(ku), []).append(ku_id)
        valid_until = ku.get("valid_until")
        if valid_until:
            try:
                expiry = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                if expiry < now:
                    issues.append(_issue("stale_grade", ku_id, "warn", "valid_until has expired"))
            except ValueError:
                issues.append(_issue("stale_grade", ku_id, "warn", "valid_until is not a valid timestamp"))
    if nodes and unverified / len(nodes) > config.unverified_grade_ratio_warn:
        for ku_id, ku in nodes.items():
            if ku.get("grade", "unverified") == "unverified":
                issues.append(_issue("stale_grade", ku_id, "warn", "unverified grade ratio exceeds threshold"))
    for fingerprint, ids in fingerprints.items():
        if len(ids) > 1:
            for ku_id in ids:
                issues.append(_issue("duplicate_fingerprint", ku_id, "warn", f"duplicate fingerprint {fingerprint}"))
    return issues


def ku_lint(config: KuLintConfig, input_data: KuLintInput, output_dir: Path, *, on_step: Any = None) -> dict:
    config = KuLintConfig.model_validate(config)
    input_data = KuLintInput.model_validate(input_data)
    trail = Trail()
    issues = lint_knowledge_units(config, input_data)
    trail.record(event="lint", ku_total=len(_nodes(input_data)), issue_count=len(issues))
    if on_step:
        try:
            on_step("ku_lint", "done")
        except TypeError:
            on_step({"step": "ku_lint", "state": "done"})
    issue_path = persist_issues(output_dir, issues)
    by_rule: dict[str, int] = {}
    for item in issues:
        by_rule[item["rule"]] = by_rule.get(item["rule"], 0) + 1
    findings = {"issue_count": len(issues), "issues": issues, "by_rule": by_rule, "ku_total": len(_nodes(input_data))}
    trail_path = trail.write(output_dir)
    return build_result(status="completed", error=None, fingerprint=_fingerprint({"knowledge_units": input_data.knowledge_units}), trail=trail, trail_path=trail_path, cost_usd=0.0, findings=findings, issues_path=str(issue_path))
