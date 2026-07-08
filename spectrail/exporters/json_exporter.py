from __future__ import annotations

from typing import Any

from spectrail.core.io import write_json


def export_json(path: str, data: Any) -> None:
    write_json(path, data)
