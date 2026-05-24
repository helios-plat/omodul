import hashlib
import json
from typing import Any, Literal


def compute_fingerprint(
    config: Any,
    input_data: Any,
    *,
    input_hash_strategy: Literal["pydantic_canonical", "dataframe_columns_sample", "dict"] = "pydantic_canonical",
) -> str:
    """SHA-256 64 字符. 共用 helper, 各 omodul 调用并传 config + input_data."""
    # Ensure _fingerprint_fields is a set
    fingerprint_fields: set[str] = getattr(config, "_fingerprint_fields", set())

    config_subset = {
        k: getattr(config, k)
        for k in sorted(list(fingerprint_fields))
        if hasattr(config, k)
    }

    input_hash = _hash_input_data(input_data, strategy=input_hash_strategy)

    fingerprint_input = {
        "omodul_name": getattr(config, "_omodul_name", ""),
        "omodul_version": getattr(config, "_omodul_version", ""),
        "config_subset": config_subset,
        "input_data_fingerprint": input_hash,
    }
    canonical = json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_input_data(input_data: Any, *, strategy: str) -> str:
    """input_data 类型分派 hash."""
    if hasattr(input_data, "model_dump"):  # pydantic BaseModel
        canonical = json.dumps(input_data.model_dump(), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    elif isinstance(input_data, dict):
        canonical = json.dumps(input_data, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    else:
        # Fallback to str() or something stable
        try:
            canonical = json.dumps(input_data, sort_keys=True, default=str)
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except Exception:
            return hashlib.sha256(str(input_data).encode("utf-8")).hexdigest()
