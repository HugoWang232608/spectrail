from __future__ import annotations

from pathlib import Path
from typing import Any

from spectrail.core.io import read_json
from spectrail.llm.base import ModelRequest, ModelResponse
from spectrail.llm.errors import ModelConfigurationError
from spectrail.llm.prompt_builder import CHUNKED_PROMPT_VERSION, PROMPT_VERSION, build_reqir_prompt
from spectrail.llm.response_parser import parse_model_response


class RecordedModel:
    model_mode = "recorded"

    def __init__(self, fixture_path: str | Path = "fixtures/recorded/sample_srs_reqir_response.json") -> None:
        self.fixture_path = Path(fixture_path)

    def generate(self, request: ModelRequest) -> ModelResponse:
        if not self.fixture_path.exists():
            raise ModelConfigurationError(f"recorded fixture not found: {self.fixture_path}")

        prompt = build_reqir_prompt(request)
        fixture_path = self.fixture_path
        if fixture_path.is_dir():
            fixture_path = self._bundle_fixture(request, fixture_path)
        elif request.metadata.get("chunk_count", 1) > 1:
            raise ModelConfigurationError("RECORDED_FIXTURE_NOT_CHUNK_AWARE")

        fixture = read_json(fixture_path)
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
            prompt=prompt,
            metadata={
                **metadata,
                "fixture_path": fixture_path.as_posix(),
                "prompt_version": request.metadata.get(
                    "prompt_version",
                    CHUNKED_PROMPT_VERSION if request.metadata.get("chunked") else PROMPT_VERSION,
                ),
            },
        )

    @staticmethod
    def _bundle_fixture(request: ModelRequest, bundle_path: Path) -> Path:
        manifest_path = bundle_path / "manifest.json"
        if not manifest_path.exists():
            raise ModelConfigurationError(f"recorded bundle manifest not found: {manifest_path}")
        manifest = read_json(manifest_path)
        bundle_profile = manifest.get("metadata", {}).get("request_profile")
        request_profile = request.request_profile.to_dict() if request.request_profile else None
        if not isinstance(bundle_profile, dict):
            raise ModelConfigurationError("RECORDED_REQUEST_PROFILE_MISSING")
        if request_profile != bundle_profile:
            raise ModelConfigurationError("RECORDED_REQUEST_PROFILE_MISMATCH")
        request_fingerprint = request.metadata.get("request_fingerprint")
        chunk_fingerprint = request.metadata.get("chunk_fingerprint")
        chunk_id = request.metadata.get("chunk_id")
        for response in manifest.get("responses", []):
            if response.get("request_fingerprint") != request_fingerprint:
                continue
            if (
                response.get("chunk_id") != chunk_id
                or response.get("chunk_fingerprint") != chunk_fingerprint
            ):
                raise ModelConfigurationError("RECORDED_CHUNK_MISMATCH")
            if response.get("block_ids") != [block.block_id for block in request.blocks]:
                raise ModelConfigurationError("RECORDED_CHUNK_MISMATCH")
            fixture_path = bundle_path / response["fixture"]
            if not fixture_path.exists():
                raise ModelConfigurationError("RECORDED_RESPONSE_NOT_FOUND")
            return fixture_path
        if any(response.get("chunk_id") == chunk_id for response in manifest.get("responses", [])):
            raise ModelConfigurationError("RECORDED_REQUEST_MISMATCH")
        raise ModelConfigurationError("RECORDED_RESPONSE_NOT_FOUND")


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
