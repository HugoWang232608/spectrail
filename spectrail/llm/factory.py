from __future__ import annotations

from pathlib import Path

from spectrail.llm.base import ModelClient
from spectrail.llm.errors import ModelConfigurationError
from spectrail.llm.mock_model import MockModel
from spectrail.llm.openai_compatible import OpenAICompatibleModel
from spectrail.llm.recorded_model import RecordedModel


def create_model_client(
    *,
    model_mode: str,
    model_name: str | None = None,
    recorded_fixture: str | Path | None = None,
) -> ModelClient:
    if model_mode == "mock":
        return MockModel()
    if model_mode == "recorded":
        return RecordedModel(recorded_fixture or "fixtures/recorded/sample_srs_reqir_response.json")
    if model_mode == "live":
        return OpenAICompatibleModel(model_name=model_name)
    raise ModelConfigurationError(f"unsupported model mode: {model_mode}")
