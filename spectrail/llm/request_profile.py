from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


SAFE_REQUEST_OPTION_KEYS = frozenset(
    {
        "frequency_penalty",
        "max_completion_tokens",
        "max_tokens",
        "presence_penalty",
        "seed",
        "stop",
        "top_p",
    }
)
RESERVED_REQUEST_BODY_KEYS = frozenset({"messages", "model", "response_format", "temperature"})
SECRET_KEY_FRAGMENTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "access_token",
)


@dataclass(frozen=True)
class ModelRequestProfile:
    provider_adapter: str
    provider_endpoint_id: str
    model_name: str
    temperature: float = 0.0
    response_format: dict[str, Any] | None = None
    safe_request_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_endpoint_id.strip():
            raise ValueError("provider_endpoint_id must be a non-empty logical identifier")
        if "://" in self.provider_endpoint_id or "@" in self.provider_endpoint_id:
            raise ValueError("provider_endpoint_id must not contain a URL or credentials")
        unsupported = set(self.safe_request_options) - SAFE_REQUEST_OPTION_KEYS
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"unsupported safe_request_options: {names}")
        secret_path = _find_secret_key(self.safe_request_options)
        if secret_path is not None:
            raise ValueError(f"secret-like request option is not allowed: {secret_path}")

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
        for key, value in profile.safe_request_options.items():
            if key in RESERVED_REQUEST_BODY_KEYS:
                raise ValueError(f"request option cannot override canonical field: {key}")
            body[key] = value
        return body

    def sanitize_request_body(self, body: dict[str, Any]) -> dict[str, Any]:
        return _sanitize_value(body)


OPENAI_COMPATIBLE_ADAPTER = OpenAICompatibleRequestAdapter()


def adapter_for_profile(profile: ModelRequestProfile) -> ProviderRequestAdapter:
    if profile.provider_adapter == OPENAI_COMPATIBLE_ADAPTER.adapter_id:
        return OPENAI_COMPATIBLE_ADAPTER
    raise ValueError(f"unsupported provider adapter: {profile.provider_adapter}")


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS)


def _find_secret_key(value: Any, path: str = "safe_request_options") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _is_secret_key(str(key)):
                return child_path
            found = _find_secret_key(child, child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_secret_key(child, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_value(child)
            for key, child in value.items()
            if not _is_secret_key(str(key))
        }
    if isinstance(value, list):
        return [_sanitize_value(child) for child in value]
    return value
