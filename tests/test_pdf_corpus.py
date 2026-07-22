from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest
from pydantic import ValidationError

from spectrail.cli import main
from spectrail.core.io import read_json, write_json
from spectrail.evaluation.pdf_corpus import (
    PDF_CORPUS_OUTPUT_MARKER_PAYLOAD,
    PdfCorpusCase,
    PdfCorpusManifest,
    PdfCorpusRunner,
)
from spectrail.evidence import sha256_file


def test_checked_pdf_corpus_reports_multi_producer_structural_baseline(
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
    assert report["case_count"] == 5
    assert report["metrics"]["producer_family_count"] == 4
    assert report["metrics"]["external_document_case_count"] == 2
    assert report["metrics"]["redistribution_reviewed_case_count"] == 4
    assert report["metrics"]["metadata_locked_case_count"] == 5
    assert report["runtime_platform_id"]
    assert report["metrics"]["text_source_accuracy"] == 1.0
    assert report["metrics"]["page_region_availability_rate"] == 1.0
    assert report["metrics"]["selected_table_topology_precision"] == 1.0
    assert report["metrics"]["selected_table_topology_recall"] == 1.0
    assert report["metrics"]["continuation_pair_accuracy"] == 1.0
    assert report["metrics"]["continuation_false_positive_count"] == 0
    assert report["metrics"]["heading_precision"] == pytest.approx(0.9)
    assert report["metrics"]["heading_recall"] == pytest.approx(9 / 11)
    word_case = next(
        item
        for item in report["cases"]
        if item["case_id"] == "ieee29148_word_export"
    )
    assert word_case["actual_parser_identity"]["parser_version"] == "2.18"
    assert (
        word_case["actual_evidence_fingerprint"]
        == "b2793917b65cd81781a3bce68f010bbc8178fb02263615acbbd5d568d6e8ce97"
    )
    assert word_case["actual_pdf_metadata"]["creator"] == (
        "Microsoft® Word for Office 365"
    )
    assert read_json(output / ".spectrail-pdf-corpus-output") == (
        PDF_CORPUS_OUTPUT_MARKER_PAYLOAD
    )

    observations = word_case["observations"]
    decoration = next(
        item
        for item in observations
        if item["observation_id"] == "page-1-decoration-vs-heading"
    )
    assert decoration["gate"] is False
    assert decoration["passed"] is False

    libreoffice_case = next(
        item
        for item in report["cases"]
        if item["case_id"] == "libreoffice_merged_tables"
    )
    independence = next(
        item
        for item in libreoffice_case["observations"]
        if item["observation_id"]
        == "adjacent-isomorphic-tables-remain-independent"
    )
    assert independence["passed"] is True
    assert independence["details"]["actual_linked"] is False


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


def test_pdf_corpus_pdf_metadata_drift_fails_identity(
    tmp_path: Path,
) -> None:
    source = Path("tests/fixtures/pdf_table_requirements.pdf").resolve()
    manifest = tmp_path / "manifest.json"
    case = _case(
        "metadata-drift",
        source,
        [
            {
                "observation_id": "known-table",
                "kind": "table_page",
                "page": 1,
                "expected_tables": [
                    {
                        "row_count": 3,
                        "column_count": 3,
                    }
                ],
            }
        ],
    )
    case["source"]["expected_pdf_metadata"] = {
        "producer": "Not the checked producer"
    }
    write_json(
        manifest,
        {
            "schema_version": "pdf_corpus_v1",
            "name": "metadata identity drift",
            "cases": [case],
        },
    )

    report = PdfCorpusRunner().run(manifest, tmp_path / "output")

    assert report["passed"] is False
    assert report["cases"][0]["identity_passed"] is False
    assert report["cases"][0]["observations"][0]["passed"] is True
    assert report["cases"][0]["identity_issues"] == [
        "PDF metadata producer expected='Not the checked producer' "
        "actual='ReportLab PDF Library - (opensource)'"
    ]


def test_booktabs_provenance_record_matches_checked_corpus_case() -> None:
    manifest = read_json("eval/pdf_corpus_v1/manifest.json")
    case = next(
        item
        for item in manifest["cases"]
        if item["case_id"] == "booktabs_pdftex_manual"
    )
    provenance = read_json(
        "tests/fixtures/pdf_corpus_booktabs.provenance.json"
    )

    assert provenance["fixture"] == Path(case["document"]).name
    assert provenance["source_url"] == case["source"]["source_url"]
    assert provenance["source_sha256"] == case["source"]["source_sha256"]
    assert provenance["pdf_metadata"]["creator"] == (
        case["source"]["expected_pdf_metadata"]["creator"]
    )
    assert provenance["pdf_metadata"]["producer"] == (
        case["source"]["expected_pdf_metadata"]["producer"]
    )
    assert provenance["package"]["license"] == "LPPL-1.3c"


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


def test_pdf_corpus_core_case_requires_metadata_lock() -> None:
    case = _case(
        "missing-metadata",
        Path("tests/fixtures/pdf_table_requirements.pdf"),
        [_heading_observation()],
    )
    case["source"].pop("expected_pdf_metadata")

    with pytest.raises(
        ValidationError,
        match="core PDF corpus cases require expected_pdf_metadata",
    ):
        PdfCorpusCase.model_validate(case)


def test_pdf_corpus_external_source_requires_url() -> None:
    case = _case(
        "missing-source-url",
        Path("tests/fixtures/pdf_table_requirements.pdf"),
        [_heading_observation()],
    )
    case["source"]["provenance"] = "external_document"

    with pytest.raises(
        ValidationError,
        match="external PDF corpus sources require source_url",
    ):
        PdfCorpusCase.model_validate(case)


def test_pdf_corpus_core_case_cannot_be_download_only() -> None:
    case = _case(
        "download-only-core",
        Path("tests/fixtures/pdf_table_requirements.pdf"),
        [_heading_observation()],
    )
    case["source"]["redistribution_status"] = "download_only"

    with pytest.raises(
        ValidationError,
        match="core PDF corpus cases cannot use download_only sources",
    ):
        PdfCorpusCase.model_validate(case)


def test_pdf_corpus_producer_family_id_must_be_normalized() -> None:
    case = _case(
        "invalid-producer-family",
        Path("tests/fixtures/pdf_table_requirements.pdf"),
        [_heading_observation()],
    )
    case["source"]["producer_family_id"] = "ReportLab PDF"

    with pytest.raises(ValidationError, match="producer_family_id"):
        PdfCorpusCase.model_validate(case)


def test_pdf_corpus_only_heading_observations_may_be_report_only() -> None:
    case = _case(
        "report-only-text",
        Path("tests/fixtures/pdf_table_requirements.pdf"),
        [
            {
                "observation_id": "report-only-text",
                "kind": "text_source",
                "gate": False,
                "quote": "REQ-001",
                "page": 1,
            }
        ],
    )

    with pytest.raises(
        ValidationError,
        match="only heading_page observations may use gate=false",
    ):
        PdfCorpusCase.model_validate(case)


def test_pdf_corpus_platform_fingerprint_overrides_default() -> None:
    case = _case(
        "platform-fingerprint",
        Path("tests/fixtures/pdf_table_requirements.pdf"),
        [_heading_observation()],
    )
    case["expected_evidence_fingerprint"] = "a" * 64
    case["expected_evidence_fingerprints_by_platform"] = {
        "linux-x86_64": "b" * 64,
    }
    parsed = PdfCorpusCase.model_validate(case)

    assert parsed.expected_fingerprint_for_platform("linux-x86_64") == "b" * 64
    assert parsed.expected_fingerprint_for_platform("darwin-arm64") == "a" * 64


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


def test_pdf_corpus_clears_stale_reports_before_case_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    write_json(
        output / ".spectrail-pdf-corpus-output",
        PDF_CORPUS_OUTPUT_MARKER_PAYLOAD,
    )
    json_report = output / "pdf_corpus_report.json"
    markdown_report = output / "pdf_corpus_report.md"
    json_report.write_text('{"passed":true}', encoding="utf-8")
    markdown_report.write_text("# stale success\n", encoding="utf-8")
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    def crash_during_parse(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("unexpected parser crash")

    monkeypatch.setattr(
        "spectrail.evaluation.pdf_corpus.parse_document",
        crash_during_parse,
    )

    with pytest.raises(RuntimeError, match="unexpected parser crash"):
        PdfCorpusRunner().run("eval/pdf_corpus_v1/manifest.json", output)

    assert not json_report.exists()
    assert not markdown_report.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_pdf_corpus_replaces_managed_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    write_json(
        output / ".spectrail-pdf-corpus-output",
        PDF_CORPUS_OUTPUT_MARKER_PAYLOAD,
    )
    external = tmp_path / "external.json"
    external.write_text("do not overwrite", encoding="utf-8")
    report = output / "pdf_corpus_report.json"
    report.symlink_to(external)
    (output / "pdf_corpus_report.md").write_text(
        "# stale success\n",
        encoding="utf-8",
    )
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = PdfCorpusRunner().run(
        "eval/pdf_corpus_v1/manifest.json",
        output,
    )

    assert result["passed"] is True
    assert not report.is_symlink()
    assert read_json(report)["schema_version"] == "pdf_corpus_report_v1"
    assert external.read_text(encoding="utf-8") == "do not overwrite"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_pdf_corpus_rejects_managed_report_directory_before_cleanup(
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    write_json(
        output / ".spectrail-pdf-corpus-output",
        PDF_CORPUS_OUTPUT_MARKER_PAYLOAD,
    )
    (output / "pdf_corpus_report.json").mkdir()
    markdown_report = output / "pdf_corpus_report.md"
    markdown_report.write_text("# stale success\n", encoding="utf-8")

    with pytest.raises(ValueError, match="PDF_CORPUS_MANAGED_PATH_NOT_FILE"):
        PdfCorpusRunner().run("eval/pdf_corpus_v1/manifest.json", output)

    assert (output / "pdf_corpus_report.json").is_dir()
    assert markdown_report.read_text(encoding="utf-8") == "# stale success\n"


def test_pdf_corpus_accepts_and_upgrades_legacy_output_marker(
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    write_json(
        output / ".spectrail-pdf-corpus-output",
        {
            "schema_version": "spectrail_pdf_corpus_output_v1",
            "managed_paths": [
                "pdf_corpus_report.json",
                "pdf_corpus_report.md",
            ],
        },
    )

    result = PdfCorpusRunner().run(
        "eval/pdf_corpus_v1/manifest.json",
        output,
    )

    assert result["passed"] is True
    assert read_json(output / ".spectrail-pdf-corpus-output") == (
        PDF_CORPUS_OUTPUT_MARKER_PAYLOAD
    )


def test_pdf_corpus_does_not_publish_partial_report_when_staging_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"

    def fail_markdown_write(path: Path, value: str) -> None:
        del path, value
        raise OSError("simulated Markdown staging failure")

    monkeypatch.setattr(
        "spectrail.evaluation.pdf_corpus._write_text_durable",
        fail_markdown_write,
    )

    with pytest.raises(OSError, match="simulated Markdown staging failure"):
        PdfCorpusRunner().run("eval/pdf_corpus_v1/manifest.json", output)

    assert not (output / "pdf_corpus_report.json").exists()
    assert not (output / "pdf_corpus_report.md").exists()
    assert not (output / ".pdf_corpus_report.json.staged").exists()
    assert not (output / ".pdf_corpus_report.md.staged").exists()


def test_pdf_corpus_unknown_threshold_is_a_configuration_error(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    payload = read_json("eval/pdf_corpus_v1/manifest.json")
    payload["thresholds"] = {"text_sorce_accuracy_min": 1.0}
    write_json(manifest, payload)

    with pytest.raises(
        SystemExit,
        match="PDF_CORPUS_THRESHOLD_UNKNOWN_METRIC.*text_sorce_accuracy_min",
    ):
        main(
            [
                "evaluate-pdf-corpus",
                str(manifest),
                "--output",
                str(tmp_path / "output"),
            ]
        )

    assert not (tmp_path / "output").exists()


def _case(
    case_id: str,
    document: Path,
    observations: list[dict],
) -> dict:
    with fitz.open(document) as pdf:
        metadata = {
            name: pdf.metadata.get(name)
            for name in ("creator", "producer", "title")
            if pdf.metadata.get(name)
        }
    return {
        "case_id": case_id,
        "document": document.as_posix(),
        "source": {
            "provenance": "project_fixture",
            "producer_family_id": "test_fixture",
            "producer_family": "test fixture",
            "redistribution_status": "redistribution_permitted",
            "license_note": "Project fixture.",
            "source_sha256": sha256_file(document),
            "expected_pdf_metadata": metadata,
        },
        "observations": observations,
    }


def _heading_observation() -> dict:
    return {
        "observation_id": "heading",
        "kind": "heading_page",
        "page": 1,
        "expected_headings": [],
    }
