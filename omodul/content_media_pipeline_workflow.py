"""omodul.content_media_pipeline_workflow — 内容媒体流水线事务.

集成：oprim.media_content_parse（素材解析）+ oprim.media_publish_post
（发布，publisher 注入）+ 报告产出。

Pillars: fingerprint, decision_trail, report, cost
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig,
    CostTracker,
    Trail,
    build_result,
    compute_fingerprint,
    write_report,
)


class ContentMediaPipelineConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "content_media_pipeline_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"content"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}

    channel: str = "x"
    report_format: str = "markdown"


class ContentMediaPipelineInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str
    media_paths: list[str] = []
    channel: str | None = None
    publisher: Any = None        # oprim.media_publish_post 的 publisher Protocol
    parser: Any = None           # 可选深度解析器


def compute_fingerprint_for(
    config: ContentMediaPipelineConfig, input_data: ContentMediaPipelineInput
) -> str:
    """Fingerprint over content + media_paths。"""
    return compute_fingerprint(
        {"content": input_data.content, "media": list(input_data.media_paths)}
    )


async def content_media_pipeline_workflow(
    config: ContentMediaPipelineConfig,
    input_data: ContentMediaPipelineInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """内容 → 素材解析 → 发布 → 报告。失败绝不 raise。"""
    trail = Trail()
    cost = CostTracker()
    fp = compute_fingerprint_for(config, input_data)
    t0 = time.time()

    try:
        if not input_data.content.strip():
            raise ValueError("content must not be empty")
        if input_data.publisher is None:
            raise ValueError("publisher must be injected")

        from oprim import media_content_parse, media_publish_post

        # 1) 素材解析
        parsed_media: list[dict[str, Any]] = []
        for mp in input_data.media_paths:
            if on_step:
                on_step({"stage": "parse", "path": mp})
            parsed = await media_content_parse(mp, parser=input_data.parser)
            parsed_media.append(parsed)
            trail.record(event="media_parsed", path=mp, kind=parsed["kind"])

        # 2) 发布
        channel = input_data.channel or config.channel
        if on_step:
            on_step({"stage": "publish", "channel": channel})
        published = await media_publish_post(
            channel,
            content=input_data.content,
            media_paths=input_data.media_paths,
            publisher=input_data.publisher,
        )
        trail.record(event="published", channel=channel, post_id=published["post_id"])

        # 3) 报告
        report = _render_report(input_data, channel, parsed_media, published, time.time() - t0)
        report_path = write_report(
            report, output_dir=Path(output_dir), name="content_media_pipeline",
            fmt=config.report_format,
        )
        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            report_path=report_path,
            cost_usd=round(cost.total_usd, 6),
            findings={
                "channel": channel,
                "post_id": published["post_id"],
                "media": parsed_media,
                "media_count": len(parsed_media),
            },
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
        )


def _render_report(
    input_data, channel: str, parsed_media: list, published: dict, elapsed: float
) -> str:
    media_lines = "\n".join(
        f"- {m['path']}: {m['kind']} ({m['size_bytes']}B)" for m in parsed_media
    ) or "- (无附件)"
    return (
        f"# 内容媒体流水线报告\n\n"
        f"- 渠道: {channel}\n- 文案: {input_data.content[:200]}\n"
        f"- 媒体数: {len(parsed_media)}\n- post_id: {published.get('post_id', '')}\n"
        f"- 耗时: {elapsed:.1f}s\n\n## 素材\n{media_lines}\n"
    )
