from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQIR_SCHEMA_VERSION = "reqir_v2"


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def model_list_dump(items: list[Any]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]


def reqir_package_dump(
    items: list[Any],
    *,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": REQIR_SCHEMA_VERSION,
        "metadata": metadata,
        "items": model_list_dump(items),
    }


def read_reqir_items(path: str | Path) -> list[Any]:
    return read_reqir_package(path).items


def read_reqir_package(path: str | Path):
    from spectrail.core.models import ReqIRPackage

    payload = read_json(path)
    if isinstance(payload, dict):
        if "items" not in payload:
            raise ValueError("ReqIR package must contain items")
        schema_version = payload.get("schema_version")
        if schema_version not in {None, "reqir_v1", REQIR_SCHEMA_VERSION}:
            raise ValueError(f"unsupported ReqIR schema version: {schema_version}")
        items = payload["items"]
        if schema_version in {None, "reqir_v1"}:
            _validate_legacy_reqir_items(items)
    elif isinstance(payload, list):
        items = payload
        _validate_legacy_reqir_items(items)
    else:
        raise ValueError("ReqIR payload must be a package object or item list")
    if not isinstance(items, list):
        raise ValueError("ReqIR package items must be a list")
    return ReqIRPackage.model_validate(
        {
            "schema_version": REQIR_SCHEMA_VERSION,
            "metadata": payload.get("metadata", {}) if isinstance(payload, dict) else {},
            "items": items,
        }
    )


def _validate_legacy_reqir_items(items: object) -> None:
    if not isinstance(items, list):
        raise ValueError("ReqIR package items must be a list")
    for item in items:
        if not isinstance(item, dict):
            continue
        for source in item.get("sources", []):
            if not isinstance(source, dict):
                continue
            table_locator = source.get("table_locator")
            if (
                isinstance(table_locator, dict)
                and "selected_row_index" not in table_locator
            ):
                raise ValueError(
                    "REQIR_V1_TABLE_LOCATOR_REQUIRES_REENRICHMENT"
                )
