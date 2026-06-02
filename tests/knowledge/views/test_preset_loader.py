"""Tests for omodul.knowledge.views.preset_loader."""

from __future__ import annotations

import pytest

from omodul.knowledge.views.crud import list_views
from omodul.knowledge.views.preset_loader import install_builtin_views

_USER = "preset_user"
_EXPECTED_NAMES = {"通用", "中文文学", "量化金融", "技术阅读", "工作日志"}


class TestInstallBuiltinViews:
    def test_installs_five_views(self):
        installed = install_builtin_views(_USER)
        assert len(installed) == 5
        assert {v["name"] for v in installed} == _EXPECTED_NAMES

    def test_idempotent_on_second_call(self):
        install_builtin_views(_USER)
        second = install_builtin_views(_USER)
        assert second == []  # nothing new installed

    def test_views_persisted_to_db(self):
        install_builtin_views(_USER)
        views = list_views(_USER)
        assert len(views) == 5
        assert {v["name"] for v in views} == _EXPECTED_NAMES

    def test_default_view_is_tongyong(self):
        install_builtin_views(_USER)
        views = list_views(_USER)
        defaults = [v for v in views if v["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["name"] == "通用"

    def test_all_views_are_builtin(self):
        install_builtin_views(_USER)
        for v in list_views(_USER):
            assert v["is_builtin"] is True

    def test_quant_finance_has_domain_filter(self):
        install_builtin_views(_USER)
        views = list_views(_USER)
        quant = next(v for v in views if v["name"] == "量化金融")
        assert "quant" in quant["default_filter"].get("domain", [])

    def test_work_log_has_time_range(self):
        install_builtin_views(_USER)
        views = list_views(_USER)
        wl = next(v for v in views if v["name"] == "工作日志")
        assert wl["default_filter"].get("time_range") == "last_30d"

    def test_chinese_literature_has_system_prompt(self):
        install_builtin_views(_USER)
        views = list_views(_USER)
        cl = next(v for v in views if v["name"] == "中文文学")
        assert cl["default_system_prompt"] is not None
        assert len(cl["default_system_prompt"]) > 5

    def test_partial_install_is_idempotent(self):
        """If only some views exist, install only missing ones."""
        from omodul.knowledge.views.crud import create_view

        create_view(_USER, {"name": "通用", "is_default": True})
        installed = install_builtin_views(_USER)
        assert len(installed) == 4
        assert all(v["name"] != "通用" for v in installed)
