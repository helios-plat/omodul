"""
omodul._base — omodul 基础设施
================================
BaseConfig / 支柱工具 / 共享类型

所有 omodul 继承 BaseConfig，使用此模块的支柱工具完成：
- fingerprint 计算
- decision_trail 记录与落盘
- cost 累计
- report 生成
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# BaseConfig (§5.3 SPEC v2.1)
# ---------------------------------------------------------------------------

class BaseConfig(BaseModel):
    """所有 omodul 的基础配置."""
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    output_format: Literal["markdown", "pdf", "both"] = "markdown"
    budget_usd: float = 5.0
    overwrite: bool = True

    _omodul_name: ClassVar[str] = ""
    _omodul_version: ClassVar[str] = ""
    _fingerprint_fields: ClassVar[set[str]] = set()
    _enabled_pillars: ClassVar[set[str]] = set()


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------

@dataclass
class CostTracker:
    """并发安全的成本追踪器（对象内累加，不替换引用）."""
    total_usd: float = 0.0
    in_tokens: int = 0
    out_tokens: int = 0
    calls: int = 0

    # 默认价格表
    _PRICING: ClassVar[dict[str, dict]] = {
        "claude-sonnet-4-6": {"in": 3e-6, "out": 15e-6},
        "claude-opus-4-6":   {"in": 15e-6, "out": 75e-6},
        "claude-haiku-4-5":  {"in": 0.8e-6, "out": 4e-6},
    }
    _FALLBACK: ClassVar[dict] = {"in": 3e-6, "out": 15e-6}

    def add(self, *, in_tok: int, out_tok: int, model: str = "claude-sonnet-4-6") -> float:
        p = self._PRICING.get(model, self._FALLBACK)
        cost = in_tok * p["in"] + out_tok * p["out"]
        self.total_usd += cost
        self.in_tokens += in_tok
        self.out_tokens += out_tok
        self.calls += 1
        return cost

    def add_from_response(self, response: dict, *, model: str = "claude-sonnet-4-6") -> float:
        usage = response.get("usage", {})
        in_tok = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        out_tok = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        return self.add(in_tok=in_tok, out_tok=out_tok, model=model)

    def exceeds(self, budget: float) -> bool:
        return self.total_usd > budget  # pragma: no cover


# ---------------------------------------------------------------------------
# Trail (decision_trail)
# ---------------------------------------------------------------------------

@dataclass
class Trail:
    """decision_trail 记录器."""
    steps: list[dict] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def record(self, *, event: str, step_no: int | None = None, **kwargs) -> None:
        sno = step_no if step_no is not None else len(self.steps) + 1
        self.steps.append({"step_no": sno, "ts": time.time(), "event": event, **kwargs})

    def write(self, output_dir: Path, suffix: str = "") -> Path:
        path = output_dir / f"decision_trail_{self.run_id}{suffix}.json"
        path.write_text(json.dumps(self.steps, ensure_ascii=False, indent=2))
        return path


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------

def compute_fingerprint(fields: dict[str, Any]) -> str:
    """sha256(canonical JSON of fields)[:24]."""
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# 标准返回结构构建
# ---------------------------------------------------------------------------

def build_result(
    *,
    status: str,
    error: dict | None = None,
    fingerprint: str | None = None,
    trail: Trail | None = None,
    trail_path: Path | None = None,
    report_path: Path | None = None,
    cost_usd: float = 0.0,
    **extra,
) -> dict:
    """构建符合 §5 标准的 omodul 返回 dict."""
    result: dict[str, Any] = {
        "status": status,
        "error": error,
        **extra,
    }
    if fingerprint is not None:
        result["fingerprint"] = fingerprint
    if trail is not None:
        result["decision_trail"] = {
            "path": str(trail_path) if trail_path else None,
            "steps": len(trail.steps),
            "run_id": trail.run_id,
        }
    if report_path is not None:
        result["report_path"] = report_path
    result["cost_usd"] = round(cost_usd, 6)
    return result


# ---------------------------------------------------------------------------
# LLM 调用工具
# ---------------------------------------------------------------------------

async def llm_call(
    messages: list[dict],
    *,
    caller: Any,
    cost: CostTracker,
    trail: Trail,
    model: str,
    max_tokens: int = 4096,
    event: str = "llm_call",
    tools: list[dict] | None = None,
) -> dict:
    """统一的 LLM 调用 + cost 累加 + trail 记录."""
    trail.record(event=event, n_messages=len(messages))
    response = await caller(messages=messages, tools=tools, max_tokens=max_tokens)
    cost.add_from_response(response, model=model)
    return response


def extract_text(response: dict) -> str:
    """从 LLM 响应中提取纯文本."""
    content = response.get("content", [])
    if isinstance(content, str):
        return content  # pragma: no cover
    return "".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


# ---------------------------------------------------------------------------
# report 写盘工具
# ---------------------------------------------------------------------------

def write_report(
    content: str,
    *,
    output_dir: Path,
    name: str,
    fmt: str = "markdown",
) -> Path:
    """写 markdown 报告到 output_dir."""
    ext = ".md" if fmt in ("markdown", "both") else ".txt"
    path = output_dir / f"{name}{ext}"
    path.write_text(content, encoding="utf-8")
    return path
