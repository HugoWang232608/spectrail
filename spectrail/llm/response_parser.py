from __future__ import annotations

import json
import re
from typing import Any

from spectrail.llm.errors import ModelResponseParseError


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_model_response(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if not text:
        raise ModelResponseParseError("model response is empty")

    for candidate in _candidate_json_strings(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            raise ModelResponseParseError("model response JSON must be an object")
        return parsed

    raise ModelResponseParseError("model response did not contain parseable JSON")


def _candidate_json_strings(text: str) -> list[str]:
    candidates = [text]

    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])

    return candidates
