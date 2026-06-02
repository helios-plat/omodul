"""AudioGeneratorAgent — batch TTS narration for pinned substrates (edge-tts v1.1+)."""

from __future__ import annotations

import time

from oskill.knowledge.generate_audio_narration import (
    AudioNarrationResult,
    generate_audio_narration,
)

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep
from omodul.knowledge.agents.registry import register_agent


@register_agent
class AudioGeneratorAgent(Agent):
    """Generate audio narration for a substrate via edge-tts.

    Requires:
      - obase.providers.register_default_providers() called at app startup
      - DASHSCOPE_API_KEY (for wanxiang) or just edge-tts (for TTS)
    Suited for on-demand or nightly scheduled runs (cron 0 3 * * *).
    """

    name = "audio_generator"
    description = "Generate audio narration for a substrate using edge-tts."
    allowed_tools = [
        "oskill.knowledge.generate_audio_narration",
    ]
    timeout_seconds = 300

    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        substrate_id = params.get("substrate_id", "").strip()
        if not substrate_id:
            return AgentResult(
                success=False,
                output={"error": "'substrate_id' param is required"},
                trace=[],
                citations=[],
                error="'substrate_id' param is required",
            )

        voice = params.get("voice", "default")
        speed = float(params.get("speed", 1.0))

        trace: list[AgentStep] = []
        t0 = time.monotonic()
        try:
            result: AudioNarrationResult = await generate_audio_narration(
                substrate_id=substrate_id,
                voice=voice,
                speed=speed,
            )
            trace.append(
                AgentStep(
                    step_num=1,
                    tool_name="generate_audio_narration",
                    tool_input={"substrate_id": substrate_id, "voice": voice, "speed": speed},
                    tool_output={
                        "audio_path": result.audio_path,
                        "duration_seconds": result.duration_seconds,
                        "asset_id": result.audio_asset_id,
                    },
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            )
            return AgentResult(
                success=True,
                output={
                    "audio_path": result.audio_path,
                    "audio_asset_id": result.audio_asset_id,
                    "duration_seconds": result.duration_seconds,
                    "substrate_id": substrate_id,
                },
                trace=trace,
                citations=[],
                cost_usd=result.cost_usd,
            )
        except Exception as exc:
            trace.append(
                AgentStep(
                    step_num=1,
                    tool_name="generate_audio_narration",
                    tool_input={"substrate_id": substrate_id},
                    error=str(exc),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            )
            return AgentResult(
                success=False,
                output={"error": str(exc)},
                trace=trace,
                citations=[],
                error=str(exc),
            )
