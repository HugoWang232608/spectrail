from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from spectrail.core.models import DocumentBlock


@dataclass(frozen=True)
class ModelRequest:
    document_text: str
    blocks: list[DocumentBlock]
    document_name: str
    source_format: str
    parser_name: str
    model_mode: str
    model_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    payload: dict[str, Any]
    model_mode: str
    model_name: str | None = None
    raw_text: str | None = None
    prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    model_mode: str

    def generate(self, request: ModelRequest) -> ModelResponse:
        ...
