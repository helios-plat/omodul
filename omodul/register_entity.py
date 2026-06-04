"""omodul.register_entity —— 注册一个实体到知识图（SPEC-2）。

3O 层级：omodul（组合 ≥2 操作 + 业务事务语义）。
内部组合（3O §5.6）：
    ontology_validate（校验）
    storage.put_node（写入，经 StorageBackend 抽象）

支柱（Owner 必修3）：_enabled_pillars = {fingerprint, decision_trail}
    fingerprint —— 服务层据此去重，避免重复注册同一实体。
    decision_trail —— 审计实体注册（校验+写入两步可追溯）。
    不启 report（无交付物）/ cost（无 LLM）。

fingerprint 语义（ADR-A09）：按内容，不按 entity_id。
    指纹基于 {type} + 该类型「语义主体字段」，不含 entity_id/时间戳/owner。
    效果：同内容不同 id → 同指纹（服务层识别为重复）。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import ConfigDict

from omodul._base_config import BaseConfig

# ---------------------------------------------------------------------------
# 本地工具函数（替代 aii._obase 导入）
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _sha256_hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 本地异常（替代 aii.errors 导入）
# ---------------------------------------------------------------------------


class OntologyViolationError(Exception):
    """实体类型未注册，或缺必填字段。"""

    code = "ERR_ONTOLOGY_VIOLATION"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


# ---------------------------------------------------------------------------
# 本地 ontology_validate（替代 aii._staging.oprim.ontology_validate 导入）
# ---------------------------------------------------------------------------


def _ontology_validate(*, entity: dict, ontology: dict) -> bool:
    """纯函数校验实体是否符合给定 Ontology。"""
    etype = entity.get("type")
    if etype is None:
        raise OntologyViolationError("entity missing 'type' field", detail={"reason": "no_type"})

    if etype not in ontology:
        raise OntologyViolationError(
            f"entity type not registered: {etype!r}",
            detail={"reason": "type_not_registered", "type": etype},
        )

    required = ontology[etype].get("required", [])
    missing = [f for f in required if f not in entity or entity[f] in (None, "")]
    if missing:
        raise OntologyViolationError(
            f"entity of type {etype!r} missing required fields: {missing}",
            detail={"reason": "missing_required", "type": etype, "missing": missing},
        )

    return True


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class RegisterEntityConfig(BaseConfig):
    """register_entity 配置."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _omodul_name: ClassVar[str] = "register_entity"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"type"}

    ontology: dict
    backend: Any  # StorageBackend — arbitrary type allowed


# 该 omodul 启用的四支柱子集（3O §5.3，显式声明）
_enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _semantic_fields(entity: dict, ontology: dict) -> list[str]:
    """取该实体类型的「语义主体字段」（进 fingerprint 的字段）。"""
    etype = entity["type"]
    spec = ontology.get(etype, {})
    if "semantic_fields" in spec:
        return list(spec["semantic_fields"])
    NON_SEMANTIC = {"entity_id", "id", "created_at", "updated_at", "owner", "timestamp"}
    required = spec.get("required", [])
    return [f for f in required if f not in NON_SEMANTIC]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_fingerprint_for(config: RegisterEntityConfig, input_data: dict) -> str:
    """公开函数（3O §5.11）：供服务层「先算指纹、再决定是否注册」。
    指纹 = sha256(canonical_json({type, <语义字段:值>}))，按内容、不含 id。
    """
    if isinstance(config, dict):
        config = RegisterEntityConfig(**config) if config else RegisterEntityConfig()
    ontology = config.ontology
    entity = input_data["entity"]
    fields = _semantic_fields(entity, ontology)
    basis = {"type": entity["type"]}
    for f in fields:
        basis[f] = entity.get(f)
    return _sha256_hash(_canonical_json(basis))


def register_entity(
    config: RegisterEntityConfig,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """标准 omodul 签名 (config, input_data, output_dir) → dict（3O §5.2）。

    config:     RegisterEntityConfig（含 ontology + backend）
    input_data: {"entity": {...}}   实体必含 'type' 与 'entity_id'
    output_dir: decision_trail.json 落盘目录（None 则不落盘，仍返回 trail）
    on_step:    每步回调（可选）

    返回（omodul 标准 dict）：
        findings:    注册的 entity_id（失败 None）
        status:      "completed" | "failed"
        error:       失败原因（成功为 None）
        fingerprint: 内容指纹（fingerprint 支柱）
        decision_trail: 执行轨迹（decision_trail 支柱）
        report_path: None（未启用）
        cost_usd:    0.0（未启用）

    失败不 raise（3O §5.12）：校验失败返回 status=failed，仍写 decision_trail。
    """
    if isinstance(config, dict):
        config = RegisterEntityConfig(**config) if config else RegisterEntityConfig()
    trail: list[dict] = []
    fingerprint: str | None = None
    entity_id = None
    status = "failed"
    error = None

    def _emit(step_dict: dict) -> None:
        trail.append(step_dict)
        if on_step is not None:
            on_step(step_dict)

    try:
        ontology = config.ontology
        backend = config.backend
        entity = input_data["entity"]
        entity_id = entity.get("entity_id")

        # step 1: Ontology 校验
        step = {"step": "ontology_validate", "input_type": entity.get("type")}
        _emit(step)
        _ontology_validate(entity=entity, ontology=ontology)
        trail[-1]["result"] = "pass"

        # step 2: 算内容指纹（fingerprint 支柱）
        fingerprint = compute_fingerprint_for(config=config, input_data=input_data)
        _emit({"step": "compute_fingerprint", "fingerprint": fingerprint})

        # step 3: 写入图节点（经 StorageBackend）
        if not entity_id:
            raise OntologyViolationError(
                "entity missing 'entity_id'", detail={"reason": "no_entity_id"}
            )
        backend.put_node(entity_id, entity)
        _emit({"step": "put_node", "node_id": entity_id, "result": "ok"})

        status = "completed"
    except OntologyViolationError as e:
        error = {"code": e.code, "message": e.message, "detail": e.detail}
        _emit({"step": "abort", "error": error})
    except Exception as e:  # 任何其它异常也不外抛，转 failed（omodul §5.12）
        error = {"code": "ERR_UNEXPECTED", "message": str(e)}
        _emit({"step": "abort", "error": error})
    finally:
        decision_trail = {
            "omodul": "register_entity",
            "enabled_pillars": sorted(_enabled_pillars),
            "entity_id": entity_id,
            "status": status,
            "steps": trail,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "decision_trail.json"), "w", encoding="utf-8") as f:
                json.dump(decision_trail, f, ensure_ascii=False, indent=2)

    return {
        "findings": entity_id if status == "completed" else None,
        "status": status,
        "error": error,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": None,
        "cost_usd": 0.0,
    }
