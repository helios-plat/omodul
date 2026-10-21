"""Phase 3-4 全量验证 — 物理具身协议 (Embodied-MCP).

关键验收:
1. 物理限位安全: 越界坐标 (10.0, 10.0, 10.0) → MQTT 下发前熔断
2. 网关隔离降级: MQTT Broker 宕机 → 安全降级不崩溃
3. 万向节死锁: (0, 0, 0) 原点 → gimbal_lock_detected=True, 数学稳定
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock

from obase.mqtt_edge_gateway import MQTTEdgeGateway
from oprim._publish_hardware_command import _publish_hardware_command
from oskill._embodied_kinematics_serialize import _embodied_kinematics_serialize

from omodul.embodied_action_transaction import Config as EmbodiedConfig
from omodul.embodied_action_transaction import InputData as EmbodiedInput
from omodul.embodied_action_transaction import embodied_action_transaction

# ============================================================================
# obase: MQTTEdgeGateway
# ============================================================================

class TestMQTTEdgeGateway:
    def test_health_disconnected(self):
        gw = MQTTEdgeGateway()
        h = gw.health()
        assert h["status"] == "disconnected"
        assert not gw.connected

    def test_mqtt_availability_checked(self):
        gw = MQTTEdgeGateway()
        assert isinstance(gw.mqtt_available, bool)

    def test_connect_no_broker_graceful(self):
        """Broker 未启动 → connect 返回 False 不抛异常."""
        gw = MQTTEdgeGateway(broker_host="127.0.0.1", port=19999)
        result = gw.connect()
        # 可能连接失败 (broker 不在) 或成功 (broker 刚好在)
        assert isinstance(result, bool)

    def test_publish_disconnected_returns_false(self):
        gw = MQTTEdgeGateway()
        assert gw.publish("test/topic", b"data") is False

    def test_disconnect_safe(self):
        gw = MQTTEdgeGateway()
        gw.disconnect()  # 不应抛异常
        assert not gw.connected


# ============================================================================
# oprim: _publish_hardware_command
# ============================================================================

class TestPublishHardwareCommand:
    def test_disconnected_gateway(self):
        gw = MagicMock()
        gw.connected = False
        result = _publish_hardware_command("cmd/move", gateway=gw, payload=b"go")
        assert result["status"] == "error"
        assert "未连接" in result["error"]

    def test_publish_success(self):
        gw = MagicMock()
        gw.connected = True
        gw.publish.return_value = True
        result = _publish_hardware_command("cmd/move", gateway=gw, payload=b"\x01\x02")
        assert result["status"] == "success"
        assert result["bytes_sent"] == 2

    def test_publish_failure(self):
        gw = MagicMock()
        gw.connected = True
        gw.publish.return_value = False
        result = _publish_hardware_command("cmd/move", gateway=gw, payload=b"x")
        assert result["status"] == "error"

    def test_publish_exception_caught(self):
        gw = MagicMock()
        gw.connected = True
        gw.publish.side_effect = ConnectionError("timeout")
        result = _publish_hardware_command("t", gateway=gw, payload=b"x")
        assert result["status"] == "error"

    def test_positional_only_one(self):
        import inspect
        sig = inspect.signature(_publish_hardware_command)
        positional = [p for p in sig.parameters.values()
                      if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD]
        assert len(positional) <= 1


# ============================================================================
# oskill: _embodied_kinematics_serialize
# ============================================================================

class TestEmbodiedKinematicsSerialize:
    def test_normal_coordinate(self):
        result = _embodied_kinematics_serialize((0.5, 0.0, 0.5), max_radius=1.0)
        assert result["valid"] is True
        assert result["payload_bytes"] is not None
        assert not result["gimbal_lock_detected"]

    def test_origin_gimbal_lock(self):
        """原点 (0,0,0) → 万向节死锁."""
        result = _embodied_kinematics_serialize((0.0, 0.0, 0.0), max_radius=1.0)
        assert result["valid"] is True
        assert result["gimbal_lock_detected"] is True

    def test_vertical_gimbal_lock(self):
        """正上方 (0, 0, 1) → z≈r 导致 φ 不稳定."""
        result = _embodied_kinematics_serialize((0.0, 0.0, 1.0), max_radius=1.0)
        assert result["valid"] is True
        # phi ≈ 0 (正上方)
        assert abs(result["raw_dict"]["joint_2_rad"]) < 0.01

    def test_out_of_bounds(self):
        result = _embodied_kinematics_serialize((10.0, 10.0, 10.0), max_radius=1.0)
        assert result["valid"] is False
        assert "超限" in result["error"]

    def test_inf_nan_rejected(self):
        result = _embodied_kinematics_serialize((float("inf"), 0, 0), max_radius=1.0)
        assert result["valid"] is False
        assert "非有限值" in result["error"]

    def test_nan_rejected(self):
        result = _embodied_kinematics_serialize((0, float("nan"), 0), max_radius=1.0)
        assert result["valid"] is False

    def test_payload_is_valid_json(self):
        result = _embodied_kinematics_serialize((0.3, 0.4, 0.5), max_radius=1.0)
        parsed = json.loads(result["payload_bytes"])
        assert "joint_1_rad" in parsed
        assert "joint_2_rad" in parsed
        assert "extension_m" in parsed

    def test_math_stability_at_edge(self):
        """atan2(0, -0) 等边界情况."""
        result = _embodied_kinematics_serialize((1e-15, -1e-15, 0.5), max_radius=1.0)
        assert result["valid"] is True
        assert all(math.isfinite(v) for v in result["raw_dict"].values() if isinstance(v, float))


# ============================================================================
# omodul: embodied_action_transaction
# ============================================================================

class TestEmbodiedActionTransaction:
    def test_successful_action(self, tmp_path: Path):
        """正常坐标 → completed."""
        cfg = EmbodiedConfig(
            gateway=MagicMock(),
            publish_op=_publish_hardware_command,
            kinematics_skill=_embodied_kinematics_serialize,
        )
        inp = EmbodiedInput(
            topic="veya/arm/test",
            target_xyz=(0.3, 0.4, 0.5),
            max_radius=1.0,
        )
        result = embodied_action_transaction(cfg, inp, tmp_path)
        # gateway 未连接 → 降级为 failed
        assert result["status"] in ("completed", "failed")

    def test_out_of_bounds_blocked(self, tmp_path: Path):
        """越界坐标 → failed + blocked=True."""
        cfg = EmbodiedConfig(
            gateway=None,
            publish_op=None,
            kinematics_skill=_embodied_kinematics_serialize,
        )
        inp = EmbodiedInput(
            topic="veya/arm/test",
            target_xyz=(10.0, 10.0, 10.0),
            max_radius=1.0,
        )
        result = embodied_action_transaction(cfg, inp, tmp_path)
        assert result["status"] == "failed"
        assert result.get("blocked") is True

    def test_gateway_disconnected_degraded(self, tmp_path: Path):
        """网关未连接 → 安全降级不崩溃."""
        cfg = EmbodiedConfig(
            gateway=None,
            publish_op=None,
            kinematics_skill=_embodied_kinematics_serialize,
        )
        inp = EmbodiedInput(
            topic="veya/arm/test",
            target_xyz=(0.1, 0.2, 0.3),
            max_radius=1.0,
        )
        result = embodied_action_transaction(cfg, inp, tmp_path)
        assert result["status"] in ("completed", "failed")

    def test_no_kinematics_skill_fallback(self, tmp_path: Path):
        """无 kinematics_skill 注入 → 降级通过."""
        cfg = EmbodiedConfig(gateway=None, publish_op=None, kinematics_skill=None)
        inp = EmbodiedInput(target_xyz=(0.5, 0, 0))
        result = embodied_action_transaction(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_gimbal_lock_still_executes(self, tmp_path: Path):
        """万向节死锁不应阻止执行，只标记."""
        cfg = EmbodiedConfig(
            gateway=None, publish_op=None,
            kinematics_skill=_embodied_kinematics_serialize,
        )
        inp = EmbodiedInput(target_xyz=(0.0, 0.0, 0.0), max_radius=1.0)
        result = embodied_action_transaction(cfg, inp, tmp_path)
        assert result["status"] == "completed"
        assert result.get("gimbal_lock") is True

    def test_config_has_pillars(self):
        assert EmbodiedConfig._enabled_pillars == {"fingerprint", "decision_trail", "report"}


# ============================================================================
# 集成测试: 端到端物理指令下发
# ============================================================================

class TestEndToEndEmbodied:
    def test_full_pipeline_valid(self, tmp_path: Path):
        """完整管道: 合法坐标 → 运动学 → 序列化 → 下发 (模拟)."""
        gw = MagicMock()
        gw.connected = True
        gw.publish.return_value = True

        cfg = EmbodiedConfig(
            gateway=gw,
            publish_op=_publish_hardware_command,
            kinematics_skill=_embodied_kinematics_serialize,
        )
        inp = EmbodiedInput(
            topic="veya/robot/joint_control",
            target_xyz=(0.5, 0.3, 0.7),
            max_radius=1.0,
        )
        result = embodied_action_transaction(cfg, inp, tmp_path)
        assert result["status"] == "completed"
        report = result["pillars"]["report"]
        assert report["bytes_sent"] > 0

    def test_full_pipeline_blocked(self, tmp_path: Path):
        """完整管道: 越界坐标 → 运动学拒绝 → 熔断 (无 MQTT 调用)."""
        gw = MagicMock()
        gw.connected = True

        cfg = EmbodiedConfig(
            gateway=gw,
            publish_op=_publish_hardware_command,
            kinematics_skill=_embodied_kinematics_serialize,
        )
        inp = EmbodiedInput(
            topic="veya/robot/joint_control",
            target_xyz=(5.0, 5.0, 5.0),
            max_radius=2.0,
        )
        result = embodied_action_transaction(cfg, inp, tmp_path)
        assert result["status"] == "failed"
        assert result.get("blocked") is True
        # 确保 MQTT publish 从未被调用
        gw.publish.assert_not_called()
