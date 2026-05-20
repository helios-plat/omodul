"""Tests for omodul.knowledge.views.crud (sync DuckDB)."""
from __future__ import annotations

import pytest

from omodul.knowledge.views.crud import (
    create_view,
    delete_view,
    get_default_view,
    get_view,
    list_views,
    set_default,
    update_view,
)

_USER = "u1"
_USER2 = "u2"

_SPEC = {
    "name": "Test View",
    "description": "A test view",
    "default_filter": {"medium": ["paper"]},
    "default_llm": {"provider": "deepseek", "model": "deepseek-chat"},
    "default_system_prompt": "Be concise.",
    "icon": "🔬",
    "is_default": False,
    "is_builtin": False,
}


class TestCreateGet:
    def test_create_returns_dict_with_id(self):
        v = create_view(_USER, _SPEC)
        assert isinstance(v["id"], str) and len(v["id"]) > 0
        assert v["name"] == "Test View"
        assert v["user_id"] == _USER

    def test_create_stores_filter_and_llm(self):
        v = create_view(_USER, _SPEC)
        assert v["default_filter"] == {"medium": ["paper"]}
        assert v["default_llm"] == {"provider": "deepseek", "model": "deepseek-chat"}
        assert v["default_system_prompt"] == "Be concise."
        assert v["icon"] == "🔬"

    def test_get_view_returns_same_data(self):
        created = create_view(_USER, _SPEC)
        fetched = get_view(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["name"] == "Test View"

    def test_get_view_unknown_returns_none(self):
        assert get_view("nonexistent-id") is None

    def test_create_empty_filter_defaults(self):
        v = create_view(_USER, {"name": "Minimal"})
        assert v["default_filter"] == {}
        assert v["default_llm"] == {}
        assert v["default_system_prompt"] is None
        assert v["is_default"] is False
        assert v["is_builtin"] is False


class TestList:
    def test_list_empty_for_new_user(self):
        assert list_views("nobody") == []

    def test_list_returns_all_for_user(self):
        create_view(_USER, {**_SPEC, "name": "A"})
        create_view(_USER, {**_SPEC, "name": "B"})
        views = list_views(_USER)
        assert len(views) == 2
        assert {v["name"] for v in views} == {"A", "B"}

    def test_list_isolates_by_user(self):
        create_view(_USER, _SPEC)
        create_view(_USER2, {**_SPEC, "name": "Other"})
        assert len(list_views(_USER)) == 1
        assert len(list_views(_USER2)) == 1

    def test_default_view_sorted_first(self):
        create_view(_USER, {**_SPEC, "name": "B", "is_default": False})
        vd = create_view(_USER, {**_SPEC, "name": "A", "is_default": True})
        views = list_views(_USER)
        assert views[0]["id"] == vd["id"]


class TestUpdate:
    def test_update_name(self):
        v = create_view(_USER, _SPEC)
        updated = update_view(v["id"], {"name": "Renamed"})
        assert updated["name"] == "Renamed"

    def test_update_filter(self):
        v = create_view(_USER, _SPEC)
        update_view(v["id"], {"default_filter": {"medium": ["book", "article"]}})
        fetched = get_view(v["id"])
        assert fetched["default_filter"]["medium"] == ["book", "article"]

    def test_update_ignores_unknown_keys(self):
        v = create_view(_USER, _SPEC)
        update_view(v["id"], {"is_builtin": True, "injected": "evil"})
        fetched = get_view(v["id"])
        assert fetched["name"] == "Test View"  # unchanged

    def test_update_nonexistent_returns_none(self):
        result = update_view("ghost-id", {"name": "X"})
        assert result is None


class TestDelete:
    def test_delete_removes_view(self):
        v = create_view(_USER, _SPEC)
        delete_view(v["id"])
        assert get_view(v["id"]) is None

    def test_delete_nonexistent_is_noop(self):
        delete_view("ghost-id")  # must not raise


class TestSetDefault:
    def test_set_default_marks_view(self):
        v = create_view(_USER, _SPEC)
        set_default(_USER, v["id"])
        fetched = get_view(v["id"])
        assert fetched["is_default"] is True

    def test_set_default_unsets_others(self):
        a = create_view(_USER, {**_SPEC, "name": "A", "is_default": True})
        b = create_view(_USER, {**_SPEC, "name": "B", "is_default": False})
        set_default(_USER, b["id"])
        assert get_view(a["id"])["is_default"] is False
        assert get_view(b["id"])["is_default"] is True

    def test_only_one_default_at_a_time(self):
        for name in ["X", "Y", "Z"]:
            create_view(_USER, {**_SPEC, "name": name})
        views_before = list_views(_USER)
        set_default(_USER, views_before[0]["id"])
        defaults = [v for v in list_views(_USER) if v["is_default"]]
        assert len(defaults) == 1

    def test_get_default_view_returns_default(self):
        v = create_view(_USER, _SPEC)
        set_default(_USER, v["id"])
        default = get_default_view(_USER)
        assert default is not None
        assert default["id"] == v["id"]

    def test_get_default_view_none_when_unset(self):
        create_view(_USER, {**_SPEC, "is_default": False})
        assert get_default_view(_USER) is None
