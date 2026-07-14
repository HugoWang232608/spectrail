from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from spectrail.core.io import read_json, reqir_package_dump, write_json
from spectrail.core.models import DocumentBlock, RequirementIR
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
from spectrail.validators.source_locator_validator import SourceLocatorValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator


BlockListAdapter = TypeAdapter(list[DocumentBlock])
SUPPORTED_REQIR_VERSIONS = {None, "reqir_v1", "reqir_v2", "reqir_v3", "reqir_v4"}


def migrate_task(task_dir: str | Path) -> dict[str, Any]:
    root = Path(task_dir)
    manifest_path = root / "run_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    outputs = manifest.get("outputs", {})

    blocks_path = _artifact_path(root, outputs, "blocks", "parsed/blocks.json")
    evidence_path = _artifact_path(
        root,
        outputs,
        "evidence_index",
        "parsed/evidence_index.json",
    )
    quote_matches_path = _artifact_path(
        root,
        outputs,
        "quote_matches",
        "extracted/quote_matches.json",
    )
    if not blocks_path.exists():
        raise ValueError(f"migration blocks artifact not found: {blocks_path}")
    if not evidence_path.exists():
        raise ValueError(f"migration evidence artifact not found: {evidence_path}")

    blocks = BlockListAdapter.validate_python(read_json(blocks_path))
    evidence_index, evidence_from = _load_migrated_evidence(
        evidence_path,
        blocks,
    )
    evidence_policy = manifest.get("evidence", {}).get(
        "policy",
        "structured_if_available",
    )

    reqir_paths = _reqir_artifact_paths(root, outputs)
    if not reqir_paths:
        raise ValueError("migration found no ReqIR package artifacts")

    migrated_packages: list[tuple[Path, dict[str, Any], list[RequirementIR], str | None]] = []
    registry = QuoteMatchRegistry(schema_version="quote_matches_v3")
    rebound_source_count = 0
    for path in reqir_paths:
        metadata, requirements, schema_version, source_count = _load_legacy_requirements(
            path
        )
        rebound_source_count += source_count
        canonicalize_source_cell_ids(requirements, evidence_index)
        package_registry = build_quote_match_registry(
            requirements,
            blocks,
            evidence_fingerprint=evidence_index.evidence_fingerprint,
            evidence_index=evidence_index,
        )
        registry.merge(package_registry)
        SourceEvidenceEnricher().enrich(
            requirements,
            evidence_index,
            package_registry,
            blocks,
        )
        SourceQuoteValidator().validate(requirements, blocks, package_registry)
        SourceLocatorValidator().validate(
            requirements,
            evidence_index,
            package_registry,
            policy=evidence_policy,
            document_blocks=blocks,
        )
        migrated_packages.append((path, metadata, requirements, schema_version))

    write_json(evidence_path, evidence_index.model_dump(mode="json"))
    write_json(quote_matches_path, registry.model_dump(mode="json"))
    exported_requirements: list[RequirementIR] | None = None
    for path, metadata, requirements, _ in migrated_packages:
        write_json(path, reqir_package_dump(requirements, metadata=metadata))
        if path == _artifact_path(
            root,
            outputs,
            "reqir_export",
            "exports/reqir.json",
        ):
            exported_requirements = requirements

    source_map_path = _artifact_path(
        root,
        outputs,
        "source_map",
        "extracted/source_map.json",
    )
    if exported_requirements is not None:
        write_json(source_map_path, build_source_map(exported_requirements))

    if manifest_path.exists():
        manifest["migration"] = {
            "reqir_schema_version": "reqir_v4",
            "quote_matches_schema_version": "quote_matches_v3",
            "evidence_schema_from": evidence_from,
            "evidence_schema_version": "evidence_v5",
            "rebound_source_count": rebound_source_count,
        }
        evidence_metadata = dict(manifest.get("evidence", {}))
        evidence_metadata.update(
            {
                "schema_version": evidence_index.schema_version,
                "evidence_fingerprint": evidence_index.evidence_fingerprint,
            }
        )
        manifest["evidence"] = evidence_metadata
        write_json(manifest_path, manifest)

    return {
        "task_dir": root.as_posix(),
        "reqir_packages": len(migrated_packages),
        "rebound_sources": rebound_source_count,
        "evidence_schema_from": evidence_from,
        "evidence_schema_version": evidence_index.schema_version,
        "quote_matches_schema_version": registry.schema_version,
    }


def _load_migrated_evidence(
    path: Path,
    blocks: list[DocumentBlock],
) -> tuple[EvidenceIndex, str]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("migration evidence artifact must be an object")
    schema_version = payload.get("schema_version")
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
    return index, str(schema_version)


def _load_legacy_requirements(
    path: Path,
) -> tuple[dict[str, Any], list[RequirementIR], str | None, int]:
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
    source_count = 0
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"migration ReqIR item must be an object: {path}")
        prepared = dict(item)
        raw_sources = prepared.get("sources", [])
        if not isinstance(raw_sources, list):
            raise ValueError(f"migration ReqIR sources must be a list: {path}")
        prepared["sources"] = [
            _prepare_legacy_source(source, path) for source in raw_sources
        ]
        source_count += len(raw_sources)
        prepared_items.append(prepared)
    try:
        requirements = [RequirementIR.model_validate(item) for item in prepared_items]
    except ValidationError as exc:
        raise ValueError(f"REQIR_LEGACY_REENRICHMENT_FAILED: {path}") from exc
    return metadata, requirements, schema_version, source_count


def _prepare_legacy_source(source: object, path: Path) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValueError(f"migration ReqIR source must be an object: {path}")
    prepared = dict(source)
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
    return prepared


def _reqir_artifact_paths(root: Path, outputs: dict[str, Any]) -> list[Path]:
    candidates = [
        _artifact_path(root, outputs, "reqir_raw", "extracted/reqir.raw.json"),
        _artifact_path(
            root,
            outputs,
            "reqir_validated",
            "extracted/reqir.validated.json",
        ),
        _artifact_path(
            root,
            outputs,
            "reqir_quarantined",
            "extracted/reqir.quarantined.json",
        ),
        _artifact_path(root, outputs, "reqir_export", "exports/reqir.json"),
    ]
    result: list[Path] = []
    for path in candidates:
        if path.exists() and path not in result:
            result.append(path)
    return result


def _artifact_path(
    root: Path,
    outputs: dict[str, Any],
    key: str,
    default: str,
) -> Path:
    value = outputs.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"manifest output path must be a string: {key}")
    return root / value
