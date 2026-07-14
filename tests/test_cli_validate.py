from pathlib import Path
import hashlib
import json
import shutil
import socket

import pytest

import spectrail.migrations as migrations
import spectrail.task_transactions as task_transactions
from spectrail.cli import main
from spectrail.core.io import read_json, write_json
from spectrail.pipeline import PipelineRunner
from spectrail.task_transactions import task_lock


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
    migration_report = read_json(output / "migration" / "migration_report.json")
    assert migration_report["statistics"] == {
        "processed_source_occurrences": 45,
        "unique_source_identities": 15,
        "rebound_source_keys": 15,
        "bound_missing_source_keys": 0,
    }
    assert len(migration_report["sources"]) == 45
    assert {
        "artifact_path",
        "requirement_id",
        "source_index",
        "old_source_evidence_key",
        "new_source_evidence_key",
        "old_locator_status",
        "new_locator_status",
        "old_match_status",
        "new_match_status",
    } <= migration_report["sources"][0].keys()
    assert (output / migration_report["backup_path"]).is_dir()
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


def test_migrate_rejects_invalid_export_without_writing_any_artifact(
    tmp_path: Path,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    export_path = output / "exports" / "reqir.json"
    package = read_json(export_path)
    package["items"][0]["sources"][0]["quote"] = "not present in the document"
    write_json(export_path, package)
    observed_paths = [
        output / "parsed" / "evidence_index.json",
        output / "extracted" / "quote_matches.json",
        output / "extracted" / "reqir.raw.json",
        output / "extracted" / "reqir.validated.json",
        export_path,
        output / "run_manifest.json",
    ]
    before = {path: path.read_bytes() for path in observed_paths}

    with pytest.raises(SystemExit, match="REQIR_LEGACY_REENRICHMENT_FAILED"):
        main(["migrate", str(output)])

    assert {path: path.read_bytes() for path in observed_paths} == before
    assert not (output / ".migration_tmp").exists()
    assert not (output / "migration" / "migration_report.json").exists()


def test_migrate_rejects_invalid_cell_reference_without_writing(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    export_path = output / "exports" / "reqir.json"
    package = read_json(export_path)
    source = package["items"][0]["sources"][0]
    source["source_cell_ids_raw"] = ["cell_00000001_r0001_c0001"]
    source["canonical_source_cell_ids"] = ["cell_00000001_r0001_c0001"]
    source["source_table_row_index"] = 1
    write_json(export_path, package)
    before = export_path.read_bytes()

    with pytest.raises(SystemExit, match="REQIR_LEGACY_REENRICHMENT_FAILED"):
        main(["migrate", str(output)])

    assert export_path.read_bytes() == before
    assert not (output / ".migration_tmp").exists()


def test_migrate_cleans_preparation_when_staged_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    export_path = output / "exports" / "reqir.json"
    before = export_path.read_bytes()
    original_write_json = migrations.write_json

    def fail_preparation_write(path: Path, payload: object) -> None:
        if ".migration_prepare_" in str(path):
            raise OSError("simulated staged write failure")
        original_write_json(path, payload)

    monkeypatch.setattr(migrations, "write_json", fail_preparation_write)
    with pytest.raises(OSError, match="simulated staged write failure"):
        main(["migrate", str(output)])

    assert export_path.read_bytes() == before
    assert not (output / ".migration_tmp").exists()
    assert not list(output.glob(".migration_prepare_*"))


def test_migrate_cleans_preparation_when_staged_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    export_path = output / "exports" / "reqir.json"
    before = export_path.read_bytes()

    def fail_verification(path: Path, artifact_type: str) -> None:
        del path, artifact_type
        raise ValueError("simulated staged verification failure")

    monkeypatch.setattr(migrations, "_verify_staged_artifact", fail_verification)
    with pytest.raises(SystemExit, match="simulated staged verification failure"):
        main(["migrate", str(output)])

    assert export_path.read_bytes() == before
    assert not (output / ".migration_tmp").exists()
    assert not list(output.glob(".migration_prepare_*"))


def test_migrate_cleans_preparation_when_backup_copy_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    export_path = output / "exports" / "reqir.json"
    before = export_path.read_bytes()
    original_copy2 = migrations.shutil.copy2

    def fail_backup_copy(source: Path, target: Path) -> None:
        if ".migration_prepare_" in str(target):
            raise OSError("simulated backup copy failure")
        original_copy2(source, target)

    monkeypatch.setattr(migrations.shutil, "copy2", fail_backup_copy)
    with pytest.raises(OSError, match="simulated backup copy failure"):
        main(["migrate", str(output)])

    assert export_path.read_bytes() == before
    assert not (output / ".migration_tmp").exists()
    assert not list(output.glob(".migration_prepare_*"))


def test_migrate_allows_invalid_raw_package_and_records_validation_report(
    tmp_path: Path,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    raw_path = output / "extracted" / "reqir.raw.json"
    raw = read_json(raw_path)
    raw["items"][0]["sources"][0]["quote"] = "not present in the document"
    write_json(raw_path, raw)

    assert main(["migrate", str(output)]) == 0
    report = read_json(output / "migration" / "migration_report.json")
    raw_report = next(
        item for item in report["packages"] if item["package_kind"] == "raw"
    )
    assert raw_report["strict_validation_required"] is False
    assert raw_report["quote_report"]["valid"] is False
    assert raw_report["quote_validated_count"] == 14
    assert read_json(output / "exports" / "reqir.json")["schema_version"] == (
        "reqir_v4"
    )


def test_migrate_recovers_interrupted_commit_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    reqir_paths = [
        output / "extracted" / "reqir.raw.json",
        output / "extracted" / "reqir.validated.json",
        output / "extracted" / "reqir.quarantined.json",
        output / "exports" / "reqir.json",
    ]
    for path in reqir_paths:
        package = read_json(path)
        package["schema_version"] = "reqir_v3"
        write_json(path, package)

    original_replace = migrations._replace_staged_file
    replace_count = 0

    def interrupt_fourth_replace(source: Path, target: Path) -> None:
        nonlocal replace_count
        replace_count += 1
        if replace_count == 4:
            raise OSError("simulated commit interruption")
        original_replace(source, target)

    monkeypatch.setattr(migrations, "_replace_staged_file", interrupt_fourth_replace)
    with pytest.raises(SystemExit, match="MIGRATION_COMMIT_INTERRUPTED"):
        main(["migrate", str(output)])
    assert read_json(output / ".migration_tmp" / "state.json")["status"] == (
        "committing"
    )
    assert read_json(reqir_paths[0])["schema_version"] == "reqir_v4"
    assert read_json(reqir_paths[1])["schema_version"] == "reqir_v3"

    export_before = reqir_paths[-1].read_bytes()
    with pytest.raises(ValueError, match="TASK_MIGRATION_INCOMPLETE"):
        main(["review", str(output)])
    with pytest.raises(ValueError, match="TASK_MIGRATION_INCOMPLETE"):
        main(
            [
                "validate",
                str(reqir_paths[-1]),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
            ]
        )
    with pytest.raises(ValueError, match="TASK_MIGRATION_INCOMPLETE"):
        main(
            [
                "export",
                str(reqir_paths[-1]),
                "--output",
                str(tmp_path / "blocked.xlsx"),
            ]
        )
    with pytest.raises(ValueError, match="TASK_MIGRATION_INCOMPLETE"):
        PipelineRunner().extract("docs/sample_srs.md", output)
    assert reqir_paths[-1].read_bytes() == export_before

    monkeypatch.setattr(migrations, "_replace_staged_file", original_replace)
    assert main(["migrate", str(output)]) == 0
    assert not (output / ".migration_tmp").exists()
    assert all(read_json(path)["schema_version"] == "reqir_v4" for path in reqir_paths)
    assert len(list((output / ".migration_backup").iterdir())) == 2


def test_migrate_discards_legacy_preparation_without_transaction_state(
    tmp_path: Path,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    abandoned = output / ".migration_tmp"
    abandoned.mkdir()
    (abandoned / "partial.json").write_text("partial", encoding="utf-8")

    assert main(["migrate", str(output)]) == 0
    assert not abandoned.exists()


@pytest.mark.parametrize(
    "first_key,second_key",
    [
        ("evidence_index", "quote_matches"),
        ("reqir_raw", "reqir_export"),
    ],
)
def test_migrate_rejects_manifest_artifact_path_collisions(
    tmp_path: Path,
    first_key: str,
    second_key: str,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    manifest_path = output / "run_manifest.json"
    manifest = read_json(manifest_path)
    manifest["outputs"][first_key] = manifest["outputs"][second_key]
    write_json(manifest_path, manifest)
    before = manifest_path.read_bytes()

    with pytest.raises(SystemExit, match="MIGRATION_ARTIFACT_PATH_COLLISION"):
        main(["migrate", str(output)])

    assert manifest_path.read_bytes() == before
    assert not (output / ".migration_tmp").exists()


@pytest.mark.parametrize(
    "target_path",
    ["../../outside.json", "/tmp/spectrail-outside.json"],
)
def test_migration_recovery_rejects_untrusted_state_paths(
    tmp_path: Path,
    target_path: str,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    outside = tmp_path / "outside.json"
    outside.write_text("sentinel", encoding="utf-8")
    migration_id = "20260714T000000000000Z_deadbeef"
    staging = output / ".migration_tmp"
    staging.mkdir()
    write_json(
        staging / "state.json",
        {
            "schema_version": "migration_transaction_v1",
            "migration_id": migration_id,
            "status": "committing",
            "backup_path": f".migration_backup/{migration_id}",
            "targets": [
                {
                    "path": target_path,
                    "artifact_type": "reqir",
                    "existed": True,
                }
            ],
        },
    )

    with pytest.raises(SystemExit, match="MIGRATION_RECOVERY_STATE_INVALID"):
        main(["migrate", str(output)])
    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_migration_recovery_rejects_symlink_escape_and_duplicate_targets(
    tmp_path: Path,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_text("sentinel", encoding="utf-8")
    (output / "escape").symlink_to(outside, target_is_directory=True)
    migration_id = "20260714T000000000000Z_deadbeef"
    staging = output / ".migration_tmp"
    staging.mkdir()
    base_target = {
        "path": "escape/sentinel.json",
        "artifact_type": "reqir",
        "existed": False,
    }
    write_json(
        staging / "state.json",
        {
            "schema_version": "migration_transaction_v1",
            "migration_id": migration_id,
            "status": "committing",
            "backup_path": f".migration_backup/{migration_id}",
            "targets": [base_target],
        },
    )
    with pytest.raises(SystemExit, match="MIGRATION_RECOVERY_PATH_OUTSIDE_TASK"):
        main(["migrate", str(output)])
    assert sentinel.read_text(encoding="utf-8") == "sentinel"

    shutil.rmtree(staging)
    staging.mkdir()
    write_json(
        staging / "state.json",
        {
            "schema_version": "migration_transaction_v1",
            "migration_id": migration_id,
            "status": "committing",
            "backup_path": f".migration_backup/{migration_id}",
            "targets": [
                {**base_target, "path": "exports/reqir.json"},
                {**base_target, "path": "exports/reqir.json"},
            ],
        },
    )
    with pytest.raises(SystemExit, match="MIGRATION_RECOVERY_STATE_INVALID"):
        main(["migrate", str(output)])


def test_migration_recovery_rejects_untrusted_backup_and_unknown_fields(
    tmp_path: Path,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    migration_id = "20260714T000000000000Z_deadbeef"
    staging = output / ".migration_tmp"
    staging.mkdir()
    state = {
        "schema_version": "migration_transaction_v1",
        "migration_id": migration_id,
        "status": "committing",
        "backup_path": "../../backup",
        "targets": [
            {
                "path": "exports/reqir.json",
                "artifact_type": "reqir",
                "existed": False,
            }
        ],
    }
    write_json(staging / "state.json", state)
    with pytest.raises(SystemExit, match="MIGRATION_RECOVERY_STATE_INVALID"):
        main(["migrate", str(output)])

    shutil.rmtree(staging)
    staging.mkdir()
    state["backup_path"] = f".migration_backup/{migration_id}"
    state["targets"][0]["unexpected"] = "untrusted"
    write_json(staging / "state.json", state)
    with pytest.raises(SystemExit, match="MIGRATION_RECOVERY_STATE_INVALID"):
        main(["migrate", str(output)])


def test_active_task_lock_blocks_second_migrate_without_deleting_staging(
    tmp_path: Path,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    with task_lock(output, operation="first_migration"):
        staging = output / ".migration_tmp"
        staging.mkdir()
        marker = staging / "active"
        marker.write_text("owned", encoding="utf-8")
        with pytest.raises(SystemExit, match="TASK_TRANSACTION_LOCKED"):
            main(["migrate", str(output)])
        assert marker.read_text(encoding="utf-8") == "owned"
    shutil.rmtree(output / ".migration_tmp")


def test_migrate_reclaims_stale_same_host_lock(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    lock_dir = output / ".task.lock"
    lock_dir.mkdir()
    write_json(
        lock_dir / "owner.json",
        {
            "schema_version": "task_lock_v1",
            "token": "stale",
            "operation": "migrate",
            "pid": 99999999,
            "host": socket.gethostname(),
            "started_at": "2026-07-14T00:00:00Z",
        },
    )

    assert main(["migrate", str(output)]) == 0
    assert not lock_dir.exists()


def test_lock_owner_write_failure_never_publishes_official_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "demo"
    output.mkdir()
    original_write_json = task_transactions.write_json

    def fail_owner_write(path: Path, payload: object) -> None:
        if path.name == "owner.json":
            raise OSError("simulated owner write failure")
        original_write_json(path, payload)

    monkeypatch.setattr(task_transactions, "write_json", fail_owner_write)
    with pytest.raises(OSError, match="simulated owner write failure"):
        with task_lock(output, operation="test"):
            pass

    assert not (output / ".task.lock").exists()
    assert not [
        path
        for path in output.glob(".task.lock.*")
        if path.name != ".task.lock.guard"
    ]


def test_migrate_conservatively_reclaims_old_lock_without_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    lock_dir = output / ".task.lock"
    lock_dir.mkdir()
    lock_mtime = lock_dir.stat().st_mtime
    monkeypatch.setattr(
        task_transactions.time,
        "time",
        lambda: lock_mtime + 301,
    )

    assert main(["migrate", str(output)]) == 0
    assert not lock_dir.exists()


def test_validate_rejects_artifacts_from_different_task_roots(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert main(["extract", "docs/sample_srs.md", "--output", str(first)]) == 0
    assert main(["extract", "docs/sample_srs.md", "--output", str(second)]) == 0

    with pytest.raises(
        ValueError,
        match="VALIDATION_CROSS_TASK_ARTIFACTS_NOT_ALLOWED",
    ):
        main(
            [
                "validate",
                str(first / "exports" / "reqir.json"),
                "--blocks",
                str(first / "parsed" / "blocks.json"),
                "--evidence-index",
                str(second / "parsed" / "evidence_index.json"),
            ]
        )


def test_validate_rejects_manifest_artifact_outside_task_root(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert main(["extract", "docs/sample_srs.md", "--output", str(first)]) == 0
    assert main(["extract", "docs/sample_srs.md", "--output", str(second)]) == 0
    manifest_path = first / "run_manifest.json"
    manifest = read_json(manifest_path)
    manifest["outputs"]["evidence_index"] = (
        "../second/parsed/evidence_index.json"
    )
    write_json(manifest_path, manifest)

    with pytest.raises(
        ValueError,
        match="VALIDATION_MANIFEST_OUTPUT_PATH_OUTSIDE_TASK",
    ):
        main(
            [
                "validate",
                str(first / "exports" / "reqir.json"),
                "--blocks",
                str(first / "parsed" / "blocks.json"),
            ]
        )


def test_validate_rejects_local_artifact_symlink_outside_task_root(
    tmp_path: Path,
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert main(["extract", "docs/sample_srs.md", "--output", str(first)]) == 0
    assert main(["extract", "docs/sample_srs.md", "--output", str(second)]) == 0
    local_evidence = first / "exports" / "evidence_index.json"
    local_evidence.symlink_to(
        second / "parsed" / "evidence_index.json"
    )

    with pytest.raises(
        ValueError,
        match="VALIDATION_LOCAL_ARTIFACT_OUTSIDE_TASK",
    ):
        main(
            [
                "validate",
                str(first / "exports" / "reqir.json"),
                "--blocks",
                str(first / "parsed" / "blocks.json"),
            ]
        )


def test_validate_rejects_non_object_manifest_outputs(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    manifest_path = output / "run_manifest.json"
    manifest = read_json(manifest_path)
    manifest["outputs"] = []
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="VALIDATION_MANIFEST_OUTPUTS_INVALID"):
        main(
            [
                "validate",
                str(output / "exports" / "reqir.json"),
                "--blocks",
                str(output / "parsed" / "blocks.json"),
            ]
        )


def test_validate_explicit_artifacts_bypass_invalid_manifest_paths(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    manifest_path = output / "run_manifest.json"
    manifest = read_json(manifest_path)
    manifest["outputs"]["quote_matches"] = "../../unsafe-quotes.json"
    manifest["outputs"]["evidence_index"] = "../../unsafe-evidence.json"
    write_json(manifest_path, manifest)

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


def test_migration_fsyncs_staged_and_backup_files_before_prepared_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    events: list[tuple[str, Path]] = []
    original_fsync_file = migrations._fsync_file
    original_write_state = migrations._write_state_atomic

    def observe_fsync(path: Path) -> None:
        events.append(("file", path))
        original_fsync_file(path)

    def observe_state(path: Path, payload: dict) -> None:
        if payload["status"] == "prepared":
            events.append(("prepared", path))
        original_write_state(path, payload)

    monkeypatch.setattr(migrations, "_fsync_file", observe_fsync)
    monkeypatch.setattr(migrations, "_write_state_atomic", observe_state)

    assert main(["migrate", str(output)]) == 0

    prepared_index = next(
        index for index, event in enumerate(events) if event[0] == "prepared"
    )
    durable_paths = [path for kind, path in events[:prepared_index] if kind == "file"]
    assert any("/files/" in path.as_posix() for path in durable_paths)
    assert any("/backup/files/" in path.as_posix() for path in durable_paths)


def test_migration_state_is_fsynced_before_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []
    state_path = tmp_path / "transaction" / "state.json"
    state = {
        "schema_version": "migration_transaction_v1",
        "migration_id": "20260714T000000000000Z_deadbeef",
        "status": "prepared",
        "backup_path": (
            ".migration_backup/20260714T000000000000Z_deadbeef"
        ),
        "targets": [],
    }

    monkeypatch.setattr(
        migrations.os,
        "fsync",
        lambda descriptor: events.append(f"file:{descriptor}"),
    )
    monkeypatch.setattr(
        migrations,
        "_fsync_directory",
        lambda path: events.append(f"directory:{path.name}"),
    )
    migrations._write_state_atomic(state_path, state)

    assert events[0].startswith("file:")
    assert events[1] == "directory:transaction"
    assert read_json(state_path)["status"] == "prepared"


def test_migrate_cleans_only_strict_abandoned_preparations_without_following_symlinks(
    tmp_path: Path,
):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--output", str(output)]) == 0
    abandoned = (
        output
        / ".migration_prepare_20260714T000000000000Z_deadbeef"
    )
    abandoned.mkdir()
    (abandoned / "sensitive.json").write_text("staged", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_text("keep", encoding="utf-8")
    abandoned_symlink = (
        output
        / ".migration_prepare_20260714T000000000001Z_feedface"
    )
    abandoned_symlink.symlink_to(outside, target_is_directory=True)
    nonmatching = output / ".migration_prepare_keep"
    nonmatching.mkdir()

    assert main(["migrate", str(output)]) == 0

    assert not abandoned.exists()
    assert not abandoned_symlink.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert nonmatching.is_dir()


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
