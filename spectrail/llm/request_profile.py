from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelRequestProfile:
    provider_adapter: str
    provider_endpoint_id: str
    model_name: str
    temperature: float = 0.0
    response_format: dict[str, Any] | None = None
    safe_request_options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderRequestAdapter(Protocol):
    adapter_id: str

    def build_provider_request_body(self, *, prompt: str, profile: ModelRequestProfile) -> dict[str, Any]:
        ...

    def sanitize_request_body(self, body: dict[str, Any]) -> dict[str, Any]:
        ...


class OpenAICompatibleRequestAdapter:
    adapter_id = "openai_compatible_v1"

    def build_provider_request_body(self, *, prompt: str, profile: ModelRequestProfile) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": profile.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": profile.temperature,
        }
        if profile.response_format is not None:
            body["response_format"] = profile.response_format
        body.update(profile.safe_request_options)
        return body

    def sanitize_request_body(self, body: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"api_key", "authorization", "cookie", "access_token"}
        return {key: value for key, value in body.items() if key.lower() not in forbidden}


OPENAI_COMPATIBLE_ADAPTER = OpenAICompatibleRequestAdapter()


def adapter_for_profile(profile: ModelRequestProfile) -> ProviderRequestAdapter:
    if profile.provider_adapter == OPENAI_COMPATIBLE_ADAPTER.adapter_id:
        return OPENAI_COMPATIBLE_ADAPTER
    raise ValueError(f"unsupported provider adapter: {profile.provider_adapter}")
