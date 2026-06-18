"""Tests for M-1: process_media_substrate."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stubs so we don't need oprim/__init__.py to fully import
# ---------------------------------------------------------------------------

@dataclass
class _MediaResult:
    has_subtitle: bool
    subtitle_text: str | None
    audio_path: Path | None
    title: str
    duration: float
    metadata: dict


@dataclass
class _TranscriptResult:
    text: str
    segments: list
    language: str
    duration: float


@dataclass
class _IngestResult:
    substrate_id: str
    medium: str = "video"
    duplicate_of: str | None = None
    elapsed_seconds: float = 0.0
    cost_usd: float = 0.0


_SUBTITLE_MEDIA = _MediaResult(
    has_subtitle=True,
    subtitle_text="Python is great. Lists and dicts are useful data structures.",
    audio_path=None,
    title="Python Tutorial",
    duration=600.0,
    metadata={"uploader": "Chan", "upload_date": "20240301", "description": ""},
)

_AUDIO_MEDIA = _MediaResult(
    has_subtitle=False,
    subtitle_text=None,
    audio_path=Path("/tmp/audio.mp3"),
    title="Audio Only Video",
    duration=300.0,
    metadata={},
)

_TRANSCRIPT = _TranscriptResult(
    text="Transcribed text from audio.",
    segments=[{"start": 0.0, "end": 3.0, "text": "Transcribed text from audio."}],
    language="zh",
    duration=3.0,
)


def _make_config(transcribe=True, asr_backend="local"):
    from omodul.process_media_substrate import MediaConfig
    return MediaConfig(
        video_url="https://yt.be/abc123",
        user_id_hash="u001hash",
        transcribe_if_no_subtitle=transcribe,
        asr_backend=asr_backend,
    )


def _make_input():
    from omodul.process_media_substrate import MediaInput
    return MediaInput()


def _make_llm(md="# Title\n\n## Topic\n- Key point [00:00](https://yt.be/abc123?t=0)\n"):
    async def caller(*, messages, max_tokens=4096, **kw):
        return {"content": [{"type": "text", "text": md}], "usage": {}}
    return caller


def _patch_all(media=_SUBTITLE_MEDIA, transcript=_TRANSCRIPT, ingest_id="sid001"):
    return [
        patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=media)),
        patch("omodul.process_media_substrate.transcribe_audio", AsyncMock(return_value=transcript)),
        patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(
            return_value="# Title\n\n## Section\n- Point\n"
        )),
        patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(
            return_value=_IngestResult(substrate_id=ingest_id)
        )),
        patch("omodul.process_media_substrate.ProviderRegistry") ,
    ]


class TestProcessMediaSubstrate:
    async def test_subtitle_flow_returns_completed(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        with patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=_SUBTITLE_MEDIA)), \
             patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(return_value="# T\n")), \
             patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(return_value=_IngestResult(substrate_id="s1"))), \
             patch("omodul.process_media_substrate.ProviderRegistry") as mock_reg:
            mock_reg.get.return_value.llm.return_value = _make_llm()
            result = await process_media_substrate(_make_config(), _make_input(), tmp_path)

        assert result["status"] == "completed"
        assert result["has_subtitle"] is True
        assert result["transcribed"] is False

    async def test_no_subtitle_transcription_flow(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        with patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=_AUDIO_MEDIA)), \
             patch("omodul.process_media_substrate.transcribe_audio", AsyncMock(return_value=_TRANSCRIPT)), \
             patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(return_value="# T\n")), \
             patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(return_value=_IngestResult(substrate_id="s2"))), \
             patch("omodul.process_media_substrate.ProviderRegistry") as mock_reg:
            mock_reg.get.return_value.llm.return_value = _make_llm()
            result = await process_media_substrate(_make_config(transcribe=True), _make_input(), tmp_path)

        assert result["status"] == "completed"
        assert result["transcribed"] is True

    async def test_no_subtitle_skip_transcription(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        transcribe_mock = AsyncMock(return_value=_TRANSCRIPT)
        with patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=_AUDIO_MEDIA)), \
             patch("omodul.process_media_substrate.transcribe_audio", transcribe_mock), \
             patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(return_value="# T\n")), \
             patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(return_value=_IngestResult(substrate_id="s3"))), \
             patch("omodul.process_media_substrate.ProviderRegistry") as mock_reg:
            mock_reg.get.return_value.llm.return_value = _make_llm()
            result = await process_media_substrate(_make_config(transcribe=False), _make_input(), tmp_path)

        transcribe_mock.assert_not_called()
        assert result["status"] == "completed"
        assert result["transcribed"] is False

    async def test_fingerprint_in_result(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        with patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=_SUBTITLE_MEDIA)), \
             patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(return_value="# T\n")), \
             patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(return_value=_IngestResult(substrate_id="s4"))), \
             patch("omodul.process_media_substrate.ProviderRegistry") as mock_reg:
            mock_reg.get.return_value.llm.return_value = _make_llm()
            result = await process_media_substrate(_make_config(), _make_input(), tmp_path)

        assert "fingerprint" in result
        assert len(result["fingerprint"]) == 24

    async def test_fingerprint_deterministic(self):
        from omodul.process_media_substrate import compute_fingerprint_for_process_media_substrate
        f1 = compute_fingerprint_for_process_media_substrate("https://yt.be/x", "user1")
        f2 = compute_fingerprint_for_process_media_substrate("https://yt.be/x", "user1")
        assert f1 == f2 and len(f1) == 24

    async def test_decision_trail_written(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        with patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=_SUBTITLE_MEDIA)), \
             patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(return_value="# T\n")), \
             patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(return_value=_IngestResult(substrate_id="s5"))), \
             patch("omodul.process_media_substrate.ProviderRegistry") as mock_reg:
            mock_reg.get.return_value.llm.return_value = _make_llm()
            await process_media_substrate(_make_config(), _make_input(), tmp_path)

        trail_files = list(tmp_path.glob("decision_trail_*.json"))
        assert len(trail_files) >= 1

    async def test_cost_usd_in_result(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        with patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=_SUBTITLE_MEDIA)), \
             patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(return_value="# T\n")), \
             patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(return_value=_IngestResult(substrate_id="s6"))), \
             patch("omodul.process_media_substrate.ProviderRegistry") as mock_reg:
            mock_reg.get.return_value.llm.return_value = _make_llm()
            result = await process_media_substrate(_make_config(), _make_input(), tmp_path)

        assert "cost_usd" in result
        assert isinstance(result["cost_usd"], float)

    async def test_on_step_callback_called(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        steps = []
        def on_step(*, step, state):
            steps.append((step, state))

        with patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=_SUBTITLE_MEDIA)), \
             patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(return_value="# T\n")), \
             patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(return_value=_IngestResult(substrate_id="s7"))), \
             patch("omodul.process_media_substrate.ProviderRegistry") as mock_reg:
            mock_reg.get.return_value.llm.return_value = _make_llm()
            await process_media_substrate(_make_config(), _make_input(), tmp_path, on_step=on_step)

        step_names = {s for s, _ in steps}
        assert "extract" in step_names

    async def test_cancelled_error_propagates(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        async def cancel_extract(**kw):
            raise asyncio.CancelledError()

        with patch("omodul.process_media_substrate.media_extract", cancel_extract), \
             patch("omodul.process_media_substrate.ProviderRegistry"):
            with pytest.raises(asyncio.CancelledError):
                await process_media_substrate(_make_config(), _make_input(), tmp_path)

    async def test_extract_failure_returns_failed_status(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        async def broken_extract(**kw):
            raise RuntimeError("network error")

        with patch("omodul.process_media_substrate.media_extract", broken_extract), \
             patch("omodul.process_media_substrate.ProviderRegistry"):
            result = await process_media_substrate(_make_config(), _make_input(), tmp_path)

        assert result["status"] == "failed"
        assert "network error" in result["error"]["message"]

    async def test_report_written_when_transcript_available(self, tmp_path):
        from omodul.process_media_substrate import process_media_substrate

        with patch("omodul.process_media_substrate.media_extract", AsyncMock(return_value=_SUBTITLE_MEDIA)), \
             patch("omodul.process_media_substrate.media_to_structured_md", AsyncMock(return_value="# T\n## S\n- p\n")), \
             patch("omodul.process_media_substrate.ingest_substrate", AsyncMock(return_value=_IngestResult(substrate_id="s8"))), \
             patch("omodul.process_media_substrate.ProviderRegistry") as mock_reg:
            mock_reg.get.return_value.llm.return_value = _make_llm()
            result = await process_media_substrate(_make_config(), _make_input(), tmp_path)

        assert "report_path" in result
        assert result["report_path"] is not None
        report = Path(result["report_path"])
        assert report.exists()
        assert "Python Tutorial" in report.read_text()
