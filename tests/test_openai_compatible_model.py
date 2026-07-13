from pathlib import Path

import pytest

from spectrail.llm.base import ModelRequest
from spectrail.llm.errors import ModelConfigurationError, ModelProviderError
from spectrail.llm.openai_compatible import OpenAICompatibleModel
from spectrail.llm.request_profile import ModelRequestProfile
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
    monkeypatch.delenv("SPECTRAIL_LLM_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("SPECTRAIL_LLM_TIMEOUT_SECONDS", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SPECTRAIL_LLM_API_KEY=test-key",
                "SPECTRAIL_LLM_MODEL=test-model",
                "SPECTRAIL_LLM_BASE_URL=https://example.test/v1/chat/completions",
                "SPECTRAIL_LLM_ENDPOINT_ID=example-compatible",
                "SPECTRAIL_LLM_TIMEOUT_SECONDS=12",
            ]
        ),
        encoding="utf-8",
    )

    config = OpenAICompatibleModel()._load_config()

    assert config["api_key"] == "test-key"
    assert config["model_name"] == "test-model"
    assert config["base_url"] == "https://example.test/v1/chat/completions"
    assert config["endpoint_id"] == "example-compatible"
    assert config["timeout_seconds"] == 12


def test_openai_compatible_model_can_disable_tls_verification(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPECTRAIL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "test-model")

    config = OpenAICompatibleModel()._load_config(insecure=True)

    assert config["tls_verify"] is False


def test_custom_base_url_requires_logical_endpoint_identity(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPECTRAIL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "test-model")
    monkeypatch.setenv("SPECTRAIL_LLM_BASE_URL", "https://internal.example/v1/chat/completions")
    monkeypatch.delenv("SPECTRAIL_LLM_ENDPOINT_ID", raising=False)

    with pytest.raises(ModelConfigurationError, match="SPECTRAIL_LLM_ENDPOINT_ID"):
        OpenAICompatibleModel()._load_config()


def test_openai_compatible_model_wraps_timeout(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)

    with pytest.raises(ModelProviderError, match="timed out after 3"):
        OpenAICompatibleModel(
            api_key="test-key",
            model_name="test-model",
            endpoint_id="timeout-test",
            timeout_seconds=3,
        ).generate(_request())


def test_live_explicit_profile_must_match_transport_identity():
    model = OpenAICompatibleModel(
        api_key="test-key",
        model_name="transport-model",
        base_url="https://transport.test/v1/chat/completions",
        endpoint_id="transport-endpoint",
    )
    matching = ModelRequestProfile(
        provider_adapter="openai_compatible_v1",
        provider_endpoint_id="transport-endpoint",
        model_name="transport-model",
        temperature=0.2,
    )
    assert model.resolve_request_profile(matching) is matching

    with pytest.raises(ModelConfigurationError, match="LIVE_REQUEST_PROFILE_MISMATCH"):
        model.resolve_request_profile(
            ModelRequestProfile(
                provider_adapter="openai_compatible_v1",
                provider_endpoint_id="different-endpoint",
                model_name="transport-model",
            )
        )


def test_live_transport_is_frozen_after_first_resolution(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SPECTRAIL_LLM_API_KEY", "first-key")
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "first-model")
    monkeypatch.setenv("SPECTRAIL_LLM_BASE_URL", "https://first.test/v1/chat/completions")
    monkeypatch.setenv("SPECTRAIL_LLM_ENDPOINT_ID", "first-endpoint")
    model = OpenAICompatibleModel()
    first = model.resolve_transport()

    monkeypatch.setenv("SPECTRAIL_LLM_API_KEY", "second-key")
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "second-model")
    monkeypatch.setenv("SPECTRAIL_LLM_BASE_URL", "https://second.test/v1/chat/completions")
    monkeypatch.setenv("SPECTRAIL_LLM_ENDPOINT_ID", "second-endpoint")
    second = model.resolve_transport()

    assert second is first
    assert second["api_key"] == "first-key"
    assert second["model_name"] == "first-model"
    assert second["endpoint_id"] == "first-endpoint"


def test_live_response_uses_request_prompt_version(monkeypatch):
    model = OpenAICompatibleModel(
        api_key="test-key",
        model_name="test-model",
        endpoint_id="test-endpoint",
    )
    monkeypatch.setattr(
        model,
        "_complete",
        lambda *, body, config: ('{"items": []}', None),
    )
    request = _request()
    request.metadata["prompt_version"] = "reqir_extraction_v2_chunked"
    response = model.generate(request)
    assert response.metadata["prompt_version"] == "reqir_extraction_v2_chunked"
    assert response.model_name == "test-model"


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
