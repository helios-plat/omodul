"""omodul.knowledge_reflux —— 纯逻辑知识回溯（知识图自我完备化）。

3O 层级：omodul（可组合的检查集）。标准入口 run_reflux 遵循 3O §5.2 契约
（config, input_data, output_dir → 标准 dict），启用 decision_trail 支柱；
fingerprint/report/cost 暂未启用。reflux() 是纯计算核心，run_reflux 是 3O omodul 包装。

这是 AII 自身变强的主干机制（不是产 skill —— skill 是副产品）。
知识入库/新证据后，确定性地完备化知识图：让 completeness↑、consistency↑、
知识网更稠密。AII 的"强" = 知识图的认识论质量；回溯是提升它的引擎。

边界（守 frontier-audit-004 §四分工）：
  纯逻辑（本模块）= 结构层面的完备性/一致性（封闭世界假设，SHACL 式）
  需 LLM（不在此）= 语义层面判断（两条知识是否"真"矛盾、新证据是否"真"支撑）

HOS-001 §六定义的五项，全部确定性、不调 LLM：
  1. 悬空引用检测   dangling references
  2. 矛盾检测       contradictions（标号冲突、关系环）
  3. 反向关系完备   inverse relation completion
  4. supersede 状态传播
  5. 必要属性校验   required-fields（三面缺一）

报告而非静默修改：回溯产出 RefluxReport（发现 + 建议的状态变更），
高风险变更（如标记 contradicted）默认需人/Governance 裁决（守 ADR-A02/A16）。
低风险补全（反向关系）可自动。No Silent Delete（守 HMS）：从不删节点。

Note: compute_coherence() extracted to oprim.coherence_compute (Batch 3a).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import ConfigDict

from oprim import coherence_compute

from omodul._base_config import BaseConfig

# supersede 等"取代"类关系，用于状态传播
SUPERSEDE_RELATIONS = {"supersedes"}
# 对称需补反向的关系（A contradicts B ⇒ B contradicts A）
SYMMETRIC_RELATIONS = {"contradicts"}
# 需补反向可达的有向关系：正向 -> 反向语义名
INVERSE_RELATIONS = {
    "depends_on": "depended_on_by",
    "supersedes": "superseded_by",
    "supports": "supported_by",
    "produces": "produced_by",
    "explains": "explained_by",
}


@dataclass
class Finding:
    """一条回溯发现。"""

    kind: str  # dangling / contradiction / missing_inverse / supersede_stale / missing_fields
    severity: str  # low（可自动补全）/ high（需裁决）
    subject: str  # 涉及的 ku_id
    detail: dict = field(default_factory=dict)


@dataclass
class RefluxReport:
    """回溯报告：发现 + 建议变更。不静默改图（守 No Silent Delete + 人在裁决位）。"""

    findings: list = field(default_factory=list)
    auto_applied: list = field(default_factory=list)  # 已自动执行的低风险补全（如反向关系）
    needs_review: list = field(default_factory=list)  # 需人/Governance 裁决的高风险建议

    def summary(self) -> dict:
        from collections import Counter

        return {
            "total_findings": len(self.findings),
            "by_kind": dict(Counter(f.kind for f in self.findings)),
            "auto_applied": len(self.auto_applied),
            "needs_review": len(self.needs_review),
        }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class KnowledgeRefluxConfig(BaseConfig):
    """knowledge_reflux 配置."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _omodul_name: ClassVar[str] = "knowledge_reflux"
    _omodul_version: ClassVar[str] = "1.0.0"

    backend: Any = None  # StorageBackend — arbitrary type allowed; None triggers graceful failure
    auto_apply_low: bool = True


# 该 omodul 启用的四支柱子集（3O §5.3，显式声明）
_enabled_pillars: set[str] = {"decision_trail"}


# ---------------------------------------------------------------------------
# Graph snapshot helper
# ---------------------------------------------------------------------------


def _graph_snapshot(backend):
    """读取全图快照：nodes {id: payload}, edges [(src, rel, dst)]。"""
    nodes, edges = {}, []
    if hasattr(backend, "_nodes"):  # InMemoryBackend
        nodes = {nid: backend.get_node(nid) for nid in backend._nodes}
        edges = [(s, r, d) for (s, r, d) in backend._edges]
    else:  # SqlBackend
        cur = backend._conn.execute("SELECT node_id, payload FROM nodes")
        for nid, pj in cur.fetchall():
            nodes[nid] = json.loads(pj)
        cur = backend._conn.execute("SELECT src_id, relation, dst_id FROM edges")
        for s, r, d in cur.fetchall():
            edges.append((s, r, d))
    return nodes, edges


# ---------------------------------------------------------------------------
# 五项纯逻辑检查（每项独立、确定性）
# ---------------------------------------------------------------------------


def check_dangling(nodes, edges) -> list:
    """悬空引用：边的 dst 指向不存在的节点。封闭世界假设。"""
    out = []
    for s, r, d in edges:
        if d not in nodes:
            out.append(Finding("dangling", "high", s, {"relation": r, "missing_dst": d}))
    return out


def check_contradictions(nodes, edges) -> list:
    """矛盾检测（结构层）：
      a) 关系环：A supersedes B 且 B supersedes A
      b) 标号冲突：多个节点 title 同标识符前缀（如两个 ADR-038）
    注：这是结构矛盾。语义矛盾（两条知识内容是否真冲突）需 LLM，不在此。"""
    import re
    from collections import defaultdict

    out = []
    edge_set = {(s, r, d) for s, r, d in edges}
    seen_pairs = set()
    for s, r, d in edges:
        if r in SUPERSEDE_RELATIONS and (d, r, s) in edge_set:
            pair = tuple(sorted([s, d]))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                out.append(
                    Finding(
                        "contradiction",
                        "high",
                        s,
                        {"reason": "supersede_cycle", "with": d, "relation": r},
                    )
                )
    # 标号冲突
    buckets = defaultdict(list)
    for nid, payload in nodes.items():
        title = (payload or {}).get("title", "")
        m = re.match(r"(ADR-\d+)", title)
        if m:
            buckets[m.group(1)].append(nid)
    for tag, ids in buckets.items():
        if len(ids) > 1:
            out.append(
                Finding(
                    "contradiction",
                    "high",
                    ids[0],
                    {"reason": "identifier_conflict", "tag": tag, "nodes": sorted(ids)},
                )
            )
    return out


def check_missing_inverse(nodes, edges) -> list:
    """反向关系不完备：单向声明的关系缺反向可达边。低风险，可自动补。"""
    out = []
    edge_set = {(s, r, d) for s, r, d in edges}
    for s, r, d in edges:
        # 对称关系：缺 (d, r, s)
        if r in SYMMETRIC_RELATIONS and (d, r, s) not in edge_set:
            out.append(
                Finding("missing_inverse", "low", d, {"add_edge": (d, r, s), "reason": "symmetric"})
            )
        # 有向关系：缺反向语义边
        if r in INVERSE_RELATIONS:
            inv = INVERSE_RELATIONS[r]
            if d in nodes and (d, inv, s) not in edge_set:
                out.append(
                    Finding(
                        "missing_inverse",
                        "low",
                        d,
                        {"add_edge": (d, inv, s), "reason": "directed_inverse"},
                    )
                )
    return out


def check_supersede_stale(nodes, edges) -> list:
    """supersede 状态传播：A supersedes B，但 B 的 epistemic_status 未标记被取代。
    建议把 B 标记为 superseded（高风险状态变更，需裁决）。"""
    out = []
    for s, r, d in edges:
        if r in SUPERSEDE_RELATIONS and d in nodes:
            status = (nodes[d] or {}).get("epistemic_status", {})
            if status.get("truth_value") != "superseded":
                out.append(
                    Finding(
                        "supersede_stale",
                        "high",
                        d,
                        {"superseded_by": s, "suggest_status": {"truth_value": "superseded"}},
                    )
                )
    return out


def check_missing_fields(nodes) -> list:
    """三面缺一校验（HOS §一）：KU 须有 symbolic_form / vector / epistemic_status。
    宽松模式：仅对声明了 is_ku=True 的节点检查（兼容旧的非 KU 节点）。"""
    out = []
    for nid, payload in nodes.items():
        if not (payload or {}).get("is_ku"):
            continue
        missing = [f for f in ("symbolic_form", "vector", "epistemic_status") if f not in payload]
        if missing:
            out.append(Finding("missing_fields", "high", nid, {"missing": missing}))
    return out


# ---------------------------------------------------------------------------
# P1-a：融贯度计算（接 ADR-A20）
# compute_coherence extracted to oprim.coherence_compute (Batch 3a).
# INDEPENDENT_SOURCES / GRADE_LADDER / SOURCE_CEILING / helpers kept locally
# for check_coherence logic that still uses SOURCE_CEILING (not in oprim).
# ---------------------------------------------------------------------------

INDEPENDENT_SOURCES = {"formal_proof", "reproducible_empirical", "weak_empirical"}
GRADE_LADDER = ["unverified", "very_low", "low", "moderate", "high", "proven"]
SOURCE_CEILING = {
    "formal_proof": "proven",
    "reproducible_empirical": "high",
    "weak_empirical": "low",
}


def compute_coherence(nodes, edges) -> dict:
    """Backward-compat wrapper — delegates to oprim.coherence_compute."""
    return coherence_compute(nodes=nodes, edges=edges)


def _grade_index(g: str) -> int:
    return GRADE_LADDER.index(g) if g in GRADE_LADDER else 0


def _status_of(node: dict) -> dict:
    """取节点的 completeness 子结构（缺则视为 unverified/无独立来源）。"""
    es = (node or {}).get("epistemic_status", {})
    c = es.get("completeness", {}) if isinstance(es, dict) else {}
    return {
        "grade": c.get("grade", "unverified"),
        "source": c.get("source"),
        "defeaters": list(c.get("defeaters", [])),
    }


def check_coherence(nodes, edges) -> list:
    """基于融贯证据产出 Finding（boost 建议 / defeater 降级建议）。

    死守 A20 三铁律：
      1. 只 boost 不创造：仅对"已有独立确证来源"的知识给 boost 建议
      2. boost 封顶：上调一档且不超过 source 的 ceiling
      3. defeater：来自已确证知识的 contradicts → 触发降级建议（高风险，进 needs_review）
    """
    out = []
    coh = coherence_compute(nodes=nodes, edges=edges)
    for nid, node in nodes.items():
        if not (node or {}).get("is_ku"):
            continue
        st = _status_of(node)
        ev = coh[nid]

        if ev["contradicts_from_confirmed"] > 0:
            cur = st["grade"]
            lowered = GRADE_LADDER[max(0, _grade_index(cur) - 1)]
            out.append(
                Finding(
                    "coherence_defeater",
                    "high",
                    nid,
                    {
                        "reason": "contradicted_by_confirmed",
                        "contradictors": ev["contradictors"],
                        "current_grade": cur,
                        "suggest_grade": lowered,
                    },
                )
            )
            continue

        if st["source"] not in INDEPENDENT_SOURCES:
            if ev["supports_from_confirmed"] > 0:
                out.append(
                    Finding(
                        "coherence_noop",
                        "low",
                        nid,
                        {
                            "reason": "coherent_but_no_independent_warrant",
                            "supports_from_confirmed": ev["supports_from_confirmed"],
                            "note": "自洽≠真：无独立确证，融贯不授予 grade（A20）",
                        },
                    )
                )
            continue

        if ev["supports_from_confirmed"] > 0:
            cur = st["grade"]
            ceiling = SOURCE_CEILING.get(st["source"], cur)
            boosted = GRADE_LADDER[min(_grade_index(ceiling), _grade_index(cur) + 1)]
            if boosted != cur:
                out.append(
                    Finding(
                        "coherence_boost",
                        "low",
                        nid,
                        {
                            "reason": "supported_by_confirmed",
                            "supporters": ev["supporters"],
                            "current_grade": cur,
                            "suggest_grade": boosted,
                            "ceiling": ceiling,
                        },
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Pure computation core
# ---------------------------------------------------------------------------


def _apply_grade(backend, ku_id: str, new_grade: str) -> None:
    """把某 KU 的 completeness.grade 改为 new_grade。
    仅用于低风险融贯 boost 的自动应用。No Silent Delete：只改 grade，不删节点。"""
    node = backend.get_node(ku_id)
    if node is None:
        return
    es = node.setdefault("epistemic_status", {})
    c = es.setdefault("completeness", {})
    c["grade"] = new_grade
    backend.put_node(ku_id, node)


def reflux(backend, *, auto_apply_low: bool = True) -> RefluxReport:
    """执行一轮纯逻辑知识回溯，完备化知识图。

    参数：
        backend:        StorageBackend，知识图
        auto_apply_low: 是否自动执行低风险补全（反向关系）。高风险一律只建议。

    返回：RefluxReport（发现 + 已自动补全 + 待裁决）。
    """
    nodes, edges = _graph_snapshot(backend)
    report = RefluxReport()

    findings = []
    findings += check_dangling(nodes, edges)
    findings += check_contradictions(nodes, edges)
    findings += check_missing_inverse(nodes, edges)
    findings += check_supersede_stale(nodes, edges)
    findings += check_missing_fields(nodes)
    findings += check_coherence(nodes, edges)
    report.findings = findings

    for f in findings:
        if f.severity == "low" and f.kind == "missing_inverse" and auto_apply_low:
            s, r, d = f.detail["add_edge"]
            backend.put_edge(s, r, d)
            report.auto_applied.append(f)
        elif f.kind == "coherence_boost" and auto_apply_low:
            _apply_grade(backend, f.subject, f.detail["suggest_grade"])
            report.auto_applied.append(f)
        elif f.kind == "coherence_noop":
            report.needs_review.append(f)
        else:
            report.needs_review.append(f)

    return report


# ---------------------------------------------------------------------------
# 标准 omodul 包装（3O §5.2 契约）
# ---------------------------------------------------------------------------


def run_reflux(
    config: KnowledgeRefluxConfig,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """标准 omodul 签名（3O §5.2）。知识图自我完备化入口。

    config:     KnowledgeRefluxConfig（含 backend + auto_apply_low）
    input_data: {} （回溯作用于 backend 全图，无需额外输入）
    output_dir: decision_trail.json 落盘目录（None 不落盘，仍返回 trail）
    on_step:    每步回调（可选）

    返回（omodul 标准 dict）：
        findings:    RefluxReport（失败 None）
        status:      "completed" | "failed"
        error:       失败原因（成功 None）
        decision_trail: 回溯轨迹
        report_path / cost_usd: 未启用

    失败不 raise（3O §5.12）。
    """
    if isinstance(config, dict):
        config = KnowledgeRefluxConfig(**config) if config else KnowledgeRefluxConfig()
    trail: list[dict] = []
    findings = None
    status = "failed"
    error = None

    def _emit(step_dict: dict) -> None:
        trail.append(step_dict)
        if on_step is not None:
            on_step(step_dict)

    try:
        backend = config.backend
        auto_apply_low = config.auto_apply_low
        _emit({"step": "reflux_start", "auto_apply_low": auto_apply_low})

        report = reflux(backend, auto_apply_low=auto_apply_low)

        _emit(
            {
                "step": "reflux_done",
                "summary": report.summary(),
                "auto_applied": len(report.auto_applied),
                "needs_review": len(report.needs_review),
            }
        )
        findings = report
        status = "completed"
    except Exception as e:
        error = {"code": "ERR_REFLUX", "message": str(e)}
        _emit({"step": "abort", "error": error})

    decision_trail = {
        "omodul": "knowledge_reflux",
        "enabled_pillars": sorted(_enabled_pillars),
        "status": status,
        "trail": trail,
    }

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "decision_trail.json"), "w", encoding="utf-8") as f:
            json.dump(decision_trail, f, ensure_ascii=False, indent=2, default=str)

    return {
        "findings": findings,
        "status": status,
        "error": error,
        "decision_trail": decision_trail,
        "report_path": None,
        "cost_usd": 0.0,
    }
