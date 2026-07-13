from __future__ import annotations

import json
import os
from pathlib import Path
import ssl
import urllib.error
import urllib.request
from typing import Any

from spectrail.llm.base import ModelRequest, ModelResponse
from spectrail.llm.errors import ModelConfigurationError, ModelProviderError
from spectrail.llm.prompt_builder import PROMPT_VERSION, build_reqir_prompt
from spectrail.llm.request_profile import ModelRequestProfile, OPENAI_COMPATIBLE_ADAPTER
from spectrail.llm.response_parser import parse_model_response


DEFAULT_BASE_URL = "https://api.openai.com/v1/chat/completions"


class OpenAICompatibleModel:
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

    def generate(self, request: ModelRequest) -> ModelResponse:
        config = self._load_config(insecure=bool(request.metadata.get("insecure")))
        prompt = build_reqir_prompt(request)
        profile = request.request_profile or ModelRequestProfile(
            provider_adapter="openai_compatible_v1",
            provider_endpoint_id=config["endpoint_id"],
            model_name=config["model_name"],
        )
        body = OPENAI_COMPATIBLE_ADAPTER.build_provider_request_body(prompt=prompt, profile=profile)
        raw_text, usage = self._complete(body=body, config=config)
        payload = parse_model_response(raw_text)
        return ModelResponse(
            payload=payload,
            model_mode=self.model_mode,
            model_name=config["model_name"],
            raw_text=raw_text,
            prompt=prompt,
            metadata={
                "provider_endpoint_id": config["endpoint_id"],
                "model_name": config["model_name"],
                "prompt_version": PROMPT_VERSION,
                "tls_verify": config["tls_verify"],
                "usage": usage,
            },
        )

    def _load_config(self, *, insecure: bool = False) -> dict[str, Any]:
        dotenv = _load_dotenv(Path(".env"))
        api_key = self.api_key or os.environ.get("SPECTRAIL_LLM_API_KEY") or dotenv.get("SPECTRAIL_LLM_API_KEY")
        if not api_key:
            raise ModelConfigurationError("SPECTRAIL_LLM_API_KEY is required for live mode")

        model_name = self.model_name or os.environ.get("SPECTRAIL_LLM_MODEL") or dotenv.get("SPECTRAIL_LLM_MODEL")
        if not model_name:
            raise ModelConfigurationError("SPECTRAIL_LLM_MODEL is required for live mode")

        timeout_raw = os.environ.get("SPECTRAIL_LLM_TIMEOUT_SECONDS") or dotenv.get("SPECTRAIL_LLM_TIMEOUT_SECONDS")
        timeout_seconds = self.timeout_seconds
        if timeout_seconds is None and timeout_raw:
            try:
                timeout_seconds = float(timeout_raw)
            except ValueError as exc:
                raise ModelConfigurationError("SPECTRAIL_LLM_TIMEOUT_SECONDS must be a number") from exc

        base_url = (
            self.base_url
            or os.environ.get("SPECTRAIL_LLM_BASE_URL")
            or dotenv.get("SPECTRAIL_LLM_BASE_URL")
            or DEFAULT_BASE_URL
        )
        endpoint_id = (
            self.endpoint_id
            or os.environ.get("SPECTRAIL_LLM_ENDPOINT_ID")
            or dotenv.get("SPECTRAIL_LLM_ENDPOINT_ID")
        )
        if endpoint_id is None and base_url == DEFAULT_BASE_URL:
            endpoint_id = "openai-public"
        if not endpoint_id:
            raise ModelConfigurationError(
                "SPECTRAIL_LLM_ENDPOINT_ID is required when using a custom base URL"
            )

        return {
            "api_key": api_key,
            "model_name": model_name,
            "base_url": base_url,
            "endpoint_id": endpoint_id,
            "timeout_seconds": timeout_seconds or 60.0,
            "tls_verify": not insecure,
        }

    def _complete(self, *, body: dict[str, Any], config: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        encoded_body = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            config["base_url"],
            data=encoded_body,
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        context = None if config["tls_verify"] else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(request, timeout=config["timeout_seconds"], context=context) as response:
                provider_payload = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ModelProviderError(
                f"provider request timed out after {config['timeout_seconds']} seconds"
            ) from exc
        except urllib.error.HTTPError as exc:
            raise ModelProviderError(f"provider request failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(f"provider request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ModelProviderError("provider response was not JSON") from exc

        try:
            content = provider_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelProviderError("provider response did not contain choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelProviderError("provider response content was empty")
        usage = provider_payload.get("usage")
        return content, usage if isinstance(usage, dict) else None


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    return values
