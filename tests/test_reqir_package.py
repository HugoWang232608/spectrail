from pathlib import Path

import pytest

from spectrail.core.io import write_json
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
