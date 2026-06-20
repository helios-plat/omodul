"""
omodul.process_media_substrate — Video/audio ingestion and structuring workflow.

Pillars: fingerprint, decision_trail, report, cost
Fingerprint fields: video_url, user_id_hash

Flow:
  1. media_extract (oprim P-2) — subtitle text or audio download
  2. If no subtitle and transcribe_if_no_subtitle=True: transcribe_audio (oprim P-3)
  3. media_to_structured_md (oskill K-2) — LLM structuring
  4. ingest_substrate (oskill) — index into local knowledge base

LLM is obtained from obase.ProviderRegistry.get().llm(config.llm_provider).
cost: local ASR = 0; dashscope ASR and LLM structuring have cost tracked via CostTracker.
"""
from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from obase.provider_registry import ProviderRegistry
from oprim._media_extract import media_extract
from oprim._transcribe_audio import transcribe_audio
from oskill._media_to_structured_md import media_to_structured_md
from oskill.ingest_substrate import ingest_substrate

from omodul._base import (
    BaseConfig, CostTracker, Trail, build_result, compute_fingerprint,
    write_report,
)


class MediaConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "process_media_substrate"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set] = {"fingerprint", "decision_trail", "report", "cost"}
    _fingerprint_fields: ClassVar[set] = {"video_url", "user_id_hash"}

    video_url: str
    user_id_hash: str
    proxy: str | None = None
    asr_backend: str = "local"
    transcribe_if_no_subtitle: bool = True
    cookies_path: str | None = None


class MediaInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)


def compute_fingerprint_for_process_media_substrate(video_url: str, user_id_hash: str) -> str:
    return compute_fingerprint({"video_url": video_url, "user_id_hash": user_id_hash})


async def process_media_substrate(
    config: MediaConfig,
    input_data: MediaInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Ingest a video URL: extract content, transcribe if needed, structure, and index.

    Returns a standard build_result dict with fingerprint, decision_trail, report_path,
    cost_usd, and MediaFindings fields.
    """
    trail = Trail()
    cost = CostTracker()
    fingerprint = compute_fingerprint_for_process_media_substrate(
        config.video_url, config.user_id_hash
    )

    try:
        llm = ProviderRegistry.get().llm(config.llm_provider)

        trail.record(event="start", video_url=config.video_url, fingerprint=fingerprint)
        _notify(on_step, "extract", "started")

        # Step 1: extract subtitle or audio
        work_dir = output_dir / "media_work"
        media = await media_extract(
            video_url=config.video_url,
            proxy=config.proxy,
            work_dir=work_dir,
            cookies_path=config.cookies_path,
        )
        trail.record(
            event="extract_done",
            has_subtitle=media.has_subtitle,
            title=media.title,
            duration=media.duration,
        )
        _notify(on_step, "extract", "done")

        # Step 2: transcribe if no subtitle and configured to do so
        transcribed = False
        transcript_text: str | None = None

        if media.has_subtitle:
            transcript_text = media.subtitle_text
        elif config.transcribe_if_no_subtitle and media.audio_path is not None:
            _notify(on_step, "transcribe", "started")
            tr = await transcribe_audio(
                audio_path=media.audio_path,
                backend=config.asr_backend,
                language="zh",
            )
            transcript_text = tr.text
            transcribed = True
            cost.add_from_response(
                {"usage": {"input_tokens": 0, "output_tokens": 0}},
                model=config.llm_model,
            )
            trail.record(
                event="transcribe_done",
                backend=config.asr_backend,
                text_len=len(tr.text),
            )
            _notify(on_step, "transcribe", "done")

        # Step 3: structure with LLM (if transcript available)
        md_path: Path | None = None
        substrate_id: str | None = None

        if transcript_text:
            _notify(on_step, "structure", "started")
            md_content = await media_to_structured_md(
                transcript=transcript_text,
                title=media.title,
                source_url=config.video_url,
                llm=llm,
            )
            trail.record(event="structure_done", md_len=len(md_content))
            _notify(on_step, "structure", "done")

            # Write markdown to output_dir
            md_path = output_dir / f"media_{fingerprint[:8]}.md"
            md_path.write_text(md_content, encoding="utf-8")

            # Step 4: ingest into substrate
            _notify(on_step, "ingest", "started")
            ingest_result = await ingest_substrate(
                path=md_path,
                source={"type": "web_video", "source_path": config.video_url, "url": config.video_url},
                user_id_hash=config.user_id_hash,
            )
            substrate_id = ingest_result.substrate_id
            trail.record(event="ingest_done", substrate_id=substrate_id)
            _notify(on_step, "ingest", "done")

            # Write report
            report_content = (
                f"# Media Substrate Report\n\n"
                f"**Video**: {config.video_url}\n"
                f"**Title**: {media.title}\n"
                f"**Duration**: {media.duration:.0f}s\n"
                f"**Subtitle**: {'yes' if media.has_subtitle else 'no'}\n"
                f"**Transcribed**: {'yes' if transcribed else 'no'}\n"
                f"**Substrate ID**: {substrate_id or 'n/a'}\n\n"
                f"## Structured Notes\n\n{md_content}"
            )
            report_path = write_report(
                report_content,
                output_dir=output_dir,
                name=f"media_report_{fingerprint[:8]}",
                fmt="markdown",
            )
        else:
            report_path = None

        trail_path = trail.write(output_dir)

        findings = {
            "substrate_id": substrate_id or "",
            "title": media.title,
            "has_subtitle": media.has_subtitle,
            "transcribed": transcribed,
            "md_path": str(md_path) if md_path else None,
        }

        return build_result(
            status="completed",
            error=None,
            fingerprint=fingerprint,
            trail=trail,
            trail_path=trail_path,
            report_path=str(report_path) if report_path else None,
            cost_usd=cost.total_usd,
            **findings,
        )

    except asyncio.CancelledError:
        trail.record(event="cancelled")
        trail.write(output_dir)
        raise

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )


def _notify(on_step: Any, step: str, state: str) -> None:
    if on_step is not None:
        try:
            on_step(step=step, state=state)
        except Exception:
            pass
