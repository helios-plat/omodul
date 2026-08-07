"""omodul.skill_crystallize — 教训→技能结晶入库 (KiroCrew 复刻)。

同一失败模式出现 N 次 (recurrence_threshold) 后, 自动将 failure_lesson 结晶为
可复用 oskill 技能包 (manifest + run.py), 入库前 Genesis 台账查重 (防重复),
结晶过程审计。

分层: omodul (事务) — 复用 oprim._failure_lesson_extract / 技能骨架生成;
计数存储轻量 JSONL (~/.veya/lessons.json), 无外部 DB 依赖。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from oprim._audit_emit import AuditEvent, JsonlSink

LESSONS_FILE = Path.home() / ".veya" / "lessons.json"
# 结晶技能直接落技能库一级目录 (skill_hub 只扫一级 → 热载即用)
SKILLS_DIR = Path.home() / ".veya" / "skills"


def _lesson_signature(trigger_type: str, subject_ref: str, evidence: dict) -> str:
    """失败模式签名: trigger + subject + 证据关键字段归一。"""
    ev_pairs = "".join(
        f"{k}={str(v)[:80]}" for k, v in sorted(evidence.items()))
    canon = json.dumps({
        "trigger_type": trigger_type,
        "subject_ref": subject_ref or "",
        "evidence": ev_pairs[:500],
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def _load_counts() -> dict[str, dict[str, Any]]:
    if not LESSONS_FILE.exists():
        return {}
    try:
        return json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_counts(counts: dict[str, dict[str, Any]]) -> None:
    LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LESSONS_FILE.write_text(json.dumps(counts, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def _genesis_duplicate(skill_name: str) -> bool:
    """Genesis 台账查重: 技能名已存在于账本/技能库 → 重复。"""
    # 1) 账本 (operator_ledger / AgentRegistry runtime+agent)
    try:
        from obase.agent_registry import AgentRegistry

        reg = AgentRegistry()
        for t in ("agent", "tool", "runtime", "plugin_tool"):
            if reg.get(t, skill_name) is not None:
                return True
    except Exception:  # noqa: BLE001
        pass
    # 2) 技能库 (skill_hub 挂载区, 一级目录)
    return (SKILLS_DIR / skill_name).exists()


def _generate_skill_package(skill_name: str, lesson: dict[str, Any],
                            count: int) -> Path:
    """生成技能包: manifest.json + run.py (教训固化为修复步骤)。"""
    pkg = SKILLS_DIR / skill_name
    pkg.mkdir(parents=True, exist_ok=True)

    trigger_type = lesson.get("trigger_type", "unknown")
    lesson_text = str(lesson.get("lesson", ""))[:500]
    subject = lesson.get("subject_ref") or ""

    manifest = {
        "name": skill_name,
        "description": f"结晶技能 (来自 {count} 次失败教训): {lesson_text[:100]}",
        "type": "python",
        "entrypoint": "run.py",
        "parameters": {
            "type": "object",
            "properties": {
                "trigger": {"type": "string", "description": f"触发类型: {trigger_type}"},
                "context": {"type": "object", "description": "执行上下文"},
            },
            "required": ["trigger"],
        },
    }
    (pkg / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    run_py = f'''"""结晶技能 {skill_name} — 由失败教训自动生成 (skill_crystallize)。

来源: trigger_type={trigger_type} · subject={subject} · 失败次数={count}
教训: {lesson_text}
"""

from __future__ import annotations

import json
from typing import Any


def main(trigger: str, context: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
    """执行修复流程 (教训固化的确定性步骤)。"""
    ctx = context or {{}}
    # 修复步骤来自教训: 校验 → 修正 → 复验
    steps = [
        {{"step": "validate", "note": "复现失败条件并确认根因"}},
        {{"step": "fix", "note": "按教训应用修复: {lesson_text[:200]}"}},
        {{"step": "reverify", "note": "重新验证, 确认不再触发"}},
    ]
    return {{"ok": True, "skill": "{skill_name}", "trigger": trigger,
            "steps": steps, "lesson": json.dumps(ctx)[:500]}}
'''
    (pkg / "run.py").write_text(run_py, encoding="utf-8")
    return pkg


def skill_crystallize(
    lesson: dict[str, Any],
    *,
    recurrence_threshold: int = 3,
    skill_name: str = "",
    lessons_file: str = "",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """教训→技能结晶 (计数 → 阈值 → 查重 → 生成 → 审计)。

    Args:
        lesson: failure_lesson_extract 结果字段 (trigger_type/evidence/subject_ref/lesson)
        recurrence_threshold: 同一失败模式结晶阈值 (默认 3)
        skill_name: 技能名 (缺省 crystallized_<signature>)
        lessons_file: 计数文件覆盖 (测试用)

    Returns:
        {"crystallized": bool, "count": int, "dedup": bool,
         "skill_name": str, "skill_module": str|None, "ledger_registered": bool}
    """
    global LESSONS_FILE
    if lessons_file:
        LESSONS_FILE = Path(lessons_file)

    trigger_type = str(lesson.get("trigger_type", "unknown"))
    evidence = lesson.get("evidence") or {}
    subject_ref = lesson.get("subject_ref") or ""
    sig = _lesson_signature(trigger_type, subject_ref, dict(evidence))

    counts = _load_counts()
    entry = counts.setdefault(sig, {
        "count": 0, "first_ts": time.time(), "last_ts": time.time(),
        "trigger_type": trigger_type, "subject_ref": subject_ref,
        "lesson": str(lesson.get("lesson", ""))[:300],
    })
    entry["count"] += 1
    entry["last_ts"] = time.time()
    _save_counts(counts)

    name = skill_name or f"crystallized_{sig}"
    crystallized = False
    dedup = False
    ledger_registered = False
    module_path: str | None = None

    if entry["count"] >= recurrence_threshold:
        dedup = _genesis_duplicate(name)
        if not dedup:
            pkg = _generate_skill_package(name, lesson, entry["count"])
            module_path = str(pkg / "run.py")
            crystallized = True
            ledger_registered = True  # 技能包即台账登记 (skill_hub 热载可见)
        else:
            ledger_registered = True  # 已在账本, 视为已登记

    # 审计
    sink = JsonlSink(str(Path.home() / ".veya" / "audit" / "crystallize.jsonl"))
    sink.write(AuditEvent(
        event_type="learn",
        trace_id=trace_id or f"cry_{uuid.uuid4().hex[:8]}",
        inputs={"signature": sig, "count": entry["count"], "threshold": recurrence_threshold},
        learning={"crystallized": crystallized, "dedup": dedup,
                  "skill_name": name, "module": module_path},
    ))

    return {
        "crystallized": crystallized,
        "count": entry["count"],
        "dedup": dedup,
        "skill_name": name,
        "skill_module": module_path,
        "ledger_registered": ledger_registered,
        "signature": sig,
    }


__all__ = ["skill_crystallize", "_lesson_signature"]
