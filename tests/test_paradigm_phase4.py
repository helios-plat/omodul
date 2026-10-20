"""3O 范式 Phase 4 — omodul 业务事务测试.

覆盖: §5.3 四支柱声明、§5.5 防重指纹、§5.4 异常吞没、§5.6 C4 取消放行。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from obase import OmodulBaseConfig

from omodul import AuditConfig, AuditInput, audit_repository_workflow
from omodul.code_audit_workflow import compute_fingerprint_for

PILLARS = {"fingerprint", "decision_trail", "cost"}


def _input(**overrides) -> AuditInput:
    defaults = {
        "repo_url": "https://example.com/repo.git",
        "commit_hash": "abc123",
        "target_file": "src/main.py",
        "anon_user_ref": "anon-9f2a",
    }
    defaults.update(overrides)
    return AuditInput(**defaults)


def test_config_inherits_obase_base_with_pillars():
    """§5.3: 继承 obase.OmodulBaseConfig 且显式声明 _enabled_pillars。"""
    assert issubclass(AuditConfig, OmodulBaseConfig)
    assert AuditConfig._enabled_pillars == PILLARS
    assert AuditConfig._omodul_name == "code_audit_workflow"
    assert AuditConfig._omodul_version == "1.0.0"


def test_fingerprint_deterministic_and_pii_free():
    """§5.5: 指纹仅覆盖 _fingerprint_fields, 禁含 PII (anon_user_ref)。"""
    assert "anon_user_ref" not in AuditConfig._fingerprint_fields
    assert AuditConfig._fingerprint_fields == {"repo_url", "commit_hash", "target_file"}

    cfg = AuditConfig()
    f1 = compute_fingerprint_for(cfg, _input())
    f2 = compute_fingerprint_for(cfg, _input(anon_user_ref="anon-DIFFERENT"))
    f3 = compute_fingerprint_for(cfg, _input(commit_hash="different"))
    assert len(f1) == 24
    assert f1 == f2  # 匿名引用不参与指纹
    assert f1 != f3  # 业务字段参与指纹


async def test_workflow_completed(tmp_path):
    steps: list[dict] = []
    result = await audit_repository_workflow(
        AuditConfig(), _input(), tmp_path, on_step=lambda s: steps.append(s)
    )
    assert result["status"] == "completed"
    assert result["error"] is None
    assert result["fingerprint"] == compute_fingerprint_for(AuditConfig(), _input())
    assert isinstance(result["cost_usd"], float)
    assert len(steps) == 2  # step 1 + step 2 回调

    trail = json.loads((tmp_path / "decision_trail.json").read_text())
    assert trail[0] == {"action": "start", "file": "src/main.py"}
    assert trail[-1]["action"] == "audit_script"


async def test_workflow_failure_swallowed(tmp_path):
    """§5.4: 业务异常不抛出, 返回 status='failed'。"""

    def boom(_: dict) -> None:
        raise RuntimeError("disk on fire")

    result = await audit_repository_workflow(AuditConfig(), _input(), tmp_path, on_step=boom)
    assert result["status"] == "failed"
    assert result["error"] == {"type": "RuntimeError", "message": "disk on fire"}
    # 失败路径同样落盘决策轨迹
    trail = json.loads((tmp_path / "decision_trail.json").read_text())
    assert trail[-1]["action"] == "error"


async def test_workflow_cancelled_reraised(tmp_path):
    """§5.6 C4: CancelledError 必须重新 raise, 不得吞没。"""

    def cancel(_: dict) -> None:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await audit_repository_workflow(AuditConfig(), _input(), tmp_path, on_step=cancel)


async def test_workflow_pillar_gating(tmp_path):
    """支柱门控: 仅 fingerprint 时, 不落盘轨迹、不计成本。"""

    class MinimalConfig(AuditConfig):
        _enabled_pillars = {"fingerprint"}

    result = await audit_repository_workflow(MinimalConfig(), _input(), tmp_path)
    assert result["status"] == "completed"
    assert result["fingerprint"] is not None
    assert result["cost_usd"] == 0.0
    assert not (tmp_path / "decision_trail.json").exists()
