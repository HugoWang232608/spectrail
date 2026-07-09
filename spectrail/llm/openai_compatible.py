from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from spectrail.llm.base import ModelRequest, ModelResponse
from spectrail.llm.errors import ModelConfigurationError, ModelProviderError
from spectrail.llm.prompt_builder import PROMPT_VERSION, build_reqir_prompt
from spectrail.llm.response_parser import parse_model_response


DEFAULT_BASE_URL = "https://api.openai.com/v1/chat/completions"


class OpenAICompatibleModel:
    model_mode = "live"

    def __init__(
        self,
        model_name: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def generate(self, request: ModelRequest) -> ModelResponse:
        config = self._load_config()
        prompt = build_reqir_prompt(request)
        raw_text = self._complete(prompt=prompt, config=config)
        payload = parse_model_response(raw_text)
        return ModelResponse(
            payload=payload,
            model_mode=self.model_mode,
            model_name=config["model_name"],
            raw_text=raw_text,
            prompt=prompt,
            metadata={
                "base_url": config["base_url"],
                "model_name": config["model_name"],
                "prompt_version": PROMPT_VERSION,
            },
        )

    def _load_config(self) -> dict[str, Any]:
        api_key = self.api_key or os.environ.get("SPECTRAIL_LLM_API_KEY")
        if not api_key:
            raise ModelConfigurationError("SPECTRAIL_LLM_API_KEY is required for live mode")

        model_name = self.model_name or os.environ.get("SPECTRAIL_LLM_MODEL")
        if not model_name:
            raise ModelConfigurationError("SPECTRAIL_LLM_MODEL is required for live mode")

        timeout_raw = os.environ.get("SPECTRAIL_LLM_TIMEOUT_SECONDS")
        timeout_seconds = self.timeout_seconds
        if timeout_seconds is None and timeout_raw:
            try:
                timeout_seconds = float(timeout_raw)
            except ValueError as exc:
                raise ModelConfigurationError("SPECTRAIL_LLM_TIMEOUT_SECONDS must be a number") from exc

        return {
            "api_key": api_key,
            "model_name": model_name,
            "base_url": self.base_url or os.environ.get("SPECTRAIL_LLM_BASE_URL") or DEFAULT_BASE_URL,
            "timeout_seconds": timeout_seconds or 60.0,
        }

    def _complete(self, *, prompt: str, config: dict[str, Any]) -> str:
        body = json.dumps(
            {
                "model": config["model_name"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            config["base_url"],
            data=body,
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=config["timeout_seconds"]) as response:
                provider_payload = json.loads(response.read().decode("utf-8"))
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
        return content
