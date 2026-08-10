"""kg_reasoning — 确定性知识推理 (3O operator, 移植 semantica Datalog/Rete 核心)。

前向链接 Datalog 子集:
- Facts: (predicate, subject, object)
- Rules: head :- body1, body2, ...   (变量 X/Y/Z, 常量小写)
- 输出: 推导出的新事实 + 每条的推导链 (可解释路径, 非黑盒)

应用: 概念图上的传递推理 / 约束校验 / 策略推导 (stratum 概念图 / ku_lint 增强)。
纯确定性, 零 LLM, 零网络。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_VAR_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")


# ── 原子与规则 ───────────────────────────────────────────────────────

class Atom:
    """predicate(term1, term2) — term 是常量或变量。"""

    __slots__ = ("predicate", "terms")

    def __init__(self, predicate: str, terms: list[str]):
        self.predicate = predicate
        self.terms = terms

    @classmethod
    def parse(cls, text: str) -> "Atom":
        m = re.match(r"^(\w+)\s*\((.*)\)\s*$", text.strip())
        if not m:
            raise ValueError(f"原子格式非法: {text!r} (应为 predicate(a, b))")
        pred = m.group(1)
        terms = [t.strip() for t in m.group(2).split(",") if t.strip()]
        return cls(pred, terms)

    def is_ground(self) -> bool:
        return all(not _VAR_RE.match(t) for t in self.terms)

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.predicate}({', '.join(self.terms)})"


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """按顶层分隔符切分 (括号深度感知, 原子内部逗号不拆)。"""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def parse_rule(text: str) -> tuple[Atom, list[Atom]]:
    """'head :- body1, body2' → (head, [bodies])。无 ':-' 视为事实。"""
    text = text.strip()
    if ":-" in text:
        head_s, body_s = text.split(":-", 1)
        head = Atom.parse(head_s)
        parts = _split_top_level(body_s, ",") or _split_top_level(body_s, ";")
        bodies = [Atom.parse(b) for b in parts if b.strip()]
        return head, bodies
    return Atom.parse(text), []


# ── 前向链接推理 ─────────────────────────────────────────────────────

def forward_chain(
    facts: list[tuple[str, str, str]],
    rules: list[tuple[Atom, list[Atom]]],
    max_iterations: int = 10,
    max_facts: int = 10_000,
) -> tuple[set[tuple[str, str, str]], dict[tuple[str, str, str], list[str]]]:
    """前向链接: 返回 (推导事实集, {fact: 推导链证据})。

    facts: [(predicate, subject, object)]; 规则头/体中的变量按最一般合一匹配。
    """
    known: set[tuple[str, str, str]] = set(facts)
    evidence: dict[tuple[str, str, str], list[str]] = {f: ["[base fact]"] for f in facts}

    def match_atom(atom: Atom, binding: dict[str, str], fact: tuple[str, str, str]) -> bool:
        terms = (fact[1], fact[2])
        if len(atom.terms) != len(terms):
            return False
        if atom.predicate != fact[0]:
            return False
        for pat, val in zip(atom.terms, terms):
            if _VAR_RE.match(pat):
                if pat in binding:
                    if binding[pat] != val:
                        return False
                else:
                    binding[pat] = val
            elif pat != val:
                return False
        return True

    for _ in range(max_iterations):
        new_count = 0
        for head, bodies in rules:
            if head.predicate.startswith("_"):  # 辅助谓词不产出
                continue
            for fact_tuple in list(known):
                pass  # 占位: 变量绑定按 body 顺序联合
            # 联合匹配: 对每个 body 用已知事实枚举绑定
            candidates = _enumerate_bindings(bodies, known)
            for binding in candidates:
                terms = [binding.get(t, t) for t in head.terms]
                new_fact = (head.predicate, terms[0], terms[1] if len(terms) > 1 else terms[0])
                if new_fact not in known:
                    known.add(new_fact)
                    evidence[new_fact] = [
                        f"rule: {head!r} :- " + ", ".join(repr(b) for b in bodies)
                        + f"  via {binding}"
                    ]
                    new_count += 1
                    if len(known) >= max_facts:
                        return known, evidence
        if new_count == 0:
            break
    return known, evidence


def _enumerate_bindings(bodies: list[Atom], known: set[tuple[str, str, str]]) -> list[dict[str, str]]:
    """对规则体做联合绑定枚举 (小规模; 每个 body 依次找匹配事实)。"""
    bindings: list[dict[str, str]] = [{}]
    for atom in bodies:
        nxt: list[dict[str, str]] = []
        for b in bindings:
            for f in known:
                candidate = dict(b)
                if _match_with_binding(atom, f, candidate):
                    nxt.append(candidate)
        bindings = nxt
        if not bindings:
            return []
    return bindings


def _match_with_binding(atom: Atom, fact: tuple[str, str, str], binding: dict[str, str]) -> bool:
    terms = (fact[1], fact[2])
    if atom.predicate != fact[0] or len(atom.terms) != len(terms):
        return False
    for pat, val in zip(atom.terms, terms):
        if _VAR_RE.match(pat):
            if pat in binding:
                if binding[pat] != val:
                    return False
            else:
                binding[pat] = val
        elif pat != val:
            return False
    return True


# ── Config / Input ────────────────────────────────────────────────────

class KgReasoningConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "kg_reasoning"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}
    max_iterations: int = 10


class KgReasoningInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    facts: list[list[str]] = []          # [[predicate, subject, object], ...]
    rules: list[str] = []                # "head :- body1, body2" 或 "pred(a, b)"
    query: str = ""                      # 可选: 目标原子 (返回是否可推导 + 证据)
    max_facts: int = 10_000
    backend: Any | None = None           # 可选: 概念图数据源 (list_triples())


# ── operator 入口 ─────────────────────────────────────────────────────

def kg_reasoning(
    config: KgReasoningConfig,
    input_data: KgReasoningInput,
    output_dir: Path | None = None,
    *,
    on_step: Any = None,
) -> dict:
    """确定性前向推理: 从事实 + 规则推导新事实, 附带可解释推导链。

    facts 可用 backend.list_triples() 注入 (stratum 概念图); backend=None 时
    用输入 facts。query 指定目标原子时, 返回推导证据链 (可解释性)。
    """
    config = KgReasoningConfig.model_validate(config)
    input_data = KgReasoningInput.model_validate(input_data)
    trail = Trail()

    facts: list[tuple[str, str, str]] = [tuple(f) for f in input_data.facts if len(f) >= 3]
    if input_data.backend is not None:
        try:
            extra = input_data.backend.list_triples() or []
            facts.extend(tuple(t) for t in extra if len(t) >= 3)
        except Exception:  # noqa: BLE001 — 数据源故障不阻塞
            pass
    rules = [parse_rule(r) for r in input_data.rules]

    known, evidence = forward_chain(
        facts, rules,
        max_iterations=config.max_iterations,
        max_facts=input_data.max_facts,
    )
    trail.record(event="forward_chain", base_facts=len(facts), derived=len(known) - len(facts))

    findings: dict[str, Any] = {
        "base_facts": len(facts),
        "derived_facts": len(known) - len(facts),
        "derived": sorted((list(f) for f in known if f not in set(facts)), key=str),
    }
    if input_data.query.strip():
        qatom = Atom.parse(input_data.query)
        if not qatom.is_ground():
            raise ValueError("query 必须是地面原子 (无变量)")
        target = (qatom.predicate, qatom.terms[0], qatom.terms[1] if len(qatom.terms) > 1 else qatom.terms[0])
        derivable = target in known
        findings["query"] = input_data.query.strip()
        findings["derivable"] = derivable
        findings["explanation"] = evidence.get(target, []) if derivable else []

    trail_path = trail.write(Path(output_dir) if output_dir else Path.cwd())
    return build_result(
        status="completed", error=None,
        fingerprint=compute_fingerprint({"facts": len(facts), "rules": len(rules)}),
        trail=trail, trail_path=trail_path,
        cost_usd=0.0, findings=findings,
    )
