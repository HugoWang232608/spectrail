from pathlib import Path

import pytest

from spectrail.llm.base import ModelRequest
from spectrail.llm.errors import ModelConfigurationError
from spectrail.llm.recorded_model import RecordedModel
from spectrail.parsers.markdown_parser import MarkdownParser


def test_recorded_model_reads_raw_text_fixture():
    response = RecordedModel("fixtures/recorded/sample_srs_reqir_response.json").generate(_request())

    assert response.model_mode == "recorded"
    assert response.model_name == "recorded-sample-fixture"
    assert response.raw_text
    assert len(response.payload["items"]) == 2
    assert response.metadata["fixture_path"] == "fixtures/recorded/sample_srs_reqir_response.json"


def test_recorded_model_reads_payload_fixture(tmp_path: Path):
    fixture = tmp_path / "recorded.json"
    fixture.write_text(
        '{"metadata":{"model_name":"payload-fixture"},"payload":{"items":[]}}',
        encoding="utf-8",
    )

    response = RecordedModel(fixture).generate(_request())

    assert response.model_name == "payload-fixture"
    assert response.payload == {"items": []}


def test_recorded_model_rejects_missing_fixture(tmp_path: Path):
    with pytest.raises(ModelConfigurationError):
        RecordedModel(tmp_path / "missing.json").generate(_request())


def _request() -> ModelRequest:
    blocks = MarkdownParser().parse_file("docs/sample_srs.md")
    return ModelRequest(
        document_text="",
        blocks=blocks,
        document_name="sample_srs.md",
        source_format="markdown",
        parser_name="markdown_parser_v1",
        model_mode="recorded",
    )
