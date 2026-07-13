from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from pydantic import ValidationError

from spectrail.core.ids import requirement_id
from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan
from spectrail.llm.errors import ModelPayloadContractError


TYPE_ALIASES = {
    "functional": "functional",
    "non_functional": "non_functional",
    "non-functional": "non_functional",
    "nonfunctional": "non_functional",
    "interface": "interface",
    "constraint": "constraint",
    "business": "business",
    "unknown": "unknown",
}
EARS_PATTERN_ALIASES = {
    "ubiquitous": "ubiquitous",
    "event_driven": "event_driven",
    "event-driven": "event_driven",
    "event": "event_driven",
    "state_driven": "state_driven",
    "state-driven": "state_driven",
    "state": "state_driven",
    "optional": "optional",
    "unwanted": "unwanted_behavior",
    "unwanted_behavior": "unwanted_behavior",
    "unwanted_behaviour": "unwanted_behavior",
    "unwanted-behavior": "unwanted_behavior",
    "unwanted-behaviour": "unwanted_behavior",
    "unknown": "unknown",
}
PRIORITY_ALIASES = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "unknown": "unknown",
}
VERIFICATION_METHOD_ALIASES = {
    "test": "test",
    "inspection": "inspection",
    "analysis": "analysis",
    "demonstration": "demonstration",
    "unknown": "unknown",
}
CONFIDENCE_ALIASES = {
    "high": 0.9,
    "medium": 0.6,
    "low": 0.3,
    "unknown": 0.0,
}
CELL_ID_RE = re.compile(r"^cell_\d{8}_r\d{4}_c\d{4}$")


@dataclass(frozen=True)
class RejectedModelItem:
    chunk_id: str | None
    item_index: int
    raw_item: Any
    error_code: str
    error_message: str


@dataclass(frozen=True)
class ExtractionBatchResult:
    accepted_candidates: list[RequirementIR]
    rejected_items: list[RejectedModelItem]


class ReqIRExtractor:
    extractor_version = "reqir_extractor_v1"

    def extract(
        self,
        payload: dict[str, Any],
        blocks: list[DocumentBlock],
        document_name: str,
        model_mode: str = "mock",
    ) -> list[RequirementIR]:
        result = self.extract_batch(
            payload=payload,
            blocks=blocks,
            document_name=document_name,
            model_mode=model_mode,
        )
        if result.rejected_items:
            first = result.rejected_items[0]
            raise ValueError(first.error_message)
        return result.accepted_candidates

    def extract_batch(
        self,
        payload: dict[str, Any],
        blocks: list[DocumentBlock],
        document_name: str,
        model_mode: str = "mock",
        *,
        chunk_id: str | None = None,
        chunk_fingerprint: str | None = None,
        request_fingerprint: str | None = None,
        context_block_ids: set[str] | None = None,
    ) -> ExtractionBatchResult:
        if not isinstance(payload, dict):
            raise ModelPayloadContractError("model output is not a JSON object")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ModelPayloadContractError("model output must contain an items array")

        by_id = {block.block_id: block for block in blocks}
        requirements: list[RequirementIR] = []
        rejected: list[RejectedModelItem] = []
        for index, item in enumerate(items, start=1):
            try:
                requirement = self._item_to_requirement(
                    item=item,
                    index=index,
                    by_id=by_id,
                    document_name=Path(document_name).name,
                    model_mode=model_mode,
                    context_block_ids=context_block_ids or set(),
                )
            except (TypeError, ValueError) as exc:
                rejected.append(
                    RejectedModelItem(
                        chunk_id=chunk_id,
                        item_index=index - 1,
                        raw_item=item,
                        error_code=_item_error_code(exc),
                        error_message=str(exc),
                    )
                )
                continue
            if chunk_id:
                requirement.id = f"CAND-{chunk_id}-{index:04d}"
                requirement.metadata.update(
                    {
                        "chunk_id": chunk_id,
                        "chunk_fingerprint": chunk_fingerprint,
                        "request_fingerprint": request_fingerprint,
                        "local_item_index": index - 1,
                        "extractor_version": "reqir_extractor_v2",
                    }
                )
            requirements.append(requirement)
        return ExtractionBatchResult(accepted_candidates=requirements, rejected_items=rejected)

    def _item_to_requirement(
        self,
        item: Any,
        index: int,
        by_id: dict[str, DocumentBlock],
        document_name: str,
        model_mode: str,
        context_block_ids: set[str],
    ) -> RequirementIR:
        if not isinstance(item, dict):
            raise ValueError(f"item {index} is not a JSON object")
        for field in ("statement", "source_block_id", "source_quote"):
            if not item.get(field):
                raise ValueError(f"item {index} missing required field: {field}")
        if str(item["source_block_id"]) in context_block_ids:
            raise ValueError(f"item {index} cites a context-only block")
        field_normalizations: list[dict[str, str]] = []
        confidence = _normalize_confidence(item.get("confidence", 0.0), field_normalizations)
        requirement_type = _normalize_enum(
            item.get("type", "unknown"),
            TYPE_ALIASES,
            "type",
            field_normalizations,
        )
        ears_pattern = _normalize_enum(
            item.get("ears_pattern", "unknown"),
            EARS_PATTERN_ALIASES,
            "ears_pattern",
            field_normalizations,
        )
        priority = _normalize_enum(
            item.get("priority", "unknown"),
            PRIORITY_ALIASES,
            "priority",
            field_normalizations,
        )
        verification_method = _normalize_enum(
            item.get("verification_method", "unknown"),
            VERIFICATION_METHOD_ALIASES,
            "verification_method",
            field_normalizations,
        )
        source_quote = _normalize_source_quote(str(item["source_quote"]), field_normalizations)
        source_cell_ids_raw = _source_cell_ids(item.get("source_cell_ids"), index)

        source_block_id = str(item["source_block_id"])
        source_block = by_id.get(source_block_id)
        if source_cell_ids_raw and (source_block is None or source_block.type != "table"):
            raise ValueError(
                f"item {index} provides source_cell_ids for a non-table block"
            )
        section_path = list(source_block.section_path) if source_block else []
        section = " > ".join(section_path) if section_path else None
        source = SourceSpan(
            document_id=source_block.document_id if source_block else "doc_001",
            document_name=document_name,
            page=source_block.page if source_block else None,
            section=section,
            section_path=section_path,
            block_id=source_block_id,
            quote=source_quote,
            source_cell_ids_raw=source_cell_ids_raw,
        )
        metadata = {
            "source_block_id": source_block_id,
            "model_mode": model_mode,
            "extractor_version": self.extractor_version,
            "raw_item_index": index - 1,
        }
        if field_normalizations:
            metadata["field_normalizations"] = field_normalizations
        review_status = "needs_recheck" if _has_unknown_normalization(field_normalizations) else "pending"

        try:
            return RequirementIR(
                id=requirement_id(index),
                title=item.get("title"),
                type=requirement_type,
                ears_pattern=ears_pattern,
                statement=str(item["statement"]),
                subject=item.get("subject"),
                condition=item.get("condition"),
                response=item.get("response"),
                priority=priority,
                verification_method=verification_method,
                sources=[source],
                confidence=confidence,
                review_status=review_status,
                tags=_normalize_tags(item.get("tags", [])),
                metadata=metadata,
            )
        except ValidationError as exc:
            raise ValueError(f"item {index} failed ReqIR schema validation: {exc}") from exc


def _normalize_enum(
    value: Any,
    aliases: dict[str, str],
    field: str,
    field_normalizations: list[dict[str, str]],
) -> str:
    raw = str(value or "unknown")
    normalized_key = raw.strip().lower().replace(" ", "_")
    normalized = aliases.get(normalized_key, "unknown")
    if normalized != raw:
        field_normalizations.append({"field": field, "input": raw, "normalized": normalized})
    return normalized


def _source_cell_ids(value: Any, item_index: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"item {item_index} source_cell_ids must be a string list")
    if len(set(value)) != len(value):
        raise ValueError(f"item {item_index} source_cell_ids must be unique")
    if any(not CELL_ID_RE.fullmatch(item) for item in value):
        raise ValueError(f"item {item_index} source_cell_ids contain an invalid cell ID")
    return list(value)


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(tag) for tag in value]
    return [str(value)]


def _has_unknown_normalization(field_normalizations: list[dict[str, str]]) -> bool:
    return any(normalization["normalized"] == "unknown" for normalization in field_normalizations)


def _normalize_confidence(value: Any, field_normalizations: list[dict[str, str]]) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value or "unknown")
    normalized_key = raw.strip().lower().replace(" ", "_")
    if normalized_key in CONFIDENCE_ALIASES:
        normalized = CONFIDENCE_ALIASES[normalized_key]
        field_normalizations.append({"field": "confidence", "input": raw, "normalized": str(normalized)})
        return normalized

    try:
        return float(raw)
    except ValueError:
        field_normalizations.append({"field": "confidence", "input": raw, "normalized": "unknown"})
        return 0.0


def _normalize_source_quote(value: str, field_normalizations: list[dict[str, str]]) -> str:
    raw = value
    quote = raw.strip()
    if quote.startswith("- "):
        quote = quote[2:].strip()
    if quote.startswith("|") and quote.endswith("|"):
        cells = [cell.strip() for cell in quote.strip("|").split("|")]
        if cells:
            quote = cells[-1]
    if quote != raw:
        field_normalizations.append({"field": "source_quote", "input": raw, "normalized": quote})
    return quote


def _item_error_code(exc: Exception) -> str:
    message = str(exc)
    if "is not a JSON object" in message:
        return "MODEL_ITEM_NOT_OBJECT"
    if "missing required field" in message:
        return "MODEL_ITEM_MISSING_FIELD"
    if "schema validation" in message:
        return "MODEL_ITEM_SCHEMA_INVALID"
    return "MODEL_ITEM_INVALID"
