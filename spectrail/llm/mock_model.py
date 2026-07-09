from __future__ import annotations

from pathlib import Path

from spectrail.core.io import read_json
from spectrail.llm.base import ModelRequest, ModelResponse


class MockModel:
    model_mode = "mock"

    def __init__(self, fixture_path: str | Path = "fixtures/mock_reqir_response.json") -> None:
        self.fixture_path = Path(fixture_path)

    def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(
            payload=read_json(self.fixture_path),
            model_mode=self.model_mode,
            model_name="mock-fixture",
            metadata={"fixture_path": self.fixture_path.as_posix()},
        )
