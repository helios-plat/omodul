"""omodul.realtime_voice_agent_workflow — 实时语音 Agent 事务.

集成：VAD 分段（obase.vad_pipeline）+ STT（oprim.stt_transcribe_stream）
+ LLM 回复（cost 记账）+ TTS（oprim.tts_synthesize_stream），
全链路经注入处理器装配。

Pillars: decision_trail, cost
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig,
    CostTracker,
    Trail,
    build_result,
    extract_text,
    llm_call,
)


class RealtimeVoiceAgentConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "realtime_voice_agent_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}

    sample_rate: int = 16000
    min_segment_frames: int = 5
    system_prompt: str = "你是实时语音助手，回答简洁自然。"
    max_reply_tokens: int = 512


class RealtimeVoiceAgentInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    frames: list[bytes] = []    # 16bit PCM 帧
    vad: Any = None             # obase.vad_pipeline.VADPipeline
    transcriber: Any = None     # STT Protocol（oprim.stt_transcribe_stream）
    caller: Any = None          # LLM 注入
    synthesizer: Any = None     # TTS Protocol（oprim.tts_synthesize_stream）
    session_id: str = "session-1"


async def realtime_voice_agent_workflow(
    config: RealtimeVoiceAgentConfig,
    input_data: RealtimeVoiceAgentInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """实时语音回合：VAD → STT → LLM → TTS。失败绝不 raise。"""
    trail = Trail()
    cost = CostTracker()

    try:
        if not input_data.frames:
            raise ValueError("frames must not be empty")
        if input_data.transcriber is None or input_data.caller is None:
            raise ValueError("transcriber and caller must be injected")
        if input_data.synthesizer is None:
            raise ValueError("synthesizer must be injected")

        from oprim import stt_transcribe_stream, tts_synthesize_stream

        # 1) VAD 分段
        segments: list[dict[str, Any]] = []
        if input_data.vad is not None:
            segments = input_data.vad.detect_speech_segments(
                input_data.frames,
                sample_rate=config.sample_rate,
                min_segment_frames=config.min_segment_frames,
            )
            if not segments:
                return build_result(
                    status="completed",
                    error=None,
                    trail=trail,
                    trail_path=trail.write(Path(output_dir)) if output_dir else None,
                    cost_usd=round(cost.total_usd, 6),
                    heard=False,
                    segments=segments,
                )
            trail.record(event="vad_segments", count=len(segments))

        # 2) STT
        if on_step:
            on_step({"stage": "stt", "status": "running"})
        stt = await stt_transcribe_stream(
            input_data.frames,
            transcriber=input_data.transcriber,
            sample_rate=config.sample_rate,
        )
        user_text = stt["text"]
        trail.record(event="transcribed", chars=len(user_text))

        # 3) LLM 回复
        if on_step:
            on_step({"stage": "llm", "status": "running"})
        response = await llm_call(
            [
                {"role": "system", "content": config.system_prompt},
                {"role": "user", "content": user_text},
            ],
            caller=input_data.caller,
            cost=cost,
            trail=trail,
            model=config.llm_model,
            max_tokens=config.max_reply_tokens,
        )
        reply = extract_text(response)
        trail.record(event="replied", chars=len(reply))

        # 4) TTS 流
        if on_step:
            on_step({"stage": "tts", "status": "running"})
        tts = await tts_synthesize_stream(reply, synthesizer=input_data.synthesizer)

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
            heard=True,
            session_id=input_data.session_id,
            transcript=user_text,
            reply=reply,
            tts=tts,
            segments=segments,
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
        )
