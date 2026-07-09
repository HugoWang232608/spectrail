from __future__ import annotations

from pathlib import Path
from typing import Any

from spectrail.core.io import read_json
from spectrail.llm.base import ModelRequest, ModelResponse
from spectrail.llm.errors import ModelConfigurationError
from spectrail.llm.response_parser import parse_model_response


class RecordedModel:
    model_mode = "recorded"

    def __init__(self, fixture_path: str | Path = "fixtures/recorded/sample_srs_reqir_response.json") -> None:
        self.fixture_path = Path(fixture_path)

    def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        if not self.fixture_path.exists():
            raise ModelConfigurationError(f"recorded fixture not found: {self.fixture_path}")

        fixture = read_json(self.fixture_path)
        if not isinstance(fixture, dict):
            raise ModelConfigurationError("recorded fixture must be a JSON object")

        metadata = _metadata_from_fixture(fixture)
        raw_text = fixture.get("raw_text")
        if isinstance(raw_text, str) and raw_text.strip():
            payload = parse_model_response(raw_text)
        else:
            payload = _payload_from_fixture(fixture)

        return ModelResponse(
            payload=payload,
            model_mode=self.model_mode,
            model_name=metadata.get("model_name", "recorded-fixture"),
            raw_text=raw_text if isinstance(raw_text, str) else None,
            metadata={
                **metadata,
                "fixture_path": self.fixture_path.as_posix(),
            },
        )


def _metadata_from_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    metadata = fixture.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _payload_from_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    payload = fixture.get("payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(fixture.get("items"), list):
        return fixture
    raise ModelConfigurationError("recorded fixture must contain raw_text, payload, or top-level items")
