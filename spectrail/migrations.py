from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from spectrail.core.io import read_json, reqir_package_dump, write_json
from spectrail.core.models import DocumentBlock, ReqIRPackage, RequirementIR
from spectrail.evidence import (
    EvidenceIndex,
    QuoteMatchRegistry,
    build_quote_match_registry,
    finalize_evidence_fingerprint,
    validate_evidence_fingerprint,
)
from spectrail.evidence.enricher import SourceEvidenceEnricher
from spectrail.evidence.index_builder import (
    validate_evidence_index_against_parsed_document,
)
from spectrail.evidence.source_identity import canonicalize_source_cell_ids
from spectrail.exporters.source_map_exporter import build_source_map
from spectrail.parsers import ParsedDocument
from spectrail.task_transactions import task_lock
from spectrail.validators.source_locator_validator import SourceLocatorValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator


BlockListAdapter = TypeAdapter(list[DocumentBlock])
SUPPORTED_REQIR_VERSIONS = {None, "reqir_v1", "reqir_v2", "reqir_v3", "reqir_v4"}
STRICT_PACKAGE_KINDS = {"validated", "export"}
EVIDENCE_POLICIES = {
    "quote_only",
    "structured_if_available",
    "structured_required",
}

MigrationArtifactType = Literal[
    "evidence",
    "quote_matches",
    "reqir",
    "source_map",
    "migration_report",
    "manifest",
]


class MigrationTargetState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    artifact_type: MigrationArtifactType
    existed: StrictBool

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_state_relative_path(value)


class MigrationTransactionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["migration_transaction_v1"]
    migration_id: str = Field(pattern=r"^[0-9]{8}T[0-9]{12}Z_[0-9a-f]{8}$")
    status: Literal["prepared", "committing", "committed"]
    backup_path: str
    targets: list[MigrationTargetState]

    @field_validator("backup_path")
    @classmethod
    def validate_backup_path(cls, value: str) -> str:
        return _validate_state_relative_path(value)

    @model_validator(mode="after")
    def validate_transaction_identity(self) -> "MigrationTransactionState":
        expected_backup = f".migration_backup/{self.migration_id}"
        if self.backup_path != expected_backup:
            raise ValueError("migration backup path does not match migration ID")
        target_paths = [target.path for target in self.targets]
        if len(set(target_paths)) != len(target_paths):
            raise ValueError("migration transaction target paths must be unique")
        return self


def migrate_task(task_dir: str | Path) -> dict[str, Any]:
    root = Path(task_dir).resolve(strict=False)
    with task_lock(root, operation="migrate", reclaim_stale=True):
        _recover_interrupted_migration(root)
        return _migrate_task_locked(root)


def _migrate_task_locked(root: Path) -> dict[str, Any]:
    manifest_path = root / "run_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    outputs = manifest.get("outputs", {})
    if not isinstance(outputs, dict):
        raise ValueError("migration manifest outputs must be an object")
    artifact_map = _migration_artifact_map(root, outputs)

    blocks_path = artifact_map["blocks"]
    evidence_path = artifact_map["evidence_index"]
    quote_matches_path = artifact_map["quote_matches"]
    if not blocks_path.exists():
        raise ValueError(f"migration blocks artifact not found: {blocks_path}")
    if not evidence_path.exists():
        raise ValueError(f"migration evidence artifact not found: {evidence_path}")

    blocks = BlockListAdapter.validate_python(read_json(blocks_path))
    evidence_index, evidence_from, old_evidence_fingerprint = (
        _load_migrated_evidence(evidence_path, blocks)
    )
    evidence_policy = manifest.get("evidence", {}).get(
        "policy",
        "structured_if_available",
    )
    if evidence_policy not in EVIDENCE_POLICIES:
        raise ValueError(f"unsupported migration EvidencePolicy: {evidence_policy}")

    reqir_artifacts = _reqir_artifact_paths(artifact_map)
    if not reqir_artifacts:
        raise ValueError("migration found no ReqIR package artifacts")

    registry = QuoteMatchRegistry(schema_version="quote_matches_v3")
    migrated_packages: list[
        tuple[str, Path, dict[str, Any], list[RequirementIR], str | None]
    ] = []
    package_reports: list[dict[str, Any]] = []
    source_audit: list[dict[str, Any]] = []
    unique_source_identities: set[str] = set()
    rebound_source_pairs: set[tuple[str, str]] = set()
    bound_missing_source_keys: set[str] = set()

    for package_kind, path in reqir_artifacts:
        metadata, requirements, schema_version, old_sources = (
            _load_legacy_requirements(path)
        )
        try:
            canonicalize_source_cell_ids(requirements, evidence_index)
            package_registry = build_quote_match_registry(
                requirements,
                blocks,
                evidence_fingerprint=evidence_index.evidence_fingerprint,
                evidence_index=evidence_index,
            )
        except ValueError as exc:
            raise ValueError(f"REQIR_LEGACY_REENRICHMENT_FAILED: {path}") from exc
        registry.merge(package_registry)
        SourceEvidenceEnricher().enrich(
            requirements,
            evidence_index,
            package_registry,
            blocks,
        )
        quote_validated, quote_report = SourceQuoteValidator().validate(
            requirements,
            blocks,
            package_registry,
        )
        locator_validated, locator_report, locator_failures = (
            SourceLocatorValidator().validate(
                requirements,
                evidence_index,
                package_registry,
                policy=evidence_policy,
                document_blocks=blocks,
            )
        )
        strict = package_kind in STRICT_PACKAGE_KINDS
        if strict and (
            not quote_report.valid
            or not locator_report.valid
            or len(quote_validated) != len(requirements)
            or len(locator_validated) != len(requirements)
        ):
            raise ValueError(f"REQIR_LEGACY_REENRICHMENT_FAILED: {path}")

        artifact_path = _relative_artifact_path(root, path).as_posix()
        package_reports.append(
            {
                "artifact_path": artifact_path,
                "package_kind": package_kind,
                "strict_validation_required": strict,
                "requirement_count": len(requirements),
                "quote_validated_count": len(quote_validated),
                "locator_validated_count": len(locator_validated),
                "quote_report": quote_report.model_dump(mode="json"),
                "locator_report": locator_report.model_dump(mode="json"),
                "locator_failures": locator_failures,
            }
        )
        new_sources = [
            (requirement.id, source_index, source)
            for requirement in requirements
            for source_index, source in enumerate(requirement.sources)
        ]
        if len(old_sources) != len(new_sources):
            raise ValueError(f"REQIR_LEGACY_REENRICHMENT_FAILED: {path}")
        for old_source, (requirement_id, source_index, source) in zip(
            old_sources,
            new_sources,
        ):
            new_key = source.source_evidence_key
            if new_key is None:
                raise ValueError(f"REQIR_LEGACY_REENRICHMENT_FAILED: {path}")
            unique_source_identities.add(new_key)
            old_key = old_source["old_source_evidence_key"]
            if isinstance(old_key, str) and old_key:
                if old_key != new_key:
                    rebound_source_pairs.add((old_key, new_key))
            else:
                bound_missing_source_keys.add(new_key)
            source_audit.append(
                {
                    "artifact_path": artifact_path,
                    "requirement_id": requirement_id,
                    "source_index": source_index,
                    "old_source_evidence_key": old_key,
                    "new_source_evidence_key": new_key,
                    "old_locator_status": old_source["old_locator_status"],
                    "new_locator_status": source.locator_status,
                    "old_match_status": old_source["old_match_status"],
                    "new_match_status": source.match_status,
                }
            )
        migrated_packages.append(
            (package_kind, path, metadata, requirements, schema_version)
        )

    migration_id = _migration_id()
    backup_relative = Path(".migration_backup") / migration_id
    statistics = {
        "processed_source_occurrences": len(source_audit),
        "unique_source_identities": len(unique_source_identities),
        "rebound_source_keys": len(rebound_source_pairs),
        "bound_missing_source_keys": len(bound_missing_source_keys),
    }
    migration_report_path = artifact_map["migration_report"]
    migration_report = {
        "schema_version": "migration_report_v1",
        "migration_id": migration_id,
        "backup_path": backup_relative.as_posix(),
        "evidence": {
            "schema_from": evidence_from,
            "schema_version": evidence_index.schema_version,
            "old_fingerprint": old_evidence_fingerprint,
            "new_fingerprint": evidence_index.evidence_fingerprint,
        },
        "statistics": statistics,
        "packages": package_reports,
        "sources": source_audit,
    }

    manifest_payload = dict(manifest)
    manifest_payload["migration"] = {
        "migration_id": migration_id,
        "migration_report": _relative_artifact_path(
            root,
            migration_report_path,
        ).as_posix(),
        "backup_path": backup_relative.as_posix(),
        "reqir_schema_version": "reqir_v4",
        "quote_matches_schema_version": "quote_matches_v3",
        "evidence_schema_from": evidence_from,
        "evidence_schema_version": "evidence_v5",
        **statistics,
    }
    evidence_metadata = dict(manifest_payload.get("evidence", {}))
    evidence_metadata.update(
        {
            "schema_version": evidence_index.schema_version,
            "evidence_fingerprint": evidence_index.evidence_fingerprint,
        }
    )
    manifest_payload["evidence"] = evidence_metadata

    payloads: dict[Path, Any] = {
        evidence_path: evidence_index.model_dump(mode="json"),
        quote_matches_path: registry.model_dump(mode="json"),
    }
    artifact_types: dict[Path, str] = {
        evidence_path: "evidence",
        quote_matches_path: "quote_matches",
    }
    exported_requirements: list[RequirementIR] | None = None
    for package_kind, path, metadata, requirements, _ in migrated_packages:
        payloads[path] = reqir_package_dump(requirements, metadata=metadata)
        artifact_types[path] = "reqir"
        if package_kind == "export":
            exported_requirements = requirements

    source_map_path = artifact_map["source_map"]
    if exported_requirements is not None:
        payloads[source_map_path] = build_source_map(exported_requirements)
        artifact_types[source_map_path] = "source_map"
    payloads[migration_report_path] = migration_report
    artifact_types[migration_report_path] = "migration_report"
    if manifest_path.exists():
        payloads[manifest_path] = manifest_payload
        artifact_types[manifest_path] = "manifest"

    _stage_and_commit(
        root,
        migration_id=migration_id,
        backup_relative=backup_relative,
        payloads=payloads,
        artifact_types=artifact_types,
        manifest_path=manifest_path if manifest_path.exists() else None,
    )

    return {
        "task_dir": root.as_posix(),
        "reqir_packages": len(migrated_packages),
        **statistics,
        "migration_report": migration_report_path.as_posix(),
        "backup_path": (root / backup_relative).as_posix(),
        "evidence_schema_from": evidence_from,
        "evidence_schema_version": evidence_index.schema_version,
        "quote_matches_schema_version": registry.schema_version,
    }


def _load_migrated_evidence(
    path: Path,
    blocks: list[DocumentBlock],
) -> tuple[EvidenceIndex, str, str | None]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("migration evidence artifact must be an object")
    schema_version = payload.get("schema_version")
    old_fingerprint = payload.get("evidence_fingerprint")
    if schema_version == "evidence_v4":
        candidate = {
            **payload,
            "schema_version": "evidence_v5",
            "evidence_fingerprint": "0" * 64,
        }
        try:
            index = EvidenceIndex.model_validate(candidate)
        except (ValidationError, ValueError) as exc:
            raise ValueError("EVIDENCE_V4_REPARSE_REQUIRED") from exc
        index = finalize_evidence_fingerprint(index)
    elif schema_version == "evidence_v5":
        index = EvidenceIndex.model_validate(payload)
        validate_evidence_fingerprint(index)
    else:
        raise ValueError(
            f"unsupported evidence schema version for migration: {schema_version}"
        )

    parsed_document = ParsedDocument(
        document_id=index.document_id,
        document_name=index.document_name,
        source_format=index.source_format,
        parser_name=index.parser_identity.parser_name,
        text="\n\n".join(block.text for block in blocks),
        blocks=blocks,
        parser_identity=index.parser_identity,
    )
    try:
        validate_evidence_index_against_parsed_document(index, parsed_document)
    except ValueError as exc:
        if schema_version == "evidence_v4":
            raise ValueError("EVIDENCE_V4_REPARSE_REQUIRED") from exc
        raise
    return index, str(schema_version), (
        str(old_fingerprint) if old_fingerprint is not None else None
    )


def _load_legacy_requirements(
    path: Path,
) -> tuple[
    dict[str, Any],
    list[RequirementIR],
    str | None,
    list[dict[str, Any]],
]:
    payload = read_json(path)
    if isinstance(payload, dict):
        schema_version = payload.get("schema_version")
        metadata = payload.get("metadata", {})
        items = payload.get("items")
    elif isinstance(payload, list):
        schema_version = None
        metadata = {}
        items = payload
    else:
        raise ValueError(f"migration ReqIR payload must be an object or list: {path}")
    if schema_version not in SUPPORTED_REQIR_VERSIONS:
        raise ValueError(f"unsupported ReqIR schema version for migration: {schema_version}")
    if not isinstance(metadata, dict) or not isinstance(items, list):
        raise ValueError(f"migration ReqIR package is malformed: {path}")

    prepared_items: list[dict[str, Any]] = []
    old_sources: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"migration ReqIR item must be an object: {path}")
        prepared = dict(item)
        raw_sources = prepared.get("sources", [])
        if not isinstance(raw_sources, list):
            raise ValueError(f"migration ReqIR sources must be a list: {path}")
        prepared_sources = []
        for source in raw_sources:
            prepared_source, snapshot = _prepare_legacy_source(source, path)
            prepared_sources.append(prepared_source)
            old_sources.append(snapshot)
        prepared["sources"] = prepared_sources
        prepared_items.append(prepared)
    try:
        requirements = [RequirementIR.model_validate(item) for item in prepared_items]
    except ValidationError as exc:
        raise ValueError(f"REQIR_LEGACY_REENRICHMENT_FAILED: {path}") from exc
    return metadata, requirements, schema_version, old_sources


def _prepare_legacy_source(
    source: object,
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(source, dict):
        raise ValueError(f"migration ReqIR source must be an object: {path}")
    prepared = dict(source)
    snapshot = {
        "old_source_evidence_key": prepared.get("source_evidence_key"),
        "old_locator_status": prepared.get("locator_status"),
        "old_match_status": prepared.get("match_status"),
    }
    table_locator = prepared.get("table_locator")
    locator = table_locator if isinstance(table_locator, dict) else {}
    cell_ids = (
        prepared.get("source_cell_ids_raw")
        or prepared.get("canonical_source_cell_ids")
        or locator.get("cell_ids")
        or []
    )
    source_row = prepared.get("source_table_row_index")
    if source_row is None:
        source_row = locator.get("selected_row_index")
    if cell_ids and source_row is None:
        raise ValueError("REQIR_LEGACY_TABLE_SOURCE_REQUIRES_REENRICHMENT")
    if source_row is not None and not cell_ids:
        raise ValueError("REQIR_LEGACY_TABLE_SOURCE_REQUIRES_REENRICHMENT")

    prepared["source_cell_ids_raw"] = list(cell_ids)
    prepared["canonical_source_cell_ids"] = list(cell_ids)
    prepared["source_table_row_index"] = source_row
    prepared["source_evidence_key"] = None
    prepared["match_status"] = "UNVERIFIED"
    prepared["match_score"] = None
    prepared["text_locator"] = None
    prepared["page_locator"] = None
    prepared["table_locator"] = None
    prepared["provisional_text_locator"] = None
    prepared["locator_status"] = "UNVERIFIED"
    prepared["capability_results"] = []
    prepared["locator_score"] = None
    return prepared, snapshot


def _reqir_artifact_paths(
    artifact_map: dict[str, Path],
) -> list[tuple[str, Path]]:
    candidates = [
        ("raw", artifact_map["reqir_raw"]),
        ("validated", artifact_map["reqir_validated"]),
        ("quarantined", artifact_map["reqir_quarantined"]),
        ("export", artifact_map["reqir_export"]),
    ]
    return [(package_kind, path) for package_kind, path in candidates if path.exists()]


def _migration_artifact_map(
    root: Path,
    outputs: dict[str, Any],
) -> dict[str, Path]:
    defaults = {
        "blocks": "parsed/blocks.json",
        "evidence_index": "parsed/evidence_index.json",
        "quote_matches": "extracted/quote_matches.json",
        "reqir_raw": "extracted/reqir.raw.json",
        "reqir_validated": "extracted/reqir.validated.json",
        "reqir_quarantined": "extracted/reqir.quarantined.json",
        "reqir_export": "exports/reqir.json",
        "source_map": "extracted/source_map.json",
    }
    artifact_map = {
        key: _artifact_path(root, outputs, key, default)
        for key, default in defaults.items()
    }
    artifact_map["manifest"] = _path_within_root(root, root / "run_manifest.json")
    artifact_map["migration_report"] = _path_within_root(
        root,
        root / "migration" / "migration_report.json",
    )
    owners: dict[Path, str] = {}
    for logical_name, path in artifact_map.items():
        existing = owners.get(path)
        if existing is not None:
            raise ValueError(
                "MIGRATION_ARTIFACT_PATH_COLLISION: "
                f"{existing} and {logical_name} resolve to {path}"
            )
        owners[path] = logical_name
    return artifact_map


def _stage_and_commit(
    root: Path,
    *,
    migration_id: str,
    backup_relative: Path,
    payloads: dict[Path, Any],
    artifact_types: dict[Path, str],
    manifest_path: Path | None,
) -> None:
    staging_root = _path_within_root(root, root / ".migration_tmp")
    preparation_root = _path_within_root(
        root,
        root / f".migration_prepare_{migration_id}",
    )
    prepared_files_root = _path_within_root(
        preparation_root,
        preparation_root / "files",
    )
    prepared_backup_root = _path_within_root(
        preparation_root,
        preparation_root / "backup" / "files",
    )
    backup_base = _path_within_root(root, root / backup_relative)
    if staging_root.exists() or preparation_root.exists():
        raise ValueError("MIGRATION_TRANSACTION_STATE_EXISTS")
    preparation_root.mkdir(parents=True)
    (preparation_root / "backup").mkdir()

    target_records: list[dict[str, Any]] = []
    ordered_targets = [path for path in payloads if path != manifest_path]
    if manifest_path is not None and manifest_path in payloads:
        ordered_targets.append(manifest_path)
    try:
        for target in ordered_targets:
            relative = _relative_artifact_path(root, target)
            staged_path = prepared_files_root / relative
            _write_json_durable(staged_path, payloads[target])
            _verify_staged_artifact(staged_path, artifact_types[target])
            existed = target.exists()
            if existed:
                backup_path = prepared_backup_root / relative
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_path)
                _fsync_file(backup_path)
                _fsync_directory(backup_path.parent)
            target_records.append(
                {
                    "path": relative.as_posix(),
                    "artifact_type": artifact_types[target],
                    "existed": existed,
                }
            )

        _fsync_directory_tree(preparation_root)
        state = {
            "schema_version": "migration_transaction_v1",
            "migration_id": migration_id,
            "status": "prepared",
            "backup_path": backup_relative.as_posix(),
            "targets": target_records,
        }
        _write_state_atomic(preparation_root / "state.json", state)
        preparation_root.replace(staging_root)
        _fsync_directory(root)
    except Exception:
        shutil.rmtree(preparation_root, ignore_errors=True)
        raise

    staged_files_root = _path_within_root(staging_root, staging_root / "files")
    state_path = staging_root / "state.json"
    state["status"] = "committing"
    _write_state_atomic(state_path, state)
    try:
        for record in target_records:
            relative = Path(record["path"])
            source = staged_files_root / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace_staged_file(source, target)
            _fsync_directory(target.parent)
        prepared_backup_base = staging_root / "backup"
        backup_base.parent.mkdir(parents=True, exist_ok=True)
        if backup_base.exists():
            raise ValueError("MIGRATION_BACKUP_ALREADY_EXISTS")
        prepared_backup_base.replace(backup_base)
        _fsync_directory(backup_base.parent)
    except Exception as exc:
        raise ValueError("MIGRATION_COMMIT_INTERRUPTED") from exc

    state["status"] = "committed"
    _write_state_atomic(state_path, state)
    shutil.rmtree(staging_root)
    _fsync_directory(root)


def _recover_interrupted_migration(root: Path) -> None:
    staging_root = _path_within_root(root, root / ".migration_tmp")
    state_path = staging_root / "state.json"
    if not staging_root.exists():
        return
    if not state_path.exists():
        shutil.rmtree(staging_root)
        _fsync_directory(root)
        return
    try:
        state = MigrationTransactionState.model_validate(read_json(state_path))
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError("MIGRATION_RECOVERY_STATE_INVALID") from exc
    if state.status == "committed":
        shutil.rmtree(staging_root)
        _fsync_directory(root)
        return

    backup_root = _path_within_root(root, root / state.backup_path)
    internal_backup_files_root = _path_within_root(
        staging_root,
        staging_root / "backup" / "files",
    )
    external_backup_files_root = _path_within_root(
        backup_root,
        backup_root / "files",
    )
    backup_files_root = (
        internal_backup_files_root
        if internal_backup_files_root.exists()
        else external_backup_files_root
    )
    uses_internal_backup = backup_files_root == internal_backup_files_root
    restore_root = _path_within_root(staging_root, staging_root / "restore")
    for record in state.targets:
        relative = Path(record.path)
        target = _path_within_root(root, root / relative)
        if record.existed:
            backup = _path_within_root(
                backup_files_root,
                backup_files_root / relative,
            )
            if not backup.exists():
                raise ValueError("MIGRATION_RECOVERY_BACKUP_MISSING")
            restore = _path_within_root(restore_root, restore_root / relative)
            restore.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, restore)
            target.parent.mkdir(parents=True, exist_ok=True)
            restore.replace(target)
            _fsync_directory(target.parent)
        elif target.exists():
            target.unlink()
            _fsync_directory(target.parent)
    if uses_internal_backup:
        backup_root.parent.mkdir(parents=True, exist_ok=True)
        if backup_root.exists():
            raise ValueError("MIGRATION_RECOVERY_BACKUP_COLLISION")
        (staging_root / "backup").replace(backup_root)
        _fsync_directory(backup_root.parent)
    shutil.rmtree(staging_root)
    _fsync_directory(root)


def _verify_staged_artifact(path: Path, artifact_type: str) -> None:
    payload = read_json(path)
    if artifact_type == "evidence":
        index = EvidenceIndex.model_validate(payload)
        validate_evidence_fingerprint(index)
    elif artifact_type == "quote_matches":
        QuoteMatchRegistry.model_validate(payload)
    elif artifact_type == "reqir":
        ReqIRPackage.model_validate(payload)
    elif artifact_type == "migration_report":
        if not isinstance(payload, dict) or payload.get("schema_version") != (
            "migration_report_v1"
        ):
            raise ValueError("staged migration report is invalid")
    elif artifact_type in {"source_map", "manifest"}:
        if not isinstance(payload, dict):
            raise ValueError(f"staged {artifact_type} artifact is invalid")
    else:
        raise ValueError(f"unknown staged migration artifact type: {artifact_type}")


def _write_state_atomic(path: Path, payload: dict[str, Any]) -> None:
    validated = MigrationTransactionState.model_validate(payload)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(validated.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    _fsync_directory(path.parent)


def _write_json_durable(path: Path, payload: Any) -> None:
    write_json(path, payload)
    _fsync_file(path)
    _fsync_directory(path.parent)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory_tree(root: Path) -> None:
    directories = [root, *(path for path in root.rglob("*") if path.is_dir())]
    for directory in sorted(
        directories,
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _replace_staged_file(source: Path, target: Path) -> None:
    source.replace(target)


def _relative_artifact_path(root: Path, path: Path) -> Path:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"migration artifact is outside task directory: {path}") from exc


def _path_within_root(root: Path, path: Path) -> Path:
    resolved_root = root.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("MIGRATION_RECOVERY_PATH_OUTSIDE_TASK") from exc
    return resolved_path


def _validate_state_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("migration transaction path must be a non-empty string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value in {".", "./"}:
        raise ValueError("migration transaction path must remain inside the task")
    normalized = path.as_posix()
    if normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError("migration transaction path must remain inside the task")
    return normalized


def _migration_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}_{uuid4().hex[:8]}"


def _artifact_path(
    root: Path,
    outputs: dict[str, Any],
    key: str,
    default: str,
) -> Path:
    value = outputs.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"manifest output path must be a string: {key}")
    path = root / value
    return _path_within_root(root, path)
