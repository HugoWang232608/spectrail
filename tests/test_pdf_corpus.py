from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from spectrail.cli import main
from spectrail.core.io import read_json
from spectrail.evaluation.pdf_corpus import (
    PDF_CORPUS_OUTPUT_MARKER_PAYLOAD,
    PdfCorpusManifest,
    PdfCorpusRunner,
)
from spectrail.evidence import sha256_file


def test_checked_pdf_corpus_seed_reports_parser_and_heading_baseline(
    tmp_path: Path,
) -> None:
    output = tmp_path / "pdf-corpus"

    assert main(
        [
            "evaluate-pdf-corpus",
            "eval/pdf_corpus_v1/manifest.json",
            "--output",
            str(output),
        ]
    ) == 0

    report = read_json(output / "pdf_corpus_report.json")
    assert report["passed"] is True
    assert report["case_count"] == 1
    assert report["metrics"]["text_source_accuracy"] == 1.0
    assert report["metrics"]["page_region_availability_rate"] == 1.0
    assert report["metrics"]["heading_precision"] == pytest.approx(0.9)
    assert report["metrics"]["heading_recall"] == 1.0
    assert report["cases"][0]["actual_parser_identity"]["parser_version"] == "2.18"
    assert (
        report["cases"][0]["actual_evidence_fingerprint"]
        == "b2793917b65cd81781a3bce68f010bbc8178fb02263615acbbd5d568d6e8ce97"
    )
    assert read_json(output / ".spectrail-pdf-corpus-output") == (
        PDF_CORPUS_OUTPUT_MARKER_PAYLOAD
    )

    observations = report["cases"][0]["observations"]
    decoration = next(
        item
        for item in observations
        if item["observation_id"] == "page-1-decoration-vs-heading"
    )
    assert decoration["gate"] is False
    assert decoration["passed"] is False


def test_pdf_corpus_runner_covers_table_fallback_and_continuation_observations(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    payload = {
        "schema_version": "pdf_corpus_v1",
        "name": "structural observation coverage",
        "thresholds": {
            "selected_table_topology_precision_min": 1.0,
            "selected_table_topology_recall_min": 1.0,
            "fallback_accuracy_min": 1.0,
            "continuation_pair_accuracy_min": 1.0,
            "continuation_false_positive_count_max": 0,
        },
        "cases": [
            _case(
                "simple-grid",
                Path("tests/fixtures/pdf_table_requirements.pdf").resolve(),
                [
                    {
                        "observation_id": "simple-grid-page",
                        "kind": "table_page",
                        "page": 1,
                        "expected_tables": [
                            {
                                "row_count": 3,
                                "column_count": 3,
                                "topology_status": "complete",
                                "continuation_role": "single",
                            }
                        ],
                    }
                ],
            ),
            _case(
                "ambiguous-grid",
                Path("tests/fixtures/pdf_table_ambiguous_merge.pdf").resolve(),
                [
                    {
                        "observation_id": "no-trusted-table",
                        "kind": "table_page",
                        "page": 1,
                        "expected_tables": [],
                    },
                    {
                        "observation_id": "fallback-capability",
                        "kind": "fallback_block",
                        "page": 1,
                        "quote": "Ambiguous header",
                    },
                ],
            ),
            _case(
                "authored-continuation",
                Path("tests/fixtures/pdf_table_continuation.pdf").resolve(),
                [
                    {
                        "observation_id": "root-page",
                        "kind": "table_page",
                        "page": 1,
                        "expected_tables": [
                            {
                                "row_count": 3,
                                "column_count": 2,
                                "continuation_role": "start",
                            }
                        ],
                    },
                    {
                        "observation_id": "continued-page",
                        "kind": "table_page",
                        "page": 2,
                        "expected_tables": [
                            {
                                "row_count": 3,
                                "column_count": 2,
                                "continuation_role": "continuation",
                            }
                        ],
                    },
                    {
                        "observation_id": "page-link",
                        "kind": "continuation_pair",
                        "root_page": 1,
                        "continued_page": 2,
                        "expected_linked": True,
                    },
                ],
            ),
        ],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = PdfCorpusRunner().run(manifest, tmp_path / "output")

    assert report["passed"] is True
    assert report["metrics"]["selected_table_topology_precision"] == 1.0
    assert report["metrics"]["selected_table_topology_recall"] == 1.0
    assert report["metrics"]["fallback_accuracy"] == 1.0
    assert report["metrics"]["continuation_pair_accuracy"] == 1.0
    assert report["metrics"]["continuation_false_positive_count"] == 0


def test_pdf_corpus_identity_drift_fails_without_hiding_observations(
    tmp_path: Path,
) -> None:
    source = Path("tests/fixtures/ieee29148_srs_example.pdf").resolve()
    manifest = tmp_path / "manifest.json"
    payload = {
        "schema_version": "pdf_corpus_v1",
        "name": "stale identity",
        "cases": [
            {
                **_case(
                    "stale-case",
                    source,
                    [
                        {
                            "observation_id": "known-source",
                            "kind": "text_source",
                            "quote": (
                                "The application shall allow users to create "
                                "a new empty document."
                            ),
                            "page": 5,
                        }
                    ],
                ),
                "expected_evidence_fingerprint": "f" * 64,
            }
        ],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = PdfCorpusRunner().run(manifest, tmp_path / "output")

    assert report["passed"] is False
    assert report["cases"][0]["identity_passed"] is False
    assert "evidence_fingerprint" in report["cases"][0]["identity_issues"][0]
    assert report["cases"][0]["observations"][0]["passed"] is True


def test_pdf_corpus_missing_document_fails_one_case_and_continues(
    tmp_path: Path,
) -> None:
    source = Path("tests/fixtures/ieee29148_srs_example.pdf").resolve()
    missing = tmp_path / "missing.pdf"
    manifest = tmp_path / "manifest.json"
    payload = {
        "schema_version": "pdf_corpus_v1",
        "name": "case isolation",
        "cases": [
            {
                **_case(
                    "missing-document",
                    source,
                    [
                        {
                            "observation_id": "never-evaluated",
                            "kind": "heading_page",
                            "page": 1,
                            "expected_headings": [],
                        }
                    ],
                ),
                "document": missing.as_posix(),
            },
            _case(
                "readable-document",
                source,
                [
                    {
                        "observation_id": "known-heading",
                        "kind": "heading_page",
                        "page": 5,
                        "expected_headings": [
                            "2. Requirements",
                            "2.2.1 File Operations",
                            "2.2.1.1 Create Document",
                            "2.2.1.2 Open File",
                            "2.2.1.3 Save Local File",
                            "2.2.1.4 Document Template",
                            "2.2.1.5 Import",
                            "2.2.1.6 Export",
                            "2.2.2 Document View",
                        ],
                    }
                ],
            ),
        ],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = PdfCorpusRunner().run(manifest, tmp_path / "output")

    assert report["case_count"] == 2
    assert report["case_passed"] == 1
    assert report["passed"] is False
    assert "PDF corpus document not found" in report["cases"][0]["error"]
    assert report["cases"][1]["passed"] is True


def test_pdf_corpus_manifest_rejects_duplicate_observation_ids() -> None:
    observation = {
        "observation_id": "duplicate",
        "kind": "heading_page",
        "page": 1,
        "expected_headings": [],
    }
    with pytest.raises(ValidationError, match="observation IDs must be unique"):
        PdfCorpusManifest.model_validate(
            {
                "schema_version": "pdf_corpus_v1",
                "name": "duplicates",
                "cases": [
                    _case(
                        "duplicate-observations",
                        Path("tests/fixtures/ieee29148_srs_example.pdf"),
                        [observation, observation],
                    )
                ],
            }
        )


def test_pdf_corpus_output_refuses_nonempty_unowned_directory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "unowned"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="PDF_CORPUS_OUTPUT_NOT_OWNED"):
        PdfCorpusRunner().run("eval/pdf_corpus_v1/manifest.json", output)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def _case(
    case_id: str,
    document: Path,
    observations: list[dict],
) -> dict:
    return {
        "case_id": case_id,
        "document": document.as_posix(),
        "source": {
            "provenance": "project_fixture",
            "producer_family": "test fixture",
            "redistribution_status": "redistribution_permitted",
            "license_note": "Project fixture.",
            "source_sha256": sha256_file(document),
        },
        "observations": observations,
    }
