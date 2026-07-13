from spectrail.llm.fingerprints import build_request_identity
from spectrail.llm.request_profile import ModelRequestProfile


def test_request_fingerprint_uses_sanitized_body_and_excludes_credentials():
    profile = ModelRequestProfile(
        provider_adapter="openai_compatible_v1",
        provider_endpoint_id="local-test",
        model_name="test-model",
        safe_request_options={"Authorization": "Bearer secret", "api_key": "secret", "seed": 7},
    )
    fingerprint, body = build_request_identity("final prompt", profile)
    assert len(fingerprint) == 64
    assert "Authorization" not in body
    assert "api_key" not in body
    assert body["seed"] == 7
