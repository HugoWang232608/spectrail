from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from spectrail.llm.request_profile import ModelRequestProfile


@dataclass(frozen=True)
class CompletionRequest:
    prompt: str
    request_profile: ModelRequestProfile
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompletionResponse:
    raw_text: str
    model_name: str | None
    usage: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CompletionTransport(Protocol):
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        ...
