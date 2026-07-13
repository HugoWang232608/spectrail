from __future__ import annotations

import hashlib
import json
from typing import Any

from spectrail.llm.request_profile import ModelRequestProfile, adapter_for_profile


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_request_identity(prompt: str, profile: ModelRequestProfile) -> tuple[str, dict[str, Any]]:
    adapter = adapter_for_profile(profile)
    body = adapter.build_provider_request_body(prompt=prompt, profile=profile)
    sanitized = adapter.sanitize_request_body(body)
    fingerprint = sha256_hex(
        {
            "provider_adapter": profile.provider_adapter,
            "provider_endpoint_id": profile.provider_endpoint_id,
            "request_body": sanitized,
        }
    )
    return fingerprint, sanitized
