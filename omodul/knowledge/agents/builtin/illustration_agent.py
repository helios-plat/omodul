"""IllustrationAgent — generate 1-3 illustration derivatives for a substrate."""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from pathlib import Path

from obase import ProviderRegistry
from oprim import image_generate
from oprim.llm import LLMResponse, llm_call

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep, Citation
from omodul.knowledge.agents.registry import register_agent

_ASPECT_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1024, 576),
    "4:3": (1024, 768),
}

_PROMPT_TEMPLATE = (
    "Generate a detailed {style} image prompt based on the following content summary.\n"
    "The prompt should describe a visually compelling scene that captures the main themes.\n\n"
    "Style: {style}\n"
    "Summary:\n{summary}\n\n"
    "Return only the image prompt (1-3 sentences, English):"
)

_SUMMARY_PROMPT_TEMPLATE = (
    "Summarize the following substrate content in 2-3 sentences for image generation purposes.\n"
    "Substrate ID: {substrate_id}\n\n"
    "Provide a concise, visual summary:"
)


@register_agent
class IllustrationAgent(Agent):
    """Generate 1-3 illustration derivatives for a substrate via oprim.image_generate.

    Requires:
      - obase.providers.register_default_providers() called at app startup
      - DASHSCOPE_API_KEY configured (for default wanxiang provider)

    Steps:
      1. Fetch or generate substrate summary (meta_db or llm_call fallback)
      2. Generate image prompt(s) via llm_call
      3. Generate image(s) via oprim.image_generate
      4. Write illustration derivative record(s) to meta_db
    """

    name = "illustration_agent"
    description = "基于 substrate 主题自动生成 1-3 张配图 derivative"
    allowed_tools = [
        "oprim.image_generate",
        "oprim.llm.llm_call",
    ]
    timeout_seconds = 600

    async def run(self, params: dict, context: AgentContext) -> AgentResult:  # noqa: C901
        substrate_id = params.get("substrate_id", "").strip()
        if not substrate_id:
            return AgentResult(
                success=False,
                output={"error": "'substrate_id' param is required"},
                trace=[],
                citations=[],
                error="'substrate_id' param is required",
            )

        image_count = min(max(int(params.get("image_count", 1)), 1), 3)
        style = params.get("style", "illustration")
        aspect_ratio = params.get("aspect_ratio", "1:1")
        provider = params.get("provider", "wanxiang")
        output_dir = Path(params["output_dir"]) if params.get("output_dir") else None

        trace: list[AgentStep] = []

        # Guard: provider must be registered before doing any work
        if not ProviderRegistry.has("image_gen", provider):
            err = f"{provider} provider not registered (DASHSCOPE_API_KEY missing)"
            return AgentResult(
                success=False,
                output={"error": err},
                trace=trace,
                citations=[],
                error=err,
            )

        # Step 1: Fetch or generate substrate summary
        t0 = time.monotonic()
        summary = _fetch_substrate_summary(substrate_id)
        used_llm_for_summary = False
        if not summary:
            used_llm_for_summary = True
            try:
                resp: LLMResponse = llm_call(
                    prompt=_SUMMARY_PROMPT_TEMPLATE.format(substrate_id=substrate_id),
                    provider=self.llm_provider,
                    temperature=0.0,
                    max_tokens=200,
                )
                summary = resp.text.strip()
            except Exception as exc:
                err = f"Failed to generate substrate summary: {exc}"
                trace.append(
                    AgentStep(
                        step_num=1,
                        tool_name="llm_summarize",
                        tool_input={"substrate_id": substrate_id},
                        error=str(exc),
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
                return AgentResult(
                    success=False,
                    output={"error": err},
                    trace=trace,
                    citations=[],
                    error=err,
                )

        trace.append(
            AgentStep(
                step_num=1,
                tool_name="llm_summarize",
                tool_input={"substrate_id": substrate_id, "used_llm": used_llm_for_summary},
                tool_output={"summary_len": len(summary)},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )

        # Step 2: Generate image prompts via LLM
        image_prompts: list[str] = []
        for i in range(image_count):
            t0 = time.monotonic()
            try:
                resp = llm_call(
                    prompt=_PROMPT_TEMPLATE.format(style=style, summary=summary),
                    provider=self.llm_provider,
                    temperature=0.3,
                    max_tokens=256,
                )
                prompt_text = resp.text.strip()
            except Exception as exc:
                err = f"LLM prompt generation failed: {exc}"
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="llm_call_prompt",
                        tool_input={"style": style, "image_index": i},
                        error=str(exc),
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
                return AgentResult(
                    success=False,
                    output={"error": err},
                    trace=trace,
                    citations=[],
                    error=err,
                )
            image_prompts.append(prompt_text)
            trace.append(
                AgentStep(
                    step_num=len(trace) + 1,
                    tool_name="llm_call_prompt",
                    tool_input={"style": style, "image_index": i},
                    tool_output={"prompt_len": len(prompt_text)},
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            )

        # Step 3: Generate images
        width, height = _ASPECT_SIZES.get(aspect_ratio, (1024, 1024))
        img_dir = _resolve_output_dir(output_dir)

        image_paths: list[str] = []
        for i, prompt_text in enumerate(image_prompts):
            t0 = time.monotonic()
            try:
                asset_id = uuid.uuid4().hex
                out_path = img_dir / f"{asset_id}.png"
                await image_generate(
                    provider=provider,
                    prompt=prompt_text,
                    width=width,
                    height=height,
                    output_path=out_path,
                )
                image_paths.append(str(out_path))
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="image_generate",
                        tool_input={
                            "provider": provider,
                            "prompt_len": len(prompt_text),
                            "width": width,
                            "height": height,
                        },
                        tool_output={"image_path": str(out_path)},
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
            except Exception as exc:
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="image_generate",
                        tool_input={"provider": provider, "image_index": i},
                        error=str(exc),
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
                return AgentResult(
                    success=False,
                    output={"error": str(exc), "image_paths": image_paths},
                    trace=trace,
                    citations=[],
                    error=str(exc),
                )

        # Step 4: Write derivatives
        derivative_ids: list[str] = []
        for img_path in image_paths:
            t0 = time.monotonic()
            try:
                derivative_id = _save_illustration_derivative(
                    substrate_id=substrate_id,
                    image_path=img_path,
                    style=style,
                    provider=provider,
                )
                derivative_ids.append(derivative_id)
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="write_derivative",
                        tool_input={"substrate_id": substrate_id, "image_path": img_path},
                        tool_output={"derivative_id": derivative_id},
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
            except Exception as exc:
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="write_derivative",
                        tool_input={"substrate_id": substrate_id},
                        error=str(exc),
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
                return AgentResult(
                    success=False,
                    output={
                        "error": f"Derivative write failed: {exc}",
                        "image_paths": image_paths,
                    },
                    trace=trace,
                    citations=[],
                    error=f"Derivative write failed: {exc}",
                )

        return AgentResult(
            success=True,
            output={
                "substrate_id": substrate_id,
                "image_paths": image_paths,
                "prompts_used": image_prompts,
                "provider": provider,
                "derivative_ids": derivative_ids,
                "images_generated": len(image_paths),
            },
            trace=trace,
            citations=[Citation(substrate_id=substrate_id)],
        )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fetch_substrate_summary(substrate_id: str) -> str:
    """Fetch cached summary from meta_db derivative or substrate row."""
    try:
        from oskill.knowledge._context import meta_db_path

        from oprim.meta_db import open_meta_db

        db_path = meta_db_path()
        if not db_path.exists():
            return ""
        db = open_meta_db(db_path)
        rows = db.fetchall(
            "SELECT summary FROM substrates WHERE id = ? LIMIT 1",
            [substrate_id],
        )
        db.close()
        if rows and rows[0][0]:
            return str(rows[0][0])
    except Exception:
        pass
    return ""


def _save_illustration_derivative(
    substrate_id: str,
    image_path: str,
    style: str,
    provider: str,
) -> str:
    """Insert illustration derivative record into meta_db; return derivative ULID."""
    from oskill.knowledge._context import meta_db_path

    from oprim.meta_db import open_meta_db

    derivative_id = uuid.uuid4().hex
    db = open_meta_db(meta_db_path())
    db.execute(
        """
        INSERT INTO derivative
            (id, substrate_id, kind, medium, file_path, meta_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            derivative_id,
            substrate_id,
            "illustration",
            "illustration",
            image_path,
            json.dumps({"style": style, "provider": provider}),
        ],
    )
    db.close()
    return derivative_id


def _resolve_output_dir(override: Path | None) -> Path:
    """Return the output directory for generated images."""
    if override is not None:
        override.mkdir(parents=True, exist_ok=True)
        return override
    try:
        from oskill.knowledge._context import stratum_home

        d = stratum_home() / "data" / "illustrations"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        d = Path(tempfile.mkdtemp())
        return d
