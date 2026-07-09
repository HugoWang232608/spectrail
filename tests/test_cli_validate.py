from pathlib import Path

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
