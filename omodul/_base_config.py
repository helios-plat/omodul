from typing import ClassVar, Literal

from pydantic import BaseModel


class BaseConfig(BaseModel):
    llm_provider: str = "anthropic"
    llm_model: str = "claude-3-5-sonnet-20241022"
    output_format: Literal["markdown", "pdf", "both"] = "markdown"
    budget_usd: float = 5.0
    overwrite: bool = True

    _omodul_name: ClassVar[str] = ""
    _omodul_version: ClassVar[str] = ""
    _fingerprint_fields: ClassVar[set[str]] = set()
