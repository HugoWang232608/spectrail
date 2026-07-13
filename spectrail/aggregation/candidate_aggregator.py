from __future__ import annotations

from dataclasses import dataclass, field
from math import inf
from typing import Any

from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan
from spectrail.llm.fingerprints import sha256_hex
from spectrail.validators.source_quote_validator import normalize_text


@dataclass
class AggregationResult:
    requirements: list[RequirementIR]
    duplicate_groups: list[dict[str, Any]] = field(default_factory=list)
    collapsed_exact_candidates: int = 0
    field_conflict_count: int = 0


class CandidateAggregator:
    conflict_fields = (
        "type",
        "ears_pattern",
        "subject",
        "condition",
        "response",
        "priority",
        "verification_method",
    )

    def aggregate(
        self, candidates: list[RequirementIR], blocks: list[DocumentBlock], *, document_id: str = "doc_001"
    ) -> AggregationResult:
        by_key: dict[str, list[RequirementIR]] = {}
        for candidate in candidates:
            key = candidate_key(candidate, document_id=document_id)
            candidate.metadata["candidate_key"] = key
            by_key.setdefault(key, []).append(candidate)

        block_map = {block.block_id: block for block in blocks}
        merged: list[RequirementIR] = []
        collapsed = 0
        field_conflict_count = 0
        for key in sorted(by_key):
            variants = sorted(by_key[key], key=lambda item: _candidate_sort_key(item, block_map))
            canonical = variants[0].model_copy(deep=True)
            collapsed += len(variants) - 1
            canonical.confidence = max(item.confidence for item in variants)
            canonical.tags = _stable_union(item.tags for item in variants)
            canonical.sources = _merge_sources(variants)
            canonical.metadata["source_chunk_ids"] = sorted(
                {str(item.metadata.get("chunk_id")) for item in variants if item.metadata.get("chunk_id")}
            )
            canonical.metadata["aggregation_variants"] = [_variant(item) for item in variants]
            resolutions, conflicts = self._resolve_fields(canonical, variants)
            if resolutions:
                canonical.metadata["field_resolutions"] = resolutions
            if conflicts:
                canonical.metadata["field_conflicts"] = conflicts
                canonical.review_status = "needs_recheck"
                field_conflict_count += len(conflicts)
            merged.append(canonical)

        merged.sort(key=lambda item: _candidate_sort_key(item, block_map))
        for index, requirement in enumerate(merged, start=1):
            requirement.id = f"REQ-{index:04d}"

        duplicate_groups = self._build_duplicate_groups(merged)
        return AggregationResult(
            requirements=merged,
            duplicate_groups=duplicate_groups,
            collapsed_exact_candidates=collapsed,
            field_conflict_count=field_conflict_count,
        )

    def _resolve_fields(
        self, canonical: RequirementIR, variants: list[RequirementIR]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        resolutions = []
        conflicts = []
        for name in self.conflict_fields:
            concrete: list[tuple[str | None, Any]] = []
            for item in variants:
                value = getattr(item, name)
                if value not in {None, "unknown", ""}:
                    concrete.append((item.metadata.get("chunk_id"), value))
            distinct = []
            for _, value in concrete:
                if value not in distinct:
                    distinct.append(value)
            if len(distinct) == 1:
                current = getattr(canonical, name)
                if current in {None, "unknown", ""}:
                    setattr(canonical, name, distinct[0])
                    resolutions.append({"field": name, "value": distinct[0], "sources": concrete})
            elif len(distinct) > 1:
                conflicts.append(
                    {
                        "field": name,
                        "variants": [
                            {"chunk_id": chunk_id, "value": value} for chunk_id, value in concrete
                        ],
                    }
                )
        return resolutions, conflicts

    @staticmethod
    def _build_duplicate_groups(requirements: list[RequirementIR]) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        by_statement: dict[str, list[RequirementIR]] = {}
        by_source: dict[tuple, list[RequirementIR]] = {}
        for item in requirements:
            by_statement.setdefault(normalize_text(item.statement), []).append(item)
            for source in item.sources:
                by_source.setdefault(_source_identity(source), []).append(item)

        seen: set[tuple[str, tuple[str, ...]]] = set()
        for reason, mapping in (
            ("normalized_statement_equal", by_statement),
            ("same_source_divergent_statement", by_source),
        ):
            for members in mapping.values():
                ids = tuple(sorted({item.id for item in members}))
                if len(ids) < 2 or (reason, ids) in seen:
                    continue
                if reason == "same_source_divergent_statement" and len(
                    {normalize_text(item.statement) for item in members}
                ) < 2:
                    continue
                seen.add((reason, ids))
                group_id = f"DUP-{len(groups) + 1:04d}"
                for item in members:
                    item.duplicate_group_id = item.duplicate_group_id or group_id
                    item.possible_duplicate_ids = sorted(identifier for identifier in ids if identifier != item.id)
                groups.append({"group_id": group_id, "reason": reason, "requirement_ids": list(ids)})
        return groups


def candidate_key(candidate: RequirementIR, *, document_id: str) -> str:
    value = {
        "document_id": document_id,
        "sources": sorted(_source_identity(source) for source in candidate.sources),
        "statement": normalize_text(candidate.statement),
    }
    return f"cand_{sha256_hex(value)[:16]}"


def _candidate_sort_key(candidate: RequirementIR, block_map: dict[str, DocumentBlock]) -> tuple:
    source_keys = [_source_sort_key(source, block_map) for source in candidate.sources]
    source_key = min(source_keys) if source_keys else (inf, inf, "\uffff", "\uffff")
    return (
        *source_key,
        int(candidate.metadata.get("chunk_index", 10**12)),
        int(candidate.metadata.get("local_item_index", 10**12)),
        str(candidate.metadata.get("candidate_key", "")),
    )


def _source_sort_key(source: SourceSpan, block_map: dict[str, DocumentBlock]) -> tuple:
    block = block_map.get(source.block_id)
    if block is None:
        return (
            inf,
            inf,
            source.block_id,
            normalize_text(source.quote),
            tuple(source.canonical_source_cell_ids),
        )
    exact = block.text.find(source.quote)
    if exact >= 0:
        offset = exact
    else:
        normalized_quote = normalize_text(source.quote)
        normalized_block = normalize_text(block.text)
        normalized = normalized_block.find(normalized_quote) if normalized_quote else -1
        offset = normalized if normalized >= 0 else inf
    return (
        block.order,
        offset,
        source.block_id,
        normalize_text(source.quote),
        tuple(source.canonical_source_cell_ids),
    )


def _stable_union(values: Any) -> list[str]:
    return sorted({value for group in values for value in group})


def _merge_sources(variants: list[RequirementIR]) -> list[SourceSpan]:
    result: list[SourceSpan] = []
    seen = set()
    for item in variants:
        for source in item.sources:
            key = _source_identity(source)
            if key not in seen:
                seen.add(key)
                result.append(source.model_copy(deep=True))
    return result


def _variant(item: RequirementIR) -> dict[str, Any]:
    return {
        "chunk_id": item.metadata.get("chunk_id"),
        "local_item_index": item.metadata.get("local_item_index"),
        "type": item.type,
        "ears_pattern": item.ears_pattern,
        "subject": item.subject,
        "condition": item.condition,
        "response": item.response,
        "priority": item.priority,
        "verification_method": item.verification_method,
        "confidence": item.confidence,
        "tags": list(item.tags),
        "sources": [
            {
                "block_id": source.block_id,
                "source_cell_ids_raw": list(source.source_cell_ids_raw),
                "canonical_source_cell_ids": list(
                    source.canonical_source_cell_ids
                ),
            }
            for source in item.sources
        ],
    }


def _source_identity(source: SourceSpan) -> tuple:
    return (
        source.document_id,
        source.block_id,
        normalize_text(source.quote),
        tuple(source.canonical_source_cell_ids),
    )
