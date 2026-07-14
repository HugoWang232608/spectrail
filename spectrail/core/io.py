from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from spectrail.core.models import ReqIRPackage


REQIR_SCHEMA_VERSION = "reqir_v4"


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


def read_reqir_package(path: str | Path) -> ReqIRPackage:
    from spectrail.core.models import ReqIRPackage

    payload = read_json(path)
    if isinstance(payload, dict):
        if "items" not in payload:
            raise ValueError("ReqIR package must contain items")
        schema_version = payload.get("schema_version")
        if schema_version not in {
            None,
            "reqir_v1",
            "reqir_v2",
            "reqir_v3",
            REQIR_SCHEMA_VERSION,
        }:
            raise ValueError(f"unsupported ReqIR schema version: {schema_version}")
        items = payload["items"]
        if schema_version in {None, "reqir_v1", "reqir_v2", "reqir_v3"}:
            _validate_legacy_reqir_items(items, schema_version=schema_version)
    elif isinstance(payload, list):
        items = payload
        _validate_legacy_reqir_items(items, schema_version=None)
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


def _validate_legacy_reqir_items(
    items: object,
    *,
    schema_version: str | None,
) -> None:
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
                schema_version == "reqir_v3"
                and "source_evidence_key" in source
                and source["source_evidence_key"] is not None
            ):
                raise ValueError(
                    "REQIR_V3_SOURCE_KEYS_REQUIRE_QUOTE_MATCH_REBUILD"
                )
            if schema_version == "reqir_v3" and isinstance(table_locator, dict):
                canonical_cell_ids = source.get("canonical_source_cell_ids")
                source_row = source.get("source_table_row_index")
                if (
                    not isinstance(canonical_cell_ids, list)
                    or not canonical_cell_ids
                    or table_locator.get("cell_ids") != canonical_cell_ids
                    or table_locator.get("selected_row_index") != source_row
                ):
                    raise ValueError(
                        "REQIR_V3_TABLE_SOURCE_REQUIRES_REENRICHMENT"
                    )
            if (
                isinstance(table_locator, dict)
                and "selected_row_index" not in table_locator
            ):
                raise ValueError(
                    "REQIR_V1_TABLE_LOCATOR_REQUIRES_REENRICHMENT"
                )
            has_cell_identity = bool(
                source.get("source_cell_ids_raw")
                or source.get("canonical_source_cell_ids")
                or table_locator
            )
            if has_cell_identity and "source_table_row_index" not in source:
                code = (
                    "REQIR_V2_TABLE_SOURCE_REQUIRES_REENRICHMENT"
                    if schema_version == "reqir_v2"
                    else "REQIR_LEGACY_TABLE_SOURCE_REQUIRES_REENRICHMENT"
                )
                raise ValueError(code)
