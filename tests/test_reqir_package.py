from pathlib import Path
import hashlib
import json

import pytest

from spectrail.core.io import read_reqir_package, write_json
from spectrail.core.models import SourceSpan
from spectrail.evidence import TableLocator, validate_source_evidence_keys
from spectrail.review.service import load_requirements


def test_legacy_reqir_without_table_locator_remains_loadable(tmp_path: Path):
    path = tmp_path / "legacy.json"
    write_json(
        path,
        {
            "items": [
                {
                    "id": "REQ-1",
                    "statement": "The system shall log events.",
                    "sources": [],
                }
            ]
        },
    )

    assert load_requirements(path)[0].id == "REQ-1"


def test_legacy_table_locator_requires_evidence_reenrichment(tmp_path: Path):
    path = tmp_path / "legacy-table.json"
    write_json(
        path,
        {
            "schema_version": "reqir_v1",
            "items": [
                {
                    "id": "REQ-1",
                    "statement": "A maps to B.",
                    "sources": [
                        {
                            "table_locator": {
                                "table_id": "tbl_00000001",
                                "cell_ids": ["cell_00000001_r0001_c0001"],
                                "row_indices": [1],
                                "column_indices": [1],
                            }
                        }
                    ],
                }
            ],
        },
    )

    with pytest.raises(
        ValueError,
        match="REQIR_V1_TABLE_LOCATOR_REQUIRES_REENRICHMENT",
    ):
        load_requirements(path)


def test_unknown_reqir_schema_version_is_rejected(tmp_path: Path):
    path = tmp_path / "future.json"
    write_json(path, {"schema_version": "reqir_v99", "items": []})

    with pytest.raises(ValueError, match="unsupported ReqIR schema version"):
        load_requirements(path)


def test_reqir_v2_table_source_requires_row_identity_reenrichment(tmp_path: Path):
    path = tmp_path / "reqir-v2-table.json"
    write_json(
        path,
        {
            "schema_version": "reqir_v2",
            "items": [
                {
                    "id": "REQ-1",
                    "statement": "Merged applies.",
                    "sources": [
                        {
                            "document_id": "doc_001",
                            "block_id": "blk_0001",
                            "quote": "Merged",
                            "canonical_source_cell_ids": [
                                "cell_00000001_r0001_c0001"
                            ],
                        }
                    ],
                }
            ],
        },
    )

    with pytest.raises(
        ValueError,
        match="REQIR_V2_TABLE_SOURCE_REQUIRES_REENRICHMENT",
    ):
        read_reqir_package(path)


def test_reqir_v2_text_source_is_upgraded_to_v4(tmp_path: Path):
    path = tmp_path / "reqir-v2-text.json"
    write_json(
        path,
        {
            "schema_version": "reqir_v2",
            "metadata": {"document": "sample.md"},
            "items": [
                {
                    "id": "REQ-1",
                    "statement": "The system shall log events.",
                    "sources": [],
                }
            ],
        },
    )

    package = read_reqir_package(path)
    assert package.schema_version == "reqir_v4"
    assert package.metadata == {"document": "sample.md"}


def test_reqir_v2_text_source_preserves_legacy_source_evidence_key(tmp_path: Path):
    path = tmp_path / "reqir-v2-keyed-text.json"
    fingerprint = "a" * 64
    legacy_payload = {
        "evidence_fingerprint": fingerprint,
        "document_id": "doc_001",
        "block_id": "blk_0001",
        "quote": "The system shall log events.",
        "canonical_cell_ids": [],
    }
    encoded = json.dumps(
        legacy_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    legacy_key = f"src_{hashlib.sha256(encoded).hexdigest()[:24]}"
    write_json(
        path,
        {
            "schema_version": "reqir_v2",
            "items": [
                {
                    "id": "REQ-1",
                    "statement": "The system shall log events.",
                    "sources": [
                        {
                            "document_id": "doc_001",
                            "block_id": "blk_0001",
                            "quote": "The system shall log events.",
                            "source_evidence_key": legacy_key,
                        }
                    ],
                }
            ],
        },
    )

    package = read_reqir_package(path)
    validate_source_evidence_keys(
        package.items,
        evidence_fingerprint=fingerprint,
    )
    assert package.items[0].sources[0].source_evidence_key == legacy_key


def test_reqir_v3_key_with_null_table_row_requires_registry_rebuild(
    tmp_path: Path,
):
    path = tmp_path / "reqir-v3-keyed-text.json"
    fingerprint = "a" * 64
    transient_v3_payload = {
        "evidence_fingerprint": fingerprint,
        "document_id": "doc_001",
        "block_id": "blk_0001",
        "quote": "The system shall log events.",
        "canonical_cell_ids": [],
        "source_table_row_index": None,
    }
    encoded = json.dumps(
        transient_v3_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    transient_v3_key = f"src_{hashlib.sha256(encoded).hexdigest()[:24]}"
    write_json(
        path,
        {
            "schema_version": "reqir_v3",
            "items": [
                {
                    "id": "REQ-1",
                    "statement": "The system shall log events.",
                    "sources": [
                        {
                            "document_id": "doc_001",
                            "block_id": "blk_0001",
                            "quote": "The system shall log events.",
                            "source_evidence_key": transient_v3_key,
                        }
                    ],
                }
            ],
        },
    )

    with pytest.raises(
        ValueError,
        match="REQIR_V3_SOURCE_KEYS_REQUIRE_QUOTE_MATCH_REBUILD",
    ):
        read_reqir_package(path)


def test_reqir_v3_empty_source_key_requires_registry_rebuild(tmp_path: Path):
    path = tmp_path / "reqir-v3-empty-key.json"
    write_json(
        path,
        {
            "schema_version": "reqir_v3",
            "items": [
                {
                    "id": "REQ-1",
                    "statement": "The system shall log events.",
                    "sources": [
                        {
                            "document_id": "doc_001",
                            "block_id": "blk_0001",
                            "quote": "The system shall log events.",
                            "source_evidence_key": "",
                        }
                    ],
                }
            ],
        },
    )

    with pytest.raises(
        ValueError,
        match="REQIR_V3_SOURCE_KEYS_REQUIRE_QUOTE_MATCH_REBUILD",
    ):
        read_reqir_package(path)


def test_source_evidence_key_must_use_canonical_format():
    with pytest.raises(ValueError, match="must match"):
        SourceSpan(
            document_id="doc_001",
            block_id="blk_0001",
            quote="quote",
            source_evidence_key="",
        )


def test_reqir_v3_incomplete_table_locator_requires_reenrichment(tmp_path: Path):
    path = tmp_path / "reqir-v3-incomplete-table.json"
    write_json(
        path,
        {
            "schema_version": "reqir_v3",
            "items": [
                {
                    "id": "REQ-1",
                    "statement": "A maps to B.",
                    "sources": [
                        {
                            "document_id": "doc_001",
                            "block_id": "blk_0001",
                            "quote": "A",
                            "table_locator": {
                                "table_id": "tbl_00000001",
                                "cell_ids": ["cell_00000001_r0001_c0001"],
                                "row_indices": [1],
                                "selected_row_index": 1,
                                "column_indices": [1],
                            },
                        }
                    ],
                }
            ],
        },
    )

    with pytest.raises(
        ValueError,
        match="REQIR_V3_TABLE_SOURCE_REQUIRES_REENRICHMENT",
    ):
        read_reqir_package(path)


def test_table_source_identity_requires_cell_ids_and_physical_row_together():
    with pytest.raises(ValueError, match="require source_table_row_index"):
        SourceSpan(
            document_id="doc_001",
            block_id="blk_0001",
            quote="Merged",
            source_cell_ids_raw=["cell_00000001_r0001_c0001"],
        )
    with pytest.raises(ValueError, match="requires table source cell IDs"):
        SourceSpan(
            document_id="doc_001",
            block_id="blk_0001",
            quote="Merged",
            source_table_row_index=1,
        )


def test_table_locator_requires_complete_canonical_source_identity():
    locator = TableLocator(
        table_id="tbl_00000001",
        cell_ids=["cell_00000001_r0001_c0001"],
        row_indices=[1],
        selected_row_index=1,
        column_indices=[1],
    )
    with pytest.raises(ValueError, match="canonical_source_cell_ids"):
        SourceSpan(
            document_id="doc_001",
            block_id="blk_0001",
            quote="A",
            table_locator=locator,
        )
    with pytest.raises(ValueError, match="cell IDs do not match"):
        SourceSpan(
            document_id="doc_001",
            block_id="blk_0001",
            quote="A",
            canonical_source_cell_ids=["cell_00000001_r0001_c0002"],
            source_table_row_index=1,
            table_locator=locator,
        )
