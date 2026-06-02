"""Tests for IllustrationAgent (≥11 scenarios)."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import omodul.knowledge.agents.builtin.illustration_agent as _mod
from omodul.knowledge.agents.base import AgentContext
from omodul.knowledge.agents.builtin.illustration_agent import IllustrationAgent
from omodul.knowledge.agents.registry import get_registry


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _ctx() -> AgentContext:
    return AgentContext(
        user_id="u1",
        agent_run_id="R001",
        invoked_at=datetime.datetime.utcnow(),
    )


def _fake_llm(text: str = "A serene mountain landscape at dawn.") -> MagicMock:
    from oprim.llm import LLMResponse

    resp = LLMResponse(text=text, model="test", input_tokens=10, output_tokens=8, cost_usd=0.001)
    return MagicMock(return_value=resp)


def _make_image_mock(tmp_path: Path) -> AsyncMock:
    """image_generate side_effect that writes a real file to output_path."""

    async def _gen(**kw):  # type: ignore[no-untyped-def]
        out: Path = kw["output_path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x89PNG\r\n\x1a\n")
        return out

    return AsyncMock(side_effect=_gen)


def _params(**kw) -> dict:  # type: ignore[no-untyped-def]
    return {"substrate_id": "SUB001", **kw}


# ---------------------------------------------------------------------------
# test_1: normal generation — 1 image, success
# ---------------------------------------------------------------------------


class TestIllustrationAgentNormal:
    @pytest.mark.asyncio
    async def test_generates_one_image(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value="A deep-sea documentary."),
            patch.object(_mod, "llm_call", _fake_llm()),
            patch.object(_mod, "image_generate", _make_image_mock(tmp_path)),
            patch.object(_mod, "_save_illustration_derivative", return_value="DRV001"),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(output_dir=str(tmp_path)), ctx)

        assert result.success
        assert result.output["images_generated"] == 1
        assert len(result.output["image_paths"]) == 1
        assert result.output["substrate_id"] == "SUB001"
        assert result.output["derivative_ids"] == ["DRV001"]

    # test_2: image_count=3 → 3 images
    @pytest.mark.asyncio
    async def test_generates_three_images(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value="Summary text."),
            patch.object(_mod, "llm_call", _fake_llm()),
            patch.object(_mod, "image_generate", _make_image_mock(tmp_path)),
            patch.object(_mod, "_save_illustration_derivative", side_effect=["D1", "D2", "D3"]),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(image_count=3, output_dir=str(tmp_path)), ctx)

        assert result.success
        assert result.output["images_generated"] == 3
        assert len(result.output["image_paths"]) == 3
        assert result.output["derivative_ids"] == ["D1", "D2", "D3"]

    # test_3: image_generate API failure → success=False, error in output
    @pytest.mark.asyncio
    async def test_image_generate_api_failure(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()

        async def _fail(**kw):  # type: ignore[no-untyped-def]
            raise RuntimeError("DashScope 500: internal server error")

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value="Summary."),
            patch.object(_mod, "llm_call", _fake_llm()),
            patch.object(_mod, "image_generate", AsyncMock(side_effect=_fail)),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(output_dir=str(tmp_path)), ctx)

        assert not result.success
        assert "500" in result.error or "internal server error" in result.error
        assert "image_paths" in result.output  # partial list (empty here)

    # test_4: provider not registered → success=False, specific error message
    @pytest.mark.asyncio
    async def test_provider_not_registered(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()

        with patch(
            "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
        ) as mock_reg:
            mock_reg.has.return_value = False
            result = await agent.run(_params(output_dir=str(tmp_path)), ctx)

        assert not result.success
        assert "wanxiang provider not registered" in result.error
        assert "DASHSCOPE_API_KEY" in result.error

    # test_5: style="realistic" → prompt template contains "realistic"
    @pytest.mark.asyncio
    async def test_style_in_prompt(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()
        captured_prompts: list[str] = []

        from oprim.llm import LLMResponse

        def _capture_llm(prompt: str, **kw):  # type: ignore[no-untyped-def]
            captured_prompts.append(prompt)
            return LLMResponse(
                text="Realistic forest scene.",
                model="t",
                input_tokens=5,
                output_tokens=5,
                cost_usd=0.0,
            )

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value="Nature documentary."),
            patch.object(_mod, "llm_call", side_effect=_capture_llm),
            patch.object(_mod, "image_generate", _make_image_mock(tmp_path)),
            patch.object(_mod, "_save_illustration_derivative", return_value="D1"),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(style="realistic", output_dir=str(tmp_path)), ctx)

        assert result.success
        # The image-prompt call contains "realistic" in its prompt
        prompt_calls = [p for p in captured_prompts if "realistic" in p.lower()]
        assert prompt_calls, "Expected at least one llm_call prompt containing 'realistic'"

    # test_6: aspect_ratio="16:9" → image_generate receives width=1024, height=576
    @pytest.mark.asyncio
    async def test_aspect_ratio_16_9(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()
        captured: dict = {}

        async def _cap(**kw):  # type: ignore[no-untyped-def]
            captured.update(kw)
            out: Path = kw["output_path"]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x89PNG")
            return out

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value="Summary."),
            patch.object(_mod, "llm_call", _fake_llm()),
            patch.object(_mod, "image_generate", AsyncMock(side_effect=_cap)),
            patch.object(_mod, "_save_illustration_derivative", return_value="D1"),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(aspect_ratio="16:9", output_dir=str(tmp_path)), ctx)

        assert result.success
        assert captured.get("width") == 1024
        assert captured.get("height") == 576

    # test_7: substrate has no summary → llm_call used to generate summary
    @pytest.mark.asyncio
    async def test_empty_substrate_summary_triggers_llm_summarize(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()
        llm_call_count = 0

        from oprim.llm import LLMResponse

        def _counting_llm(prompt: str, **kw):  # type: ignore[no-untyped-def]
            nonlocal llm_call_count
            llm_call_count += 1
            return LLMResponse(
                text="Generated summary." if llm_call_count == 1 else "Image prompt.",
                model="t",
                input_tokens=5,
                output_tokens=5,
                cost_usd=0.0,
            )

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value=""),  # no cached summary
            patch.object(_mod, "llm_call", side_effect=_counting_llm),
            patch.object(_mod, "image_generate", _make_image_mock(tmp_path)),
            patch.object(_mod, "_save_illustration_derivative", return_value="D1"),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(output_dir=str(tmp_path)), ctx)

        assert result.success
        # First llm_call = summary generation, second = image prompt → at least 2 calls
        assert llm_call_count >= 2

    # test_8: LLM prompt generation fails → success=False
    @pytest.mark.asyncio
    async def test_llm_prompt_generation_failure(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()

        from oprim.llm import LLMResponse

        call_count = 0

        def _fail_second(prompt: str, **kw):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call = summary, succeeds
                return LLMResponse(
                    text="Summary ok.", model="t", input_tokens=5, output_tokens=5, cost_usd=0.0
                )
            raise RuntimeError("LLM provider unreachable")

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value=""),
            patch.object(_mod, "llm_call", side_effect=_fail_second),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(output_dir=str(tmp_path)), ctx)

        assert not result.success
        assert "LLM prompt generation failed" in result.error

    # test_9: derivative write fails → success=False, image_paths still returned
    @pytest.mark.asyncio
    async def test_derivative_write_failure_returns_image_paths(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value="Summary."),
            patch.object(_mod, "llm_call", _fake_llm()),
            patch.object(_mod, "image_generate", _make_image_mock(tmp_path)),
            patch.object(
                _mod, "_save_illustration_derivative", side_effect=RuntimeError("DB locked")
            ),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(output_dir=str(tmp_path)), ctx)

        assert not result.success
        assert "Derivative write failed" in result.error
        # image_paths must be present (images were already generated)
        assert "image_paths" in result.output
        assert len(result.output["image_paths"]) == 1

    # test_10: full round-trip — substrate→summary→prompt→image→derivative
    @pytest.mark.asyncio
    async def test_full_round_trip(self, tmp_path: Path) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()
        trace_steps: list[str] = []

        from oprim.llm import LLMResponse

        call_num = 0

        def _llm(prompt: str, **kw):  # type: ignore[no-untyped-def]
            nonlocal call_num
            call_num += 1
            return LLMResponse(
                text="Ocean waves crashing at sunset.",
                model="qwen-max",
                input_tokens=20,
                output_tokens=15,
                cost_usd=0.002,
            )

        async def _gen(**kw):  # type: ignore[no-untyped-def]
            trace_steps.append("image_generate")
            out: Path = kw["output_path"]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x89PNG\r\n\x1a\n")
            return out

        def _save(substrate_id: str, image_path: str, style: str, provider: str) -> str:
            trace_steps.append("save_derivative")
            return "DRV_ROUNDTRIP"

        with (
            patch.object(_mod, "_fetch_substrate_summary", return_value="Ocean documentary."),
            patch.object(_mod, "llm_call", side_effect=_llm),
            patch.object(_mod, "image_generate", AsyncMock(side_effect=_gen)),
            patch.object(_mod, "_save_illustration_derivative", side_effect=_save),
            patch(
                "omodul.knowledge.agents.builtin.illustration_agent.ProviderRegistry"
            ) as mock_reg,
        ):
            mock_reg.has.return_value = True
            result = await agent.run(_params(image_count=2, output_dir=str(tmp_path)), ctx)

        assert result.success
        assert result.output["images_generated"] == 2
        assert len(result.output["derivative_ids"]) == 2
        assert trace_steps == [
            "image_generate",
            "image_generate",
            "save_derivative",
            "save_derivative",
        ]
        assert len(result.trace) >= 5  # summary + 2×prompt + 2×image

    # test_11: agent registered in registry under "illustration_agent"
    def test_agent_in_registry(self) -> None:
        registry = get_registry()
        assert "illustration_agent" in registry
        agent_cls = registry.get("illustration_agent")
        assert agent_cls is IllustrationAgent

    # test_12: missing substrate_id param → success=False
    @pytest.mark.asyncio
    async def test_missing_substrate_id(self) -> None:
        agent = IllustrationAgent()
        ctx = _ctx()
        result = await agent.run({}, ctx)
        assert not result.success
        assert "substrate_id" in result.error

    # test_13: top-level omodul import works
    def test_top_level_import(self) -> None:
        from omodul import IllustrationAgent as IA  # noqa: PLC0415

        assert IA is IllustrationAgent
