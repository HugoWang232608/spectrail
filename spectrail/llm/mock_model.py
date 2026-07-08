from __future__ import annotations

from pathlib import Path

from spectrail.core.io import read_json


class MockModel:
    def __init__(self, fixture_path: str | Path = "fixtures/mock_reqir_response.json") -> None:
        self.fixture_path = Path(fixture_path)

    def generate(self, document_text: str = "") -> dict:
        return read_json(self.fixture_path)
