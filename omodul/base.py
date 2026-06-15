# omodul/base.py
"""omodul 基类。所有 omodul Config 继承 BaseConfig，遵守标准签名。"""
from __future__ import annotations
from pydantic import BaseModel
from typing import ClassVar, Literal, Optional, Any
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

class BaseConfig(BaseModel):
    """所有 omodul Config 基类。"""
    llm_provider: str = "default"
    budget_usd: float = 5.0
    output_format: Literal["markdown", "json", "both"] = "markdown"
    overwrite: bool = True

    # 子类必须覆盖
    _omodul_name: ClassVar[str] = ""
    _omodul_version: ClassVar[str] = ""
    _fingerprint_fields: ClassVar[set[str]] = set()
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}

def build_fingerprint(config: BaseConfig, input_hash: str) -> str:
    """计算 omodul fingerprint（SHA-256，64 字符）。"""
    subset = {k: getattr(config, k) for k in sorted(config._fingerprint_fields)
              if hasattr(config, k)}
    obj = {
        "omodul_name": config._omodul_name,
        "omodul_version": config._omodul_version,
        "config_subset": subset, 
        "input_hash": input_hash
    }
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
        .encode()
    ).hexdigest()

def standard_return(*, findings: Any, status: str, error: Optional[str] = None, 
                   fingerprint: Optional[str] = None, trail: Optional[list] = None,
                   report_path: Optional[str] = None, cost_usd: float = 0.0) -> dict:
    """omodul 标准返回结构。"""
    return {
        "findings": findings, 
        "status": status, 
        "error": error,
        "fingerprint": fingerprint, 
        "decision_trail": trail,
        "report_path": report_path, 
        "cost_usd": cost_usd
    }
