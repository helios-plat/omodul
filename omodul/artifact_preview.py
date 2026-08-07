"""omodul.artifact_preview — Artifact 独立渲染/消毒/快照 (pi-workbench 复刻)。

对 Agent 产出内容 (markdown/html/svg/echarts_json/code) 提供:
  - 消毒: 剥离恶意 HTML/脚本 (script/事件属性/javascript: URL/foreignObject)
  - 校验: 结构可渲染性 (echarts_json 校验 option 结构)
  - 快照: 持久化到 ~/.veya/artifacts/ (snapshot_id), 可回放
  - issues: 结构化问题列表供 agent 自修

分层: omodul (事务) — 纯机制, 零 veya 反向依赖; 实际渲染由装配层/前端完成。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

ARTIFACT_TYPES = ("markdown", "html", "svg", "echarts_json", "code")
SNAPSHOT_DIR = Path.home() / ".veya" / "artifacts"

# 消毒正则 (HTML/SVG 通用)
_SCRIPT_RE = re.compile(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)
_STYLE_ON_RE = re.compile(r"(<[^>]+)\s+style\s*=\s*\"[^\"]*\"", re.IGNORECASE)
_EVENT_ATTR_RE = re.compile(r"\s+on\w+\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"(?i)\b(href|src|action)\s*=\s*['\"]\s*javascript:")
_DATA_HTML_RE = re.compile(r"(?i)\b(src)\s*=\s*['\"]\s*data:text/html")
_FOREIGN_OBJECT_RE = re.compile(r"<\s*foreignObject\b[^>]*>.*?<\s*/\s*foreignObject\s*>",
                                re.IGNORECASE | re.DOTALL)

# 允许的 HTML 标签 (其余剥离)
_ALLOWED_TAGS = {
    "p", "div", "span", "h1", "h2", "h3", "h4", "ul", "ol", "li", "table",
    "thead", "tbody", "tr", "td", "th", "a", "img", "pre", "code", "blockquote",
    "strong", "em", "b", "i", "br", "hr", "svg", "path", "rect", "circle",
    "g", "text", "defs", "linearGradient", "stop", "polygon", "line", "polyline",
}

_SNAPSHOT_EXT: dict[str, str] = {
    "markdown": "md", "html": "html", "svg": "svg",
    "echarts_json": "json", "code": "txt",
}


def sanitize_markup(content: str, kind: str) -> tuple[str, list[str]]:
    """HTML/SVG 消毒: 返回 (消毒后内容, 移除项 issues)。"""
    issues: list[str] = []
    original_len = len(content)
    n_script = len(_SCRIPT_RE.findall(content))
    content = _SCRIPT_RE.sub("", content)
    if n_script:
        issues.append(f"剥离 {n_script} 个 <script> 块")

    n_fo = len(_FOREIGN_OBJECT_RE.findall(content))
    content = _FOREIGN_OBJECT_RE.sub("", content)
    if n_fo:
        issues.append(f"剥离 {n_fo} 个 <foreignObject> (SVG 嵌入风险)")

    content = _EVENT_ATTR_RE.sub("", content)
    content = _JAVASCRIPT_URL_RE.sub(r"\1=\"#\"", content)
    content = _DATA_HTML_RE.sub(r"\1=\"\"", content)
    content = _STYLE_ON_RE.sub(r"\1", content)

    # 标签白名单: 未知标签剥离标签本身 (保留内容)
    tag_re = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*>")

    def _filter(m: re.Match) -> str:
        name = m.group(2)
        if name.lower() in _ALLOWED_TAGS:
            return m.group(0)
        issues.append(f"剥离非法标签 <{name}>")
        return ""

    content = tag_re.sub(_filter, content)

    if len(content) < original_len and not issues:
        issues.append("内容经消毒处理")
    return content, issues


def _validate_echarts(content: str) -> tuple[bool, list[str], dict[str, Any]]:
    """echarts_json 校验: JSON 可解析 + 具备 option 结构。"""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return False, [f"ECharts JSON 解析失败: {e}"], {}
    if not isinstance(data, dict):
        return False, ["ECharts option 必须是对象"], {}
    if "series" not in data and "xAxis" not in data and "title" not in data:
        return False, ["缺少 series/xAxis/title (无效 ECharts option)"], {}
    return True, [], data  # noqa: RET504


def artifact_preview(
    artifact: dict[str, Any],
    *,
    sandbox: bool = False,
    snapshot_dir: str = "",
) -> dict[str, Any]:
    """Artifact 预览: 消毒 + 校验 + 快照。

    Args:
        artifact: {"type": markdown|html|svg|echarts_json|code, "content": str}
        sandbox: True 时快照写入在隔离目录校验 (v1: 内容消毒已防执行, 保留参数)
        snapshot_dir: 快照目录 (缺省 ~/.veya/artifacts/)
    """
    atype = str(artifact.get("type", "")).lower()
    content = str(artifact.get("content", ""))
    if atype not in ARTIFACT_TYPES:
        return {"renderable": False, "sanitized_content": content,
                "snapshot_id": "", "issues": [f"未知类型: {atype}"], "type": atype}

    issues: list[str] = []
    sanitized = content

    if atype in ("html", "svg", "markdown"):
        sanitized, markup_issues = sanitize_markup(content, atype)
        issues.extend(markup_issues)
        renderable = len(sanitized.strip()) > 0
    elif atype == "echarts_json":
        renderable, json_issues, data = _validate_echarts(content)
        issues.extend(json_issues)
        sanitized = json.dumps(data, ensure_ascii=False) if renderable else content
    else:  # code
        renderable = len(content.strip()) > 0

    # 快照
    snapshot_id = f"art_{uuid.uuid4().hex[:12]}"
    snap_dir = Path(snapshot_dir or SNAPSHOT_DIR)
    snap_dir.mkdir(parents=True, exist_ok=True)
    ext = _SNAPSHOT_EXT[atype]
    snap_path = snap_dir / f"{snapshot_id}.{ext}"
    snap_path.write_text(sanitized, encoding="utf-8")

    return {
        "renderable": renderable,
        "sanitized_content": sanitized,
        "snapshot_id": snapshot_id,
        "snapshot_path": str(snap_path),
        "issues": issues,
        "type": atype,
        "sandbox": sandbox,
        "created_at": time.time(),
    }


__all__ = ["artifact_preview", "sanitize_markup", "ARTIFACT_TYPES"]
