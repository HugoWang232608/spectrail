from __future__ import annotations

from typing import Any

from spectrail.llm.base import ModelRequest, ModelResponse
from spectrail.llm.openai_compatible_transport import (
    DEFAULT_BASE_URL,
    OpenAICompatibleTransport,
    _load_dotenv,
)
from spectrail.llm.prompt_builder import PROMPT_VERSION, build_reqir_prompt
from spectrail.llm.request_profile import ModelRequestProfile
from spectrail.llm.response_parser import parse_model_response
from spectrail.llm.transport import CompletionRequest


class OpenAICompatibleModel:
    """ReqIR compatibility wrapper over the generic completion transport."""

    model_mode = "live"

    def __init__(
        self,
        model_name: str | None = None,
        *,
        base_url: str | None = None,
        endpoint_id: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url
        self.endpoint_id = endpoint_id
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = OpenAICompatibleTransport(
            model_name=model_name,
            base_url=base_url,
            endpoint_id=endpoint_id,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        insecure = bool(request.metadata.get("insecure"))
        prompt = build_reqir_prompt(request)
        profile = self.resolve_request_profile(
            request.request_profile,
            insecure=insecure,
        )
        completion = self.transport.complete(
            CompletionRequest(
                prompt=prompt,
                request_profile=profile,
                metadata={
                    "insecure": insecure,
                    "prompt_version": request.metadata.get(
                        "prompt_version",
                        PROMPT_VERSION,
                    ),
                },
            )
        )
        payload = parse_model_response(completion.raw_text)
        return ModelResponse(
            payload=payload,
            model_mode=self.model_mode,
            model_name=completion.model_name,
            raw_text=completion.raw_text,
            prompt=prompt,
            metadata={
                **completion.metadata,
                "model_name": completion.model_name,
                "usage": completion.usage,
            },
        )

    def resolve_transport(self, *, insecure: bool = False) -> dict[str, Any]:
        return self.transport.resolve_transport(insecure=insecure)

    def resolve_request_profile(
        self,
        explicit_profile: ModelRequestProfile | None,
        *,
        insecure: bool = False,
    ) -> ModelRequestProfile:
        return self.transport.resolve_request_profile(
            explicit_profile,
            insecure=insecure,
        )

    def _load_config(self, *, insecure: bool = False) -> dict[str, Any]:
        return self.transport._load_config(insecure=insecure)

    def _complete(
        self,
        *,
        body: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        return self.transport._complete(body=body, config=config)


__all__ = [
    "DEFAULT_BASE_URL",
    "OpenAICompatibleModel",
    "_load_dotenv",
]
