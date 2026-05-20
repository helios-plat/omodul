"""Tests for omodul.knowledge.views.applier (pure function)."""
from __future__ import annotations

import pytest

from omodul.knowledge.views.applier import apply_view

_VIEW_QUANT = {
    "default_filter": {"medium": ["paper", "article"], "domain": ["quant", "finance"]},
    "default_llm": {"provider": "deepseek", "model": "deepseek-chat"},
    "default_system_prompt": "You are a quant assistant.",
}

_VIEW_WORK_LOG = {
    "default_filter": {"medium": ["note"], "time_range": "last_30d"},
    "default_llm": {},
    "default_system_prompt": None,
}


class TestApplyView:
    def test_none_view_returns_params_unchanged(self):
        params = {"query": "hello", "top_k": 5}
        result = apply_view({}, params)
        assert result == params

    def test_medium_filter_applied(self):
        result = apply_view(_VIEW_QUANT, {})
        assert result["medium_filter"] == ["paper", "article"]

    def test_domain_filter_applied(self):
        result = apply_view(_VIEW_QUANT, {})
        assert result["domain_filter"] == ["quant", "finance"]

    def test_time_range_applied(self):
        result = apply_view(_VIEW_WORK_LOG, {})
        assert result["time_range"] == "last_30d"
        assert result["medium_filter"] == ["note"]

    def test_user_medium_filter_overrides_view(self):
        result = apply_view(_VIEW_QUANT, {"medium_filter": ["book"]})
        assert result["medium_filter"] == ["book"]

    def test_user_domain_filter_overrides_view(self):
        result = apply_view(_VIEW_QUANT, {"domain_filter": ["crypto"]})
        assert result["domain_filter"] == ["crypto"]

    def test_llm_provider_applied(self):
        result = apply_view(_VIEW_QUANT, {})
        assert result["llm_provider"] == "deepseek"
        assert result["llm_model"] == "deepseek-chat"

    def test_user_llm_overrides_view(self):
        result = apply_view(_VIEW_QUANT, {"llm_provider": "claude", "llm_model": "claude-opus-4-7"})
        assert result["llm_provider"] == "claude"
        assert result["llm_model"] == "claude-opus-4-7"

    def test_system_prompt_applied(self):
        result = apply_view(_VIEW_QUANT, {})
        assert result["system_prompt"] == "You are a quant assistant."

    def test_user_system_prompt_overrides_view(self):
        result = apply_view(_VIEW_QUANT, {"system_prompt": "Be brief."})
        assert result["system_prompt"] == "Be brief."

    def test_view_with_no_llm_leaves_params(self):
        view = {"default_filter": {}, "default_llm": {}, "default_system_prompt": None}
        params = {"query": "test", "top_k": 10}
        result = apply_view(view, params)
        assert "llm_provider" not in result
        assert "system_prompt" not in result
        assert result["top_k"] == 10

    def test_does_not_mutate_input(self):
        params = {"top_k": 3}
        apply_view(_VIEW_QUANT, params)
        assert "medium_filter" not in params

    def test_passthrough_extra_params(self):
        result = apply_view(_VIEW_QUANT, {"mode": "strict", "pinned_boost": 2.0})
        assert result["mode"] == "strict"
        assert result["pinned_boost"] == 2.0
