from pathlib import Path

import pytest

from spectrail.llm.base import ModelRequest
from spectrail.llm.errors import ModelConfigurationError
from spectrail.llm.openai_compatible import OpenAICompatibleModel
from spectrail.parsers.markdown_parser import MarkdownParser


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_openai_compatible_model_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPECTRAIL_LLM_API_KEY", raising=False)
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "test-model")

    with pytest.raises(ModelConfigurationError, match="SPECTRAIL_LLM_API_KEY"):
        OpenAICompatibleModel().generate(_request())


def test_openai_compatible_model_requires_model(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPECTRAIL_LLM_API_KEY", "test-key")
    monkeypatch.delenv("SPECTRAIL_LLM_MODEL", raising=False)

    with pytest.raises(ModelConfigurationError, match="SPECTRAIL_LLM_MODEL"):
        OpenAICompatibleModel().generate(_request())


def test_openai_compatible_model_reads_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPECTRAIL_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SPECTRAIL_LLM_MODEL", raising=False)
    monkeypatch.delenv("SPECTRAIL_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SPECTRAIL_LLM_TIMEOUT_SECONDS", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SPECTRAIL_LLM_API_KEY=test-key",
                "SPECTRAIL_LLM_MODEL=test-model",
                "SPECTRAIL_LLM_BASE_URL=https://example.test/v1/chat/completions",
                "SPECTRAIL_LLM_TIMEOUT_SECONDS=12",
            ]
        ),
        encoding="utf-8",
    )

    config = OpenAICompatibleModel()._load_config()

    assert config["api_key"] == "test-key"
    assert config["model_name"] == "test-model"
    assert config["base_url"] == "https://example.test/v1/chat/completions"
    assert config["timeout_seconds"] == 12


def test_openai_compatible_model_can_disable_tls_verification(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPECTRAIL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "test-model")

    config = OpenAICompatibleModel()._load_config(insecure=True)

    assert config["tls_verify"] is False


def _request() -> ModelRequest:
    blocks = MarkdownParser().parse_file(REPO_ROOT / "docs/sample_srs.md")
    return ModelRequest(
        document_text="",
        blocks=blocks,
        document_name="sample_srs.md",
        source_format="markdown",
        parser_name="markdown_parser_v1",
        model_mode="live",
    )
