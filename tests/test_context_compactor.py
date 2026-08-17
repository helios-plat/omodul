"""context_compactor folds only after the token threshold."""

from __future__ import annotations

import pytest

from omodul.context_compactor import CompactorConfig, context_compactor


@pytest.mark.asyncio
async def test_below_threshold_unchanged(tmp_path) -> None:
    messages = [{"role": "user", "content": "hi"}]
    rec = await context_compactor(
        CompactorConfig(token_threshold=10_000),
        {"messages": messages},
        tmp_path,
    )
    assert rec["status"] == "completed"
    assert rec["findings"]["compacted"] is False
    assert rec["findings"]["compacted_messages"] == messages


@pytest.mark.asyncio
async def test_over_threshold_folds(tmp_path) -> None:
    messages = [{"role": "user", "content": "x" * 80} for _ in range(10)]
    rec = await context_compactor(
        CompactorConfig(token_threshold=5, keep_prefix=1, keep_suffix=1),
        {"messages": messages, "current_ast": {"n": 1}},
        tmp_path,
    )
    assert rec["findings"]["compacted"] is True
    folded = rec["findings"]["compacted_messages"]
    assert folded[0] == messages[0]
    assert any(item.get("kind") == "ast_snapshot" for item in folded)
    assert folded[-1] == messages[-1]
    assert len(folded) < len(messages)
