"""omodul.mission_supervisor — Merge 前自动化审计 (Vigla Supervisor 复刻)。

多 Worker 代码改动合入前, 三通道扫描:
  1. 秘钥泄漏 (默认高危模式库, 可 policy 扩展)
  2. 路径越界 (相对工作区根的白名单之外)
  3. 禁止操作 (forbidden_ops: 删除保护文件/改写敏感路径等)

输出 verdict: approve | request_changes | block; 全程审计入不可变日志。

分层: omodul (事务) — 复用 oprim._audit_emit / hitl_approval_workflow。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oprim._audit_emit import AuditEvent, JsonlSink

# 默认高危秘钥模式库 (policy.secret_patterns 缺省使用)
DEFAULT_SECRET_PATTERNS: list[dict[str, str]] = [
    {"name": "aws_access_key", "pattern": r"AKIA[0-9A-Z]{16}"},
    {"name": "github_token", "pattern": r"ghp_[A-Za-z0-9]{36}"},
    {"name": "openai_key", "pattern": r"sk-[A-Za-z0-9]{20,}"},
    {"name": "anthropic_key", "pattern": r"sk-ant-[A-Za-z0-9_-]{20,}"},
    {"name": "private_key_block", "pattern": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"},
    {"name": "generic_secret_assignment",
     "pattern": r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{12,}['\"]"},
]

# 保护文件: 出现在 diff 中即违规 (forbidden_ops 缺省)
DEFAULT_PROTECTED_PATHS: list[str] = [
    ".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519",
    "secrets/", "config/security.yaml", "credentials.json",
]

VERDICTS = ("approve", "request_changes", "block")


@dataclass
class SupervisorPolicy:
    """审计策略: 秘钥模式 + 路径白名单 + 禁止操作。"""

    secret_patterns: list[dict[str, str]] = field(default_factory=list)
    path_allowlist: list[str] = field(default_factory=list)   # 允许改写的路径前缀
    forbidden_ops: list[str] = field(default_factory=list)    # 禁止操作标记
    protected_paths: list[str] = field(default_factory=list)  # 保护文件 glob

    def effective_secrets(self) -> list[dict[str, str]]:
        return self.secret_patterns or DEFAULT_SECRET_PATTERNS

    def effective_protected(self) -> list[str]:
        return self.protected_paths or DEFAULT_PROTECTED_PATHS


@dataclass
class DiffEntry:
    """结构化 diff 条目 (路径 + 状态 + 内容)。"""

    path: str
    status: str                  # added | modified | deleted | renamed
    content: str = ""


def parse_diff(raw_diff: str, base: str = "") -> list[DiffEntry]:
    """git diff --no-color 文本 → 结构化条目 (含删除/新增段内容)。"""
    entries: list[DiffEntry] = []
    cur: DiffEntry | None = None
    for line in raw_diff.splitlines():
        if line.startswith("diff --git"):
            if cur is not None:
                entries.append(cur)
            m = re.search(r"diff --git a/(\S+) b/(\S+)", line)
            path = m.group(2) if m else "?"
            cur = DiffEntry(path=path, status="modified")
        elif line.startswith("new file mode") and cur is not None:
            cur.status = "added"
        elif line.startswith("deleted file mode") and cur is not None:
            cur.status = "deleted"
        elif line.startswith("rename from") and cur is not None:
            cur.status = "renamed"
        elif (line.startswith(("+", "-")) and cur is not None
              and not line.startswith(("+++", "---"))):
            cur.content += line + "\n"
    if cur is not None:
        entries.append(cur)
    return entries


def _path_violation(path: str, policy: SupervisorPolicy) -> str | None:
    """路径检查: 白名单外 or 保护文件命中。"""
    p = Path(path)
    # 保护文件 (glob 匹配)
    import fnmatch

    for pat in policy.effective_protected():
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(str(p), pat):  # noqa: E501
            return f"保护文件被修改: {path}"
    # 白名单: 有白名单时, 路径必须在其下
    if policy.path_allowlist:
        allowed = any(
            str(p).startswith(a.rstrip("/")) or str(p.parent).startswith(a.rstrip("/"))
            for a in policy.path_allowlist
        )
        if not allowed:
            return f"路径越界 (白名单外): {path}"
    return None


def mission_supervisor(
    mission_id: str,
    worktree_diff: str,
    policy: dict[str, Any] | None = None,
    *,
    trace_id: str | None = None,
    sink: JsonlSink | None = None,
    llm_reviewer: Any | None = None,
) -> dict[str, Any]:
    """Merge 前审计: diff → 三通道扫描 → verdict + violations + 审计。

    Args:
        mission_id: 任务标识
        worktree_diff: git diff 原始文本
        policy: {"secret_patterns": [...], "path_allowlist": [...],
                "forbidden_ops": [...], "protected_paths": [...]}
        llm_reviewer: 可选 diff 摘要函数 (callable(diff) -> str), 缺省规则摘要
    """
    pol = SupervisorPolicy(
        secret_patterns=(policy or {}).get("secret_patterns") or [],
        path_allowlist=(policy or {}).get("path_allowlist") or [],
        forbidden_ops=(policy or {}).get("forbidden_ops") or [],
        protected_paths=(policy or {}).get("protected_paths") or [],
    )
    entries = parse_diff(worktree_diff)
    violations: list[str] = []

    # 1) 秘钥泄漏
    for entry in entries:
        for rule in pol.effective_secrets():
            if re.search(rule["pattern"], entry.content):
                violations.append(
                    f"秘钥泄漏 [{rule['name']}]: {entry.path}")

    # 2) 路径越界 / 保护文件
    for entry in entries:
        if v := _path_violation(entry.path, pol):
            violations.append(v)

    # 3) 禁止操作
    for entry in entries:
        if entry.status == "deleted":
            if any(op in entry.path for op in pol.forbidden_ops):
                violations.append(f"禁止删除操作: {entry.path}")

    # verdict: block(秘钥/保护文件) > request_changes(路径越界/删除) > approve
    if any(("秘钥" in v or "保护文件" in v) for v in violations):
        verdict = "block"
    elif violations:
        verdict = "request_changes"
    else:
        verdict = "approve"

    # diff 摘要 (LLM 可选, 缺省规则统计)
    if llm_reviewer is not None:
        try:
            diff_review = str(llm_reviewer(worktree_diff))[:2000]
        except Exception:  # noqa: BLE001
            diff_review = ""
    else:
        diff_review = (
            f"{len(entries)} 文件变更: "
            + ", ".join(f"{e.status} {e.path}" for e in entries[:10])
        )

    # 审计 (不可变日志)
    if sink is None:
        from pathlib import Path

        sink = JsonlSink(str(Path.home() / ".veya" / "audit" / "supervisor.jsonl"))
    audit = AuditEvent(
        event_type="decide",
        trace_id=trace_id or f"ms_{mission_id}",
        inputs={"mission_id": mission_id, "files": len(entries)},
        decision={"verdict": verdict, "violations": violations, "diff_review": diff_review},
    )
    sink.write(audit)

    return {
        "verdict": verdict,
        "violations": violations,
        "diff_review": diff_review,
        "files_reviewed": len(entries),
        "audit_id": audit.audit_id,
    }


__all__ = ["mission_supervisor", "parse_diff", "SupervisorPolicy",
           "DEFAULT_SECRET_PATTERNS", "DiffEntry"]
