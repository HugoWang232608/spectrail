from pathlib import Path
import hashlib
import json
import shutil

import pytest

from spectrail.cli import main
from spectrail.core.io import read_json, write_json


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


def test_validate_cli_does_not_rekey_without_matching_evidence(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    isolated = tmp_path / "isolated" / "reqir.json"
    isolated.parent.mkdir()
    shutil.copyfile(output / "exports" / "reqir.json", isolated)

    with pytest.raises(ValueError, match="canonical source identity"):
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

    assert (
        main(
            [
                "validate",
                str(isolated),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
                "--evidence-index",
                str(output / "parsed" / "evidence_index.json"),
            ]
        )
        == 0
    )


def test_validate_cli_rebuilds_auto_discovered_v2_registry(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    registry_path = output / "extracted" / "quote_matches.json"
    registry = read_json(registry_path)
    registry["schema_version"] = "quote_matches_v2"
    write_json(registry_path, registry)

    with pytest.raises(ValueError, match="--rebuild-quote-matches"):
        main(
            [
                "validate",
                str(output / "exports" / "reqir.json"),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
            ]
        )

    assert (
        main(
            [
                "validate",
                str(output / "exports" / "reqir.json"),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
                "--rebuild-quote-matches",
            ]
        )
        == 0
    )
    assert read_json(registry_path)["schema_version"] == "quote_matches_v3"


def test_migrate_cli_rebinds_transient_v3_keys_and_unblocks_review(
    tmp_path: Path,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    registry_path = output / "extracted" / "quote_matches.json"
    current_registry = read_json(registry_path)
    old_entries: dict[str, dict] = {}

    reqir_paths = [
        output / "extracted" / "reqir.raw.json",
        output / "extracted" / "reqir.validated.json",
        output / "extracted" / "reqir.quarantined.json",
        output / "exports" / "reqir.json",
    ]
    for reqir_path in reqir_paths:
        package = read_json(reqir_path)
        package["schema_version"] = "reqir_v3"
        for item in package["items"]:
            for source in item["sources"]:
                current_key = source["source_evidence_key"]
                payload = {
                    "evidence_fingerprint": read_json(
                        output / "parsed" / "evidence_index.json"
                    )["evidence_fingerprint"],
                    "document_id": source["document_id"],
                    "block_id": source["block_id"],
                    "quote": source["quote"],
                    "canonical_cell_ids": source[
                        "canonical_source_cell_ids"
                    ],
                    "source_table_row_index": None,
                }
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                old_key = f"src_{hashlib.sha256(encoded).hexdigest()[:24]}"
                source["source_evidence_key"] = old_key
                old_entries[old_key] = current_registry["entries"][current_key]
        write_json(reqir_path, package)
    write_json(
        registry_path,
        {
            "schema_version": "quote_matches_v2",
            "entries": old_entries,
        },
    )

    with pytest.raises(SystemExit, match=f"spectrail migrate {output}"):
        main(["review", str(output)])
    with pytest.raises(ValueError, match=f"spectrail migrate {output}"):
        main(
            [
                "validate",
                str(output / "exports" / "reqir.json"),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
            ]
        )

    assert main(["migrate", str(output)]) == 0
    migrated_reqir = read_json(output / "exports" / "reqir.json")
    migrated_registry = read_json(registry_path)
    assert migrated_reqir["schema_version"] == "reqir_v4"
    assert migrated_registry["schema_version"] == "quote_matches_v3"
    assert all(
        source["source_evidence_key"] in migrated_registry["entries"]
        for item in migrated_reqir["items"]
        for source in item["sources"]
    )
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
    assert main(["review", str(output)]) == 0


def test_migrate_cli_upgrades_valid_evidence_v4(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    evidence_path = output / "parsed" / "evidence_index.json"
    evidence = read_json(evidence_path)
    evidence["schema_version"] = "evidence_v4"
    fingerprint_payload = {
        key: value for key, value in evidence.items() if key != "evidence_fingerprint"
    }
    encoded = json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    old_fingerprint = hashlib.sha256(encoded).hexdigest()
    evidence["evidence_fingerprint"] = old_fingerprint
    write_json(evidence_path, evidence)

    assert main(["migrate", str(output)]) == 0
    migrated = read_json(evidence_path)
    assert migrated["schema_version"] == "evidence_v5"
    assert migrated["evidence_fingerprint"] != old_fingerprint
    assert read_json(output / "run_manifest.json")["migration"][
        "evidence_schema_from"
    ] == "evidence_v4"


def test_migrate_cli_requires_reparse_for_invalid_evidence_v4(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    evidence_path = output / "parsed" / "evidence_index.json"
    evidence = read_json(evidence_path)
    evidence["schema_version"] = "evidence_v4"
    evidence["blocks"][0]["expected_capabilities"] = []
    write_json(evidence_path, evidence)

    with pytest.raises(SystemExit, match="EVIDENCE_V4_REPARSE_REQUIRED"):
        main(["migrate", str(output)])
    assert read_json(evidence_path)["schema_version"] == "evidence_v4"


def test_validate_cli_rejects_stale_evidence_fingerprint(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    evidence = read_json(output / "parsed" / "evidence_index.json")
    evidence["warnings"].append("tampered")
    tampered = tmp_path / "tampered_evidence.json"
    write_json(tampered, evidence)

    with pytest.raises(ValueError, match="fingerprint does not match"):
        main(
            [
                "validate",
                str(output / "exports" / "reqir.json"),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
                "--quote-matches",
                str(output / "extracted" / "quote_matches.json"),
                "--evidence-index",
                str(tampered),
            ]
        )
