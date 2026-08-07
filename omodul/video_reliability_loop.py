"""omodul.video_reliability_loop — 视频质检可靠性闭环 (与 code_reliability_loop 同构)。

视频生产出片后不能直接上线时, 走:
    生成 → 沙箱质检 → 失败签名 → 有限次返工动作 → 通过|澄清|中止

Veya Loop 只做编排与门禁, 不负责提升生成模型画质。硬性规则:
  * repairs_used >= max_repairs → ABORT (禁止无预算无限重生成);
  * 规格矛盾 (min_duration_s > max_duration_s 等) → CLARIFY;
  * 沙箱崩溃 → ENV 签名, 不崩主进程;
  * 本阶段不自动公开发布 (产物仅标记 merged_candidate / 待人工发布)。

失败签名映射 (issue code → FailureKind → 偏好动作):
  DURATION_*          → SPEC_OR_DURATION → ADJUST_PROMPT / CLARIFY
  RESOLUTION_LOW/ASPECT_* → FORMAT        → ADJUST_PROMPT / REGENERATE
  NO_AUDIO            → AUDIO             → REGENERATE
  PROBE_FAILED/FILE_* → ENV               → CLARIFY / ABORT
  多次同类失败         →                   → ABORT
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from oprim._audit_emit import AuditEmitter, MemorySink


class FailureKind(str, Enum):  # noqa: UP042 - 与 code_reliability_loop 同构
    SPEC_OR_DURATION = "spec_or_duration"
    FORMAT = "format"
    AUDIO = "audio"
    POLICY = "policy"
    ENV = "env"
    OTHER = "other"


@dataclass
class VideoEvalResult:
    """沙箱质检结果 (与 sandbox stdout JSON 对齐)。"""

    passed: bool
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    size_mb: float = 0.0
    issues: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    stderr: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VideoEvalResult:
        return cls(
            passed=bool(data.get("passed", False)),
            duration_s=float(data.get("duration_s") or 0.0),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            fps=float(data.get("fps") or 0.0),
            has_audio=bool(data.get("has_audio")),
            size_mb=float(data.get("size_mb") or 0.0),
            issues=list(data.get("issues") or []),
            metrics=dict(data.get("metrics") or {}),
            stderr=str(data.get("stderr") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "duration_s": self.duration_s,
                "width": self.width, "height": self.height, "fps": self.fps,
                "has_audio": self.has_audio, "size_mb": self.size_mb,
                "issues": self.issues, "metrics": self.metrics,
                "stderr": self.stderr}


@dataclass
class FailureSignature:
    """失败签名: 分类 + 摘要 + 指纹 + 证据 (供返工轮 prompt 与审计)。"""

    kind: FailureKind
    summary: str
    fingerprint: str
    preferred_action: str = "ABORT"
    evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_eval_result(cls, er: VideoEvalResult, task_id: str = "") -> FailureSignature:
        issues = er.issues or []
        first = issues[0] if issues else {}
        code = str(first.get("code") or "UNKNOWN")
        kind, preferred = _map_issue(code, er)
        summary = str(first.get("message") or f"质检失败: {code}")
        evidence = {
            "issues": issues[:10],
            "duration_s": er.duration_s, "width": er.width, "height": er.height,
            "has_audio": er.has_audio, "size_mb": er.size_mb,
            "stderr_tail": (er.stderr or "")[-1000:],
        }
        blob = f"{task_id}|{code}|{er.duration_s}|{er.width}x{er.height}|{er.has_audio}"
        fingerprint = hashlib.sha256(blob.encode()).hexdigest()[:16]
        return cls(kind=kind, summary=summary, fingerprint=fingerprint,
                   preferred_action=preferred, evidence=evidence)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "summary": self.summary,
                "fingerprint": self.fingerprint,
                "preferred_action": self.preferred_action,
                "evidence": self.evidence}


def _map_issue(code: str, er: VideoEvalResult) -> tuple[FailureKind, str]:
    if code.startswith("DURATION_"):
        return FailureKind.SPEC_OR_DURATION, "ADJUST_PROMPT"
    if code.startswith(("RESOLUTION_", "ASPECT_", "FPS_")):
        return FailureKind.FORMAT, "ADJUST_PROMPT"
    if code == "NO_AUDIO":
        return FailureKind.AUDIO, "REGENERATE"
    if code.startswith("LOUDNESS_"):            # v2: 响度不合格
        return FailureKind.AUDIO, "REGENERATE"
    if code.startswith("BLACK_FRAMES_"):        # v2: 黑帧过多
        return FailureKind.FORMAT, "ADJUST_PROMPT"
    if code.startswith("OCR_"):                 # v2: 帧文字/违禁词
        return FailureKind.POLICY, "CLARIFY"
    if code in ("PROBE_FAILED", "FILE_MISSING", "FILE_TOO_LARGE"):
        return FailureKind.ENV, "CLARIFY"
    if code.startswith("POLICY_"):
        return FailureKind.POLICY, "CLARIFY"
    if er.stderr and "sandbox raised" in er.stderr:
        return FailureKind.ENV, "CLARIFY"
    return FailureKind.OTHER, "ABORT"


@dataclass
class VideoArtifact:
    """一轮生成的视频产物 (本地路径/uri + 谱系)。"""

    video_id: str
    video_path: str
    parent_id: str | None = None
    provider: str = "hevi"          # hevi / kling / jimeng / local ...
    note: str = ""
    failure_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoSpec:
    """质检规格 (沙箱 spec 的同构)。"""

    min_duration_s: float = 5.0
    max_duration_s: float = 60.0
    min_width: int = 720
    min_height: int = 720
    aspect_ratios: list[str] = field(default_factory=lambda: ["9:16", "16:9", "1:1"])
    require_audio: bool = True
    max_size_mb: float = 100.0
    platform: str = "generic"

    def validate(self) -> list[str]:
        """规格自检: 矛盾 → 返回错误列表 (CLARIFY 前哨)。"""
        errors: list[str] = []
        if self.min_duration_s > self.max_duration_s:
            errors.append(
                f"min_duration_s({self.min_duration_s}) > "
                f"max_duration_s({self.max_duration_s})"
            )
        if self.min_width < 1 or self.min_height < 1:
            errors.append("min_width/min_height 必须 >= 1")
        if not self.aspect_ratios:
            errors.append("aspect_ratios 不能为空")
        if self.max_size_mb <= 0:
            errors.append("max_size_mb 必须 > 0")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {"min_duration_s": self.min_duration_s,
                "max_duration_s": self.max_duration_s,
                "min_width": self.min_width, "min_height": self.min_height,
                "aspect_ratios": self.aspect_ratios,
                "require_audio": self.require_audio,
                "max_size_mb": self.max_size_mb, "platform": self.platform}


@dataclass
class VideoTask:
    """视频可靠性闭环任务。"""

    task_id: str
    prompt: str
    spec: VideoSpec
    workspace: dict[str, str] = field(default_factory=dict)
    max_repairs: int = 3                 # 硬限制: 禁止无预算死循环


@dataclass
class VideoLoopResult:
    """闭环结果 (产品行为: merged_candidate | clarify | aborted)。"""

    status: str                          # merged_candidate | clarify | aborted
    success: bool
    artifact: VideoArtifact | None = None
    signature: FailureSignature | None = None
    clarify_message: str | None = None
    action_trace: list[dict[str, Any]] = field(default_factory=list)
    repairs_used: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "success": self.success,
                "video_id": self.artifact.video_id if self.artifact else None,
                "signature": self.signature.as_dict() if self.signature else None,
                "clarify_message": self.clarify_message,
                "action_trace": self.action_trace,
                "repairs_used": self.repairs_used}


VideoGenerateFn = Callable[[VideoTask, FailureSignature | None, VideoArtifact | None],
                           VideoArtifact]
VideoEvalFn = Callable[[VideoTask, VideoArtifact], VideoEvalResult]


def run_video_reliability_loop(
    task: VideoTask,
    generate_fn: VideoGenerateFn,
    evaluate_fn: VideoEvalFn,
    *,
    audit_path: str | None = None,
) -> VideoLoopResult:
    """执行视频可靠性闭环 (同步; 生成与质检均为注入 callable)。

    generate_fn: 生成视频 (先 stub / 再接 hevi), 收到 failure signature 时
                 应据 preferred_action 调整 (时长/比例/画质/换 provider)。
    evaluate_fn: 调 VideoSandboxClient (Docker 沙箱) 或 LocalVideoEvaluator。
    audit_path: 审计 JSONL 路径; 缺省进程内 MemorySink。
    """
    sink = MemorySink() if audit_path is None else None
    if audit_path is not None:
        from oprim._audit_emit import JsonlSink

        sink = JsonlSink(audit_path)
    emitter = AuditEmitter(sink=sink, trace_id=f"video_loop_{task.task_id}")

    trace: list[dict[str, Any]] = []
    repairs = 0
    seen_fingerprints: dict[str, int] = {}   # 同类失败计数 → SWITCH_PROVIDER 升级

    def _step(action: str, **kw: Any) -> None:
        entry = {"ts": time.time(), "step": len(trace) + 1, "action": action, **kw}
        trace.append(entry)
        emitter.emit("execute",
                     execution={"primitive": f"video_loop:{action}",
                                "status": "ok" if kw.get("passed", False) else "failed",
                                "capability_nonce": None},
                     context={"task_id": task.task_id,
                              "video_id": kw.get("video_id")})

    # ── 0. 规格自检: 矛盾 → clarify, 不做无谓生成 ──────────────────
    spec_errors = task.spec.validate()
    if spec_errors:
        msg = "视频规格矛盾: " + "; ".join(spec_errors)
        _step("clarify", passed=False, reason="spec_contradiction")
        return VideoLoopResult(status="clarify", success=False,
                               clarify_message=msg, action_trace=trace)

    # ── 1. 初始生成 ─────────────────────────────────────────────────
    parent: VideoArtifact | None = None
    sig: FailureSignature | None = None
    artifact = generate_fn(task, None, None)
    _step("generate", video_id=artifact.video_id, provider=artifact.provider,
          note=artifact.note)

    for round_i in range(task.max_repairs + 1):
        # ── 2. 沙箱质检 ─────────────────────────────────────────────
        try:
            eval_result = evaluate_fn(task, artifact)
        except Exception as exc:                    # 沙箱挂 → env 签名, 不崩主进程
            eval_result = VideoEvalResult(
                passed=False, issues=[{"code": "SANDBOX_ERROR",
                                       "message": f"sandbox raised: {exc}",
                                       "severity": "high"}],
                stderr=f"sandbox raised: {exc}")
        _step("evaluate", video_id=artifact.video_id, passed=eval_result.passed,
              duration_s=round(eval_result.duration_s, 3),
              issues=[i.get("code") for i in (eval_result.issues or [])[:5]])

        if eval_result.passed:
            # 通过 → 待发布候选 (人工发布; 本阶段不自动公开发布)。
            _step("merged_candidate", video_id=artifact.video_id, passed=True)
            emitter.emit("learn", learning={"repairs_used": repairs,
                                            "status": "merged_candidate"})
            return VideoLoopResult(status="merged_candidate", success=True,
                                   artifact=artifact, action_trace=trace,
                                   repairs_used=repairs)

        # ── 3. 失败签名 → 返工轮 (预算检查) ─────────────────────────
        sig = FailureSignature.from_eval_result(eval_result, task.task_id)
        # 同类失败第二次 (同 fingerprint) 且属生成质量类 → 升级 SWITCH_PROVIDER:
        # 换生成器比继续改提示词更有效, 避免同 provider 无限空转。
        seen_fingerprints[sig.fingerprint] = seen_fingerprints.get(sig.fingerprint, 0) + 1
        if (seen_fingerprints[sig.fingerprint] >= 2
                and sig.kind in (FailureKind.FORMAT, FailureKind.AUDIO)):
            sig.preferred_action = "SWITCH_PROVIDER"
            sig.evidence["upgraded_to_switch_provider"] = True
            sig.evidence["repeat_count"] = seen_fingerprints[sig.fingerprint]
        if repairs >= task.max_repairs:
            _step("aborted", video_id=artifact.video_id, passed=False,
                  reason=f"max_repairs={task.max_repairs} 耗尽",
                  fingerprint=sig.fingerprint)
            emitter.emit("learn", learning={"repairs_used": repairs,
                                            "status": "aborted",
                                            "fingerprint": sig.fingerprint})
            return VideoLoopResult(status="aborted", success=False,
                                   signature=sig, action_trace=trace,
                                   repairs_used=repairs)

        repairs += 1
        parent = artifact
        artifact = generate_fn(task, sig, parent)
        _step("repair", video_id=artifact.video_id, round=repairs,
              fingerprint=sig.fingerprint, kind=sig.kind.value,
              preferred_action=sig.preferred_action)

    # 理论不可达 (预算循环已覆盖), 防御性兜底
    return VideoLoopResult(status="aborted", success=False, signature=sig,
                           action_trace=trace, repairs_used=repairs)


__all__ = [
    "FailureKind", "FailureSignature", "VideoArtifact", "VideoEvalFn",
    "VideoEvalResult", "VideoGenerateFn", "VideoLoopResult", "VideoSpec",
    "VideoTask", "run_video_reliability_loop",
]
