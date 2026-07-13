import pytest

from spectrail.llm.fingerprints import build_request_identity
from spectrail.llm.request_profile import ModelRequestProfile, OPENAI_COMPATIBLE_ADAPTER


def test_request_fingerprint_uses_allowlisted_safe_options():
    profile = ModelRequestProfile(
        provider_adapter="openai_compatible_v1",
        provider_endpoint_id="local-test",
        model_name="test-model",
        safe_request_options={"seed": 7, "top_p": 0.9},
    )
    fingerprint, body = build_request_identity("final prompt", profile)
    assert len(fingerprint) == 64
    assert body["seed"] == 7
    assert body["top_p"] == 0.9


@pytest.mark.parametrize("key", ["model", "messages", "temperature", "Authorization", "api_key"])
def test_request_profile_rejects_core_overrides_and_secret_options(key: str):
    with pytest.raises(ValueError, match="unsupported safe_request_options"):
        ModelRequestProfile(
            provider_adapter="openai_compatible_v1",
            provider_endpoint_id="local-test",
            model_name="test-model",
            safe_request_options={key: "unsafe"},
        )


def test_request_sanitizer_removes_nested_secret_like_keys():
    sanitized = OPENAI_COMPATIBLE_ADAPTER.sanitize_request_body(
        {
            "model": "test-model",
            "metadata": {
                "authorization": "Bearer secret",
                "nested": [{"password": "secret", "value": 1}],
            },
        }
    )
    assert sanitized == {"model": "test-model", "metadata": {"nested": [{"value": 1}]}}


def test_request_profile_rejects_url_as_endpoint_identity():
    with pytest.raises(ValueError, match="must not contain a URL"):
        ModelRequestProfile(
            provider_adapter="openai_compatible_v1",
            provider_endpoint_id="https://internal.example/v1",
            model_name="test-model",
        )
