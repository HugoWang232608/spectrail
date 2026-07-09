from spectrail.llm.errors import ModelConfigurationError
from spectrail.llm.factory import create_model_client
from spectrail.llm.mock_model import MockModel
from spectrail.llm.openai_compatible import OpenAICompatibleModel
from spectrail.llm.recorded_model import RecordedModel


def test_create_model_client_returns_mock_model():
    client = create_model_client(model_mode="mock")

    assert isinstance(client, MockModel)


def test_create_model_client_returns_recorded_model():
    client = create_model_client(model_mode="recorded")

    assert isinstance(client, RecordedModel)


def test_create_model_client_returns_live_model_without_config_validation():
    client = create_model_client(model_mode="live", model_name="test-model")

    assert isinstance(client, OpenAICompatibleModel)
    assert client.model_name == "test-model"


def test_create_model_client_rejects_unknown_mode():
    try:
        create_model_client(model_mode="unknown")
    except ModelConfigurationError as exc:
        assert "unsupported model mode" in str(exc)
    else:
        raise AssertionError("expected unsupported mode to fail")
