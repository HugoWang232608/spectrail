from pathlib import Path
import shutil

import pytest

from spectrail.cli import main
from spectrail.core.io import read_json


def test_validate_cli_writes_report_and_validated_output(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--model-mode", "mock", "--output", str(output)]) == 0

    report_path = output / "extracted" / "validation_report.rerun.json"
    validated_path = output / "extracted" / "reqir.rerun.validated.json"
    assert (
        main(
            [
                "validate",
                str(output / "extracted" / "reqir.raw.json"),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
                "--output",
                str(report_path),
                "--validated-output",
                str(validated_path),
            ]
        )
        == 0
    )

    report = read_json(report_path)
    assert report["valid"] is True
    assert not [issue for issue in report["issues"] if issue["level"] == "error"]

    package = read_json(validated_path)
    assert len(package["items"]) >= 14


def test_validate_cli_auto_discovers_artifacts_for_exported_reqir(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0

    assert (
        main(
            [
                "validate",
                str(output / "exports" / "reqir.json"),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
            ]
        )
        == 0
    )


def test_validate_cli_accepts_explicit_evidence_artifacts(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0

    assert (
        main(
            [
                "validate",
                str(output / "exports" / "reqir.json"),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
                "--quote-matches",
                str(output / "extracted" / "quote_matches.json"),
                "--evidence-index",
                str(output / "parsed" / "evidence_index.json"),
            ]
        )
        == 0
    )


def test_validate_cli_does_not_rekey_export_without_registry(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    isolated = tmp_path / "isolated" / "reqir.json"
    isolated.parent.mkdir()
    shutil.copyfile(output / "exports" / "reqir.json", isolated)

    with pytest.raises(ValueError, match="quote match registry is required"):
        main(
            [
                "validate",
                str(isolated),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
                "--evidence-policy",
                "quote_only",
            ]
        )
