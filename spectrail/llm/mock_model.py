from __future__ import annotations

from pathlib import Path

from spectrail.core.io import read_json
from spectrail.llm.base import ModelRequest, ModelResponse
from spectrail.llm.prompt_builder import CHUNKED_PROMPT_VERSION, PROMPT_VERSION, build_reqir_prompt


class MockModel:
    model_mode = "mock"

    def __init__(self, fixture_path: str | Path = "fixtures/mock_reqir_response.json") -> None:
        self.fixture_path = Path(fixture_path)

    def generate(self, request: ModelRequest) -> ModelResponse:
        payload = read_json(self.fixture_path)
        filtered = False
        if request.metadata.get("chunked"):
            block_ids = {block.block_id for block in request.blocks}
            payload = {
                **payload,
                "items": [
                    item for item in payload.get("items", []) if item.get("source_block_id") in block_ids
                ],
            }
            filtered = True
        prompt = build_reqir_prompt(request)
        return ModelResponse(
            payload=payload,
            model_mode=self.model_mode,
            model_name="mock-fixture",
            prompt=prompt,
            metadata={
                "fixture_path": self.fixture_path.as_posix(),
                "fixture_filter_applied": filtered,
                "prompt_version": CHUNKED_PROMPT_VERSION if request.metadata.get("chunked") else PROMPT_VERSION,
            },
        )
