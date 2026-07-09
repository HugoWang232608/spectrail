import pytest

from spectrail.llm.base import ModelRequest
from spectrail.llm.errors import ModelConfigurationError
from spectrail.llm.openai_compatible import OpenAICompatibleModel
from spectrail.parsers.markdown_parser import MarkdownParser


def test_openai_compatible_model_requires_api_key(monkeypatch):
    monkeypatch.delenv("SPECTRAIL_LLM_API_KEY", raising=False)
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "test-model")

    with pytest.raises(ModelConfigurationError, match="SPECTRAIL_LLM_API_KEY"):
        OpenAICompatibleModel().generate(_request())


def test_openai_compatible_model_requires_model(monkeypatch):
    monkeypatch.setenv("SPECTRAIL_LLM_API_KEY", "test-key")
    monkeypatch.delenv("SPECTRAIL_LLM_MODEL", raising=False)

    with pytest.raises(ModelConfigurationError, match="SPECTRAIL_LLM_MODEL"):
        OpenAICompatibleModel().generate(_request())


def _request() -> ModelRequest:
    blocks = MarkdownParser().parse_file("docs/sample_srs.md")
    return ModelRequest(
        document_text="",
        blocks=blocks,
        document_name="sample_srs.md",
        source_format="markdown",
        parser_name="markdown_parser_v1",
        model_mode="live",
    )
