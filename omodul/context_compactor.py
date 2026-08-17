"""omodul.context_compactor — fold history when the token estimate overflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from obase.token_counter import token_counter
from oskill.context_folding import apply_semantic_folding
from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result


class CompactorConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "context_compactor"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    token_threshold: int = 80_000
    keep_prefix: int = 2
    keep_suffix: int = 4


class CompactorInput(BaseModel):
    messages: list[dict[str, Any]]
    current_ast: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


def compute_fingerprint_for(config: CompactorConfig, input_data: CompactorInput) -> str:
    return "context_compactor_volatile"


def _estimate(messages: list[dict[str, Any]]) -> int:
    return sum(token_counter(str(item.get("content") or "")) for item in messages)


async def context_compactor(
    config: CompactorConfig,
    input_data: CompactorInput | dict[str, Any],
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    trail = Trail()
    if not isinstance(config, CompactorConfig):
        config = CompactorConfig.model_validate(config or {})
    data = (
        input_data
        if isinstance(input_data, CompactorInput)
        else CompactorInput.model_validate(input_data)
    )
    fp = compute_fingerprint_for(config, data)
    try:
        tokens = _estimate(data.messages)
        trail.record(event="estimate", tokens=tokens, threshold=config.token_threshold)
        if tokens <= config.token_threshold:
            findings = {
                "compacted": False,
                "compacted_messages": data.messages,
                "tokens": tokens,
            }
            return build_result(
                status="completed",
                error=None,
                trail=trail,
                fingerprint=fp,
                findings=findings,
            )

        folded = apply_semantic_folding(
            data.messages,
            current_ast=data.current_ast,
            keep_prefix=config.keep_prefix,
            keep_suffix=config.keep_suffix,
        )
        trail.record(
            event="folded",
            before=len(data.messages),
            after=len(folded),
        )
        if on_step:
            on_step({"folded": True, "after": len(folded)})
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            trail.write(Path(output_dir))
        return build_result(
            status="completed",
            error=None,
            trail=trail,
            fingerprint=fp,
            findings={
                "compacted": True,
                "compacted_messages": folded,
                "tokens": tokens,
                "tokens_after": _estimate(folded),
            },
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            fingerprint=fp,
            findings={"compacted": False, "compacted_messages": data.messages},
        )
