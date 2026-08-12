from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from spectrail.llm.openai_compatible_transport import OpenAICompatibleTransport
from spectrail.llm.request_profile import ModelRequestProfile
from spectrail.llm.transport import CompletionRequest


def test_openai_transport_returns_raw_completion_and_sanitized_metadata(monkeypatch):
    transport = OpenAICompatibleTransport(
        api_key="test-key",
        model_name="test-model",
        endpoint_id="test-endpoint",
    )
    monkeypatch.setattr(
        transport,
        "_complete",
        lambda *, body, config: ('{"action":"finish"}', {"total_tokens": 5}),
    )
    request = CompletionRequest(
        prompt="planner prompt",
        request_profile=ModelRequestProfile(
            provider_adapter="openai_compatible_v1",
            provider_endpoint_id="test-endpoint",
            model_name="test-model",
            response_format={"type": "json_object"},
        ),
        metadata={"prompt_version": "agent_planner_v1"},
    )

    response = transport.complete(request)

    assert response.raw_text == '{"action":"finish"}'
    assert response.model_name == "test-model"
    assert response.usage == {"total_tokens": 5}
    assert response.metadata == {
        "provider_endpoint_id": "test-endpoint",
        "prompt_version": "agent_planner_v1",
        "tls_verify": True,
    }
    assert "test-key" not in repr(response)


def test_completion_contracts_are_frozen():
    request = CompletionRequest(
        prompt="prompt",
        request_profile=ModelRequestProfile(
            provider_adapter="openai_compatible_v1",
            provider_endpoint_id="test-endpoint",
            model_name="test-model",
        ),
        metadata={},
    )

    with pytest.raises(FrozenInstanceError):
        request.prompt = "changed"  # type: ignore[misc]
