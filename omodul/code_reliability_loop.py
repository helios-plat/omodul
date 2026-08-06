"""omodul.code_reliability_loop — 代码 Agent 可靠性闭环事务 (方案 A+C)。

把「生成 → 沙箱测试 → 失败签名 → 修复轮(≤max_repairs) → 合并/澄清/中止」编排成
一条可停、可回滚、可审计的闭环:

    generate_fn(task, sig?, parent?) → PatchArtifact
    test_fn(task, patch)            → TestResult
    run_code_reliability_loop 编排:
      0. spec_quality 低 → clarify (不写危险补丁)
      1. 初始生成 → 测试 → 全过 → merged_candidate
      2. 失败 → FailureSignature (分类 + 指纹) → 修复轮 ≤ max_repairs
      3. 预算耗尽 → aborted + signature + action_trace
      4. 超时/env 错误 → signature.kind 标记, 不崩主进程

审计: 所有修复动作写入 AuditEmitter (可选, 缺省 MemorySink; 可接 JsonlSink 落 JSONL)。

硬约束: max_repairs 上限, 禁止无预算自动死循环重写; aborted 后必须人工确认
才能再开新 Loop (由调用方执行)。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from oprim._audit_emit import AuditEmitter, MemorySink


class FailureKind(str, Enum):
    TEST_FAILURE = "test_failure"
    TIMEOUT = "timeout"
    ENV_ERROR = "env_error"
    OTHER = "other"


@dataclass
class TestResult:
    """沙箱测试结果 (与 sandbox 协议 JSON 对齐)。"""

    passed: bool
    n_passed: int = 0
    n_failed: int = 0
    duration_s: float = 0.0
    stdout: str = ""
    stderr: str = ""
    failed_nodeids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TestResult":
        return cls(
            passed=bool(data.get("passed", False)),
            n_passed=int(data.get("n_passed") or 0),
            n_failed=int(data.get("n_failed") or 0),
            duration_s=float(data.get("duration_s") or 0.0),
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            failed_nodeids=list(data.get("failed_nodeids") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "n_passed": self.n_passed,
                "n_failed": self.n_failed, "duration_s": self.duration_s,
                "stdout": self.stdout, "stderr": self.stderr,
                "failed_nodeids": self.failed_nodeids,
                "metadata": self.metadata}


@dataclass
class FailureSignature:
    """失败签名: 分类 + 摘要 + 指纹 + 证据 (供修复轮 prompt 与审计)。"""

    kind: FailureKind
    summary: str
    fingerprint: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_test_result(cls, tr: TestResult, task_id: str = "") -> "FailureSignature":
        if tr.metadata.get("timeout") or "timeout" in (tr.stderr or "").lower():
            kind, summary = FailureKind.TIMEOUT, "沙箱/测试执行超时"
        elif tr.metadata.get("env_error"):
            kind, summary = FailureKind.ENV_ERROR, "沙箱环境错误"
        elif not tr.passed:
            kind, summary = FailureKind.TEST_FAILURE, (
                f"{tr.n_failed} 个测试失败: {', '.join(tr.failed_nodeids[:5])}"
                if tr.failed_nodeids else "测试失败 (无 nodeid 明细)")
        else:
            kind, summary = FailureKind.OTHER, "未知失败"
        evidence = {
            "failed_nodeids": tr.failed_nodeids[:10],
            "stderr_tail": (tr.stderr or "")[-2000:],
            "stdout_tail": (tr.stdout or "")[-1000:],
            "n_passed": tr.n_passed,
            "n_failed": tr.n_failed,
            "duration_s": tr.duration_s,
            "metadata": tr.metadata,
        }
        blob = f"{task_id}|{kind.value}|{tr.stderr[-800:]}|{sorted(tr.failed_nodeids)}"
        fingerprint = hashlib.sha256(blob.encode()).hexdigest()[:16]
        return cls(kind=kind, summary=summary, fingerprint=fingerprint,
                   evidence=evidence)

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind.value, "summary": self.summary,
                "fingerprint": self.fingerprint, "evidence": self.evidence}


@dataclass
class PatchArtifact:
    """一轮生成的产物 (文件集 + 谱系)。"""

    patch_id: str
    files: Dict[str, str]
    parent_patch_id: Optional[str] = None
    note: str = ""


@dataclass
class CodeTask:
    """可靠性闭环任务。"""

    task_id: str
    spec: str
    tests: List[str]
    workspace: Dict[str, str] = field(default_factory=dict)
    spec_quality: float = 1.0            # < SPEC_QUALITY_THRESHOLD → clarify
    max_repairs: int = 3                 # 硬限制: 禁止无预算死循环


SPEC_QUALITY_THRESHOLD = 0.7


@dataclass
class CodeLoopResult:
    """闭环结果 (产品行为: merged_candidate | clarify | aborted)。"""

    status: str                          # merged_candidate | clarify | aborted
    success: bool
    patch: Optional[PatchArtifact] = None
    signature: Optional[FailureSignature] = None
    clarify_message: Optional[str] = None
    action_trace: List[Dict[str, Any]] = field(default_factory=list)
    repairs_used: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "success": self.success,
                "patch_id": self.patch.patch_id if self.patch else None,
                "signature": self.signature.as_dict() if self.signature else None,
                "clarify_message": self.clarify_message,
                "action_trace": self.action_trace,
                "repairs_used": self.repairs_used}


GenerateFn = Callable[[CodeTask, Optional[FailureSignature], Optional[PatchArtifact]],
                     PatchArtifact]
TestFn = Callable[[CodeTask, PatchArtifact], TestResult]


def run_code_reliability_loop(
    task: CodeTask,
    generate_fn: GenerateFn,
    test_fn: TestFn,
    *,
    audit_path: Optional[str] = None,
) -> CodeLoopResult:
    """执行可靠性闭环 (同步; 生成与测试均为注入 callable)。

    audit_path: 审计 JSONL 路径; 缺省进程内 MemorySink。
    """
    sink = MemorySink() if audit_path is None else None
    if audit_path is not None:
        from oprim._audit_emit import JsonlSink

        sink = JsonlSink(audit_path)
    emitter = AuditEmitter(sink=sink, trace_id=f"code_loop_{task.task_id}")

    trace: List[Dict[str, Any]] = []
    repairs = 0

    def _step(action: str, **kw: Any) -> None:
        entry = {"ts": time.time(), "step": len(trace) + 1, "action": action, **kw}
        trace.append(entry)
        emitter.emit("execute",
                     execution={"primitive": f"code_loop:{action}",
                                "status": "ok" if kw.get("passed", False) else "failed",
                                "capability_nonce": None},
                     context={"task_id": task.task_id, "patch_id": kw.get("patch_id")})

    # ── 0. 规格质量门禁: 低质量 → clarify, 不写危险补丁 ──────────────
    if task.spec_quality < SPEC_QUALITY_THRESHOLD:
        msg = (f"规格质量评分 {task.spec_quality:.2f} < {SPEC_QUALITY_THRESHOLD}: "
               "请补充可验证的测试/验收条件后重试")
        _step("clarify", passed=False, reason="spec_quality_too_low")
        return CodeLoopResult(status="clarify", success=False,
                              clarify_message=msg, action_trace=trace)

    # ── 1. 初始生成 ─────────────────────────────────────────────────
    parent: Optional[PatchArtifact] = None
    sig: Optional[FailureSignature] = None
    patch = generate_fn(task, None, None)
    _step("generate", patch_id=patch.patch_id, note=patch.note)

    for round_i in range(task.max_repairs + 1):
        # ── 2. 沙箱测试 ─────────────────────────────────────────────
        try:
            tr = test_fn(task, patch)
        except Exception as exc:                    # 沙箱崩 → env_error 签名, 不崩主进程
            tr = TestResult(passed=False, n_failed=1, stderr=f"sandbox raised: {exc}",
                            metadata={"env_error": True})
        _step("test", patch_id=patch.patch_id, passed=tr.passed,
              n_passed=tr.n_passed, n_failed=tr.n_failed,
              duration_s=round(tr.duration_s, 3),
              metadata=tr.metadata)

        if tr.passed:
            _step("merged_candidate", patch_id=patch.patch_id, passed=True)
            emitter.emit("learn", learning={"repairs_used": repairs,
                                            "status": "merged_candidate"})
            return CodeLoopResult(status="merged_candidate", success=True,
                                  patch=patch, action_trace=trace,
                                  repairs_used=repairs)

        # ── 3. 失败签名 → 修复轮 (预算检查) ─────────────────────────
        sig = FailureSignature.from_test_result(tr, task.task_id)
        if repairs >= task.max_repairs:
            _step("aborted", patch_id=patch.patch_id, passed=False,
                  reason=f"max_repairs={task.max_repairs} 耗尽",
                  fingerprint=sig.fingerprint)
            emitter.emit("learn", learning={"repairs_used": repairs,
                                            "status": "aborted",
                                            "fingerprint": sig.fingerprint})
            return CodeLoopResult(status="aborted", success=False,
                                  signature=sig, action_trace=trace,
                                  repairs_used=repairs)

        repairs += 1
        parent = patch
        patch = generate_fn(task, sig, parent)
        _step("repair", patch_id=patch.patch_id, round=repairs,
              fingerprint=sig.fingerprint, kind=sig.kind.value)

    # 理论不可达 (预算循环已覆盖), 防御性兜底
    return CodeLoopResult(status="aborted", success=False, signature=sig,
                          action_trace=trace, repairs_used=repairs)


__all__ = ["CodeLoopResult", "CodeTask", "FailureKind", "FailureSignature",
           "GenerateFn", "PatchArtifact", "SPEC_QUALITY_THRESHOLD", "TestFn",
           "TestResult", "run_code_reliability_loop"]
