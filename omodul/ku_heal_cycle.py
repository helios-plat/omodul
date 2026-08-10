"""Deterministic KU health healing cycle."""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result
from omodul.ku_health import list_open_issues, mark_resolved
from omodul.ku_lint import KuLintConfig, KuLintInput, _fingerprint, _nodes, lint_knowledge_units


class KuHealCycleConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "ku_heal_cycle"
    _omodul_version: ClassVar[str] = "1.0.0"
    issue_dir: Path
    unverified_grade_ratio_warn: float = 0.6


class KuHealCycleInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    knowledge_units: list[dict]
    backend: Any | None = None


def _remove_target(value: Any, target: str) -> Any:
    if isinstance(value, str):
        return None if value == target else value
    if isinstance(value, list):
        return [item for item in value if str(item.get("ku_id", item.get("id", item.get("target", ""))) if isinstance(item, dict) else item) != target]
    if isinstance(value, dict):
        return None if str(value.get("ku_id", value.get("id", value.get("target", "")))) == target else value
    return value


def _put(backend: Any, ku_id: str, ku: dict) -> None:
    if backend is not None and hasattr(backend, "put_node"):
        backend.put_node(ku_id, ku)


def _all_units(input_data: KuHealCycleInput) -> dict[str, dict]:
    return _nodes(input_data)


def _fix(issue: dict, units: dict[str, dict], backend: Any) -> tuple[str, bool, str]:
    rule, ku_id = issue["rule"], issue["ku_id"]
    ku = units.get(ku_id)
    if ku is None:
        return "skipped", False, "KU not found"
    if rule == "duplicate_fingerprint":
        fp = issue["detail"].rsplit(" ", 1)[-1]
        # 保留输入中最早出现的 KU (units 保持插入顺序 = 注册顺序), 其余 deprecated
        matches = [(kid, item) for kid, item in units.items()
                   if _fingerprint(item) == fp]
        keeper = matches[0][0] if matches else ku_id
        if ku_id == keeper:
            return "keep_earliest_duplicate", True, f"kept {keeper}"
        ku["status"] = "deprecated"
        _put(backend, ku_id, ku)
        return "deprecate_duplicate", True, f"deprecated duplicate; kept {keeper}"
    if rule == "broken_relation":
        detail = issue["detail"]
        field = detail.split(" references ", 1)[0]
        target = detail.rsplit(" ", 1)[-1]
        if field in ku:
            new_value = _remove_target(ku[field], target)
            if new_value is None or new_value == []:
                ku.pop(field, None)
            else:
                ku[field] = new_value
            _put(backend, ku_id, ku)
        return "remove_broken_relation", True, f"removed {target} from {field}"
    if rule == "orphan_ku":
        ku["orphan"] = True
        _put(backend, ku_id, ku)
        return "mark_orphan", True, "marked orphan; no deletion"
    if rule == "stale_grade":
        ku["stale_grade"] = True
        _put(backend, ku_id, ku)
        return "mark_stale", True, "marked stale; grade unchanged"
    return "unknown", False, f"unsupported rule {rule}"


def ku_heal_cycle(config: KuHealCycleConfig, input_data: KuHealCycleInput, output_dir: Path | None = None, *, on_step: Any = None) -> dict:
    config = config if isinstance(config, KuHealCycleConfig) else KuHealCycleConfig.model_validate(config)
    input_data = input_data if isinstance(input_data, KuHealCycleInput) else KuHealCycleInput.model_validate(input_data)
    issue_dir = config.issue_dir
    trail = Trail()
    actions: list[dict] = []
    issues = list_open_issues(issue_dir)
    trail.record(event="fetch_open_issues", issue_count=len(issues))
    if on_step:
        try:
            on_step("fetch_open_issues", "done")
        except TypeError:
            on_step({"step": "fetch_open_issues", "state": "done"})
    units = _all_units(input_data)
    for issue in issues:
        action, ok, detail = _fix(issue, units, input_data.backend)
        trail.record(event="attempt_deterministic_fix", issue_id=issue["issue_id"], action=action, ok=ok)
        current = lint_knowledge_units(KuLintConfig(unverified_grade_ratio_warn=config.unverified_grade_ratio_warn), KuLintInput(knowledge_units=list(input_data.knowledge_units), backend=input_data.backend))
        remaining = [i for i in current if i["rule"] == issue["rule"] and i["ku_id"] == issue["ku_id"]]
        verified = not remaining or issue["rule"] in {"orphan_ku", "stale_grade"}
        if issue["rule"] == "duplicate_fingerprint":
            fp = issue["detail"].rsplit(" ", 1)[-1]
            verified = sum(1 for item in units.values() if item.get("status") != "deprecated" and _fingerprint(item) == fp) <= 1
        if verified and ok:
            mark_resolved(issue_dir, issue["issue_id"])
        actions.append({"issue_id": issue["issue_id"], "action": action, "ok": bool(ok and verified), "detail": detail if verified else f"verification failed: {remaining}"})
    trail.record(event="verify", actions=len(actions))
    # Reconcile duplicate groups after all members have been processed: the
    # keeper cannot verify until the duplicate has been deprecated.
    remaining_open = list_open_issues(issue_dir)
    final_issues = lint_knowledge_units(KuLintConfig(unverified_grade_ratio_warn=config.unverified_grade_ratio_warn), KuLintInput(knowledge_units=list(input_data.knowledge_units), backend=input_data.backend))
    for issue in remaining_open:
        if issue["rule"] == "duplicate_fingerprint" and not any(item["rule"] == issue["rule"] and item["ku_id"] == issue["ku_id"] for item in final_issues):
            mark_resolved(issue_dir, issue["issue_id"])
            for action in actions:
                if action["issue_id"] == issue["issue_id"]:
                    action["ok"] = True
    trail.record(event="mark_resolved", resolved=sum(1 for item in actions if item["ok"]))
    target = output_dir or issue_dir
    target.mkdir(parents=True, exist_ok=True)
    trail_path = trail.write(target)
    return build_result(status="completed", error=None, trail=trail, trail_path=trail_path, cost_usd=0.0, findings={"actions": actions, "issues_processed": len(issues)})
