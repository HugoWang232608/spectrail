from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from spectrail.evidence.models import EvidenceIndex


FLOAT_PRECISION = 4


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_evidence_payload(index: EvidenceIndex) -> dict[str, Any]:
    payload = index.model_dump(mode="json", exclude={"evidence_fingerprint"})
    return _canonicalize(payload)


def build_evidence_fingerprint(index: EvidenceIndex) -> str:
    payload = canonical_evidence_payload(index)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def finalize_evidence_fingerprint(index: EvidenceIndex) -> EvidenceIndex:
    return index.model_copy(update={"evidence_fingerprint": build_evidence_fingerprint(index)})


def _canonicalize(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("evidence fingerprint does not support non-finite floats")
        rounded = round(value, FLOAT_PRECISION)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, dict):
        return {key: _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value
