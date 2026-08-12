from __future__ import annotations

import re
from collections import Counter

from spectrail.agent.models import DocumentProfile
from spectrail.evidence.index_builder import (
    validate_evidence_index_against_parsed_document,
)
from spectrail.evidence.models import EvidenceIndex
from spectrail.llm.base import ModelRequest
from spectrail.llm.prompt_builder import PROMPT_VERSION, build_reqir_prompt
from spectrail.parsers.base import ParsedDocument


PROMPT_ESTIMATOR_VERSION = f"reqir_prompt_renderer_v1:{PROMPT_VERSION}"
LARGE_DOCUMENT_PROMPT_CHARS = 4_000
VERY_LARGE_DOCUMENT_PROMPT_CHARS = 16_000
MAX_DOCUMENT_NAME_CHARS = 255
MAX_WARNING_PARAMETERS = 8
MAX_WARNING_PARAMETER_VALUE = 1_000_000

_CAPABILITIES = ("page_region", "table_cell", "text_range")
_WARNING_CODE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)")
_WARNING_PARAMETER_RE = re.compile(r"\b([a-z][a-z0-9_]*)\s*=\s*(-?\d+)\b")
_EMPTY_PDF_PAGE_RE = re.compile(r"^page\s+(\d+)\s+has no extractable text$", re.I)


class DocumentProfiler:
    """Build a deterministic, planner-safe summary from trusted parser output."""

    def build(
        self,
        parsed_document: ParsedDocument,
        evidence_index: EvidenceIndex,
    ) -> DocumentProfile:
        validate_evidence_index_against_parsed_document(
            evidence_index,
            parsed_document,
        )
        if (
            parsed_document.source_sha256 is not None
            and parsed_document.source_sha256 != evidence_index.source_sha256
        ):
            raise ValueError(
                "parsed document source_sha256 does not match evidence index"
            )
        if parsed_document.parser_identity is not None and (
            parsed_document.parser_identity != evidence_index.parser_identity
        ):
            raise ValueError("parsed document parser identity does not match evidence index")

        type_counts = Counter(block.type for block in parsed_document.blocks)
        expected_counts = _capability_counts(
            block.expected_capabilities for block in evidence_index.blocks
        )
        available_counts = _capability_counts(
            block.available_capabilities for block in evidence_index.blocks
        )
        warnings = _sanitize_warnings(parsed_document.warnings)
        estimated_prompt_chars = _estimate_prompt_chars(
            parsed_document,
            evidence_index,
        )

        flags: list[str] = []
        if type_counts["table"] or evidence_index.tables:
            flags.append("HAS_TABLES")
        if available_counts["page_region"]:
            flags.append("HAS_PAGE_REGIONS")
        if available_counts["table_cell"]:
            flags.append("HAS_TABLE_CELL_EVIDENCE")
        if any(item.startswith("PDF_MULTI_COLUMN_") for item in warnings):
            flags.append("HAS_MULTI_COLUMN_WARNING")
        if any(
            "table_cell" in block.expected_capabilities
            and "table_cell" not in block.available_capabilities
            for block in evidence_index.blocks
        ):
            flags.append("HAS_UNAVAILABLE_TABLE_EVIDENCE")
        if any(
            item.startswith(
                ("PDF_REPEATED_HEADER_CANDIDATE", "PDF_REPEATED_FOOTER_CANDIDATE")
            )
            for item in warnings
        ):
            flags.append("HAS_REPEATED_EDGE_WARNING")
        if estimated_prompt_chars >= LARGE_DOCUMENT_PROMPT_CHARS:
            flags.append("LARGE_DOCUMENT")
        if estimated_prompt_chars >= VERY_LARGE_DOCUMENT_PROMPT_CHARS:
            flags.append("VERY_LARGE_DOCUMENT")
        if warnings:
            flags.append("PARSER_WARNINGS_PRESENT")

        return DocumentProfile(
            document_id=parsed_document.document_id,
            document_name=parsed_document.document_name[:MAX_DOCUMENT_NAME_CHARS],
            source_format=parsed_document.source_format,
            source_sha256=evidence_index.source_sha256,
            parser_name=evidence_index.parser_identity.parser_name,
            parser_version=evidence_index.parser_identity.parser_version,
            page_count=(len(evidence_index.pages) or None),
            block_count=len(parsed_document.blocks),
            section_count=len(
                {
                    tuple(block.section_path)
                    for block in parsed_document.blocks
                    if block.section_path
                }
            ),
            block_type_counts={key: type_counts[key] for key in sorted(type_counts)},
            heading_count=type_counts["heading"],
            paragraph_count=type_counts["paragraph"],
            table_block_count=type_counts["table"],
            evidence_table_count=len(evidence_index.tables),
            evidence_cell_count=len(evidence_index.cells),
            expected_capability_counts=expected_counts,
            available_capability_counts=available_counts,
            rendered_text_chars=sum(len(block.text) for block in parsed_document.blocks),
            estimated_prompt_chars=estimated_prompt_chars,
            prompt_estimator_version=PROMPT_ESTIMATOR_VERSION,
            warnings=warnings,
            complexity_flags=flags,
        )


def _capability_counts(capability_lists) -> dict[str, int]:
    counts = Counter(
        capability
        for capabilities in capability_lists
        for capability in capabilities
    )
    return {capability: counts[capability] for capability in _CAPABILITIES}


def _estimate_prompt_chars(
    parsed_document: ParsedDocument,
    evidence_index: EvidenceIndex,
) -> int:
    request = ModelRequest(
        document_text="",
        blocks=parsed_document.blocks,
        document_name=parsed_document.document_name,
        source_format=parsed_document.source_format,
        parser_name=parsed_document.parser_name,
        model_mode="profile",
        metadata={"evidence_policy": "structured_if_available"},
        evidence_index=evidence_index,
    )
    return len(build_reqir_prompt(request))


def _sanitize_warnings(warnings: list[str]) -> list[str]:
    sanitized: list[str] = []
    for warning in warnings[:64]:
        code_match = _WARNING_CODE_RE.match(warning)
        if code_match is not None:
            code = code_match.group(1)
            parameters: dict[str, int] = {}
            for key, raw_value in _WARNING_PARAMETER_RE.findall(warning):
                value = int(raw_value)
                if abs(value) <= MAX_WARNING_PARAMETER_VALUE:
                    parameters.setdefault(key, value)
                if len(parameters) == MAX_WARNING_PARAMETERS:
                    break
            if parameters:
                rendered = ",".join(
                    f"{key}={parameters[key]}" for key in sorted(parameters)
                )
                item = f"{code}:{rendered}"
            else:
                item = code
        else:
            empty_page = _EMPTY_PDF_PAGE_RE.match(warning)
            item = (
                f"PDF_PAGE_NO_EXTRACTABLE_TEXT:page={int(empty_page.group(1))}"
                if empty_page is not None
                else "PARSER_WARNING"
            )
        if item not in sanitized:
            sanitized.append(item)
    return sanitized
