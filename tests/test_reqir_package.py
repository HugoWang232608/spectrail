from pathlib import Path

import pytest

from spectrail.core.io import read_reqir_package, write_json
from spectrail.core.models import SourceSpan
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


def test_reqir_v2_text_source_is_upgraded_to_v3(tmp_path: Path):
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
    assert package.schema_version == "reqir_v3"
    assert package.metadata == {"document": "sample.md"}


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
