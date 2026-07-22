from __future__ import annotations

import os
import platform
import re
from collections import Counter
from json import JSONDecodeError
from pathlib import Path
from typing import Annotated, Any, Literal

import fitz
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from spectrail.core.io import read_json, write_json
from spectrail.evidence import validate_evidence_fingerprint
from spectrail.evidence.models import EvidenceCapability, EvidenceIndex, TableRecord
from spectrail.parsers import parse_document


PDF_CORPUS_OUTPUT_MARKER = ".spectrail-pdf-corpus-output"
PDF_CORPUS_OUTPUT_MARKER_PAYLOAD = {
    "schema_version": "spectrail_pdf_corpus_output_v2",
    "managed_paths": [
        "pdf_corpus_report.json",
        "pdf_corpus_report.md",
        ".pdf_corpus_report.json.staged",
        ".pdf_corpus_report.md.staged",
    ],
}
PDF_CORPUS_OUTPUT_MARKER_LEGACY_PAYLOADS = (
    {
        "schema_version": "spectrail_pdf_corpus_output_v1",
        "managed_paths": [
            "pdf_corpus_report.json",
            "pdf_corpus_report.md",
        ],
    },
)
PDF_CORPUS_METRIC_NAMES = frozenset(
    {
        "case_pass_rate",
        "case_evaluated_count",
        "producer_family_count",
        "external_document_case_count",
        "redistribution_reviewed_case_count",
        "metadata_locked_case_count",
        "gate_observation_pass_rate",
        "gate_observation_evaluated_count",
        "text_source_accuracy",
        "text_source_evaluated_count",
        "page_region_availability_rate",
        "page_region_evaluated_count",
        "selected_table_topology_precision",
        "selected_table_topology_recall",
        "table_topology_evaluated_count",
        "fallback_accuracy",
        "fallback_evaluated_count",
        "continuation_pair_accuracy",
        "continuation_pair_evaluated_count",
        "continuation_false_positive_count",
        "heading_precision",
        "heading_recall",
        "heading_evaluated_count",
    }
)


class PdfCorpusModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PdfMetadataExpectation(PdfCorpusModel):
    creator: str | None = None
    producer: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "PdfMetadataExpectation":
        if not any((self.creator, self.producer, self.title)):
            raise ValueError("expected_pdf_metadata must declare at least one field")
        return self


class PdfCorpusSource(PdfCorpusModel):
    provenance: Literal["external_document", "project_fixture"]
    producer_family_id: str = Field(
        min_length=1,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    )
    producer_family: str = Field(min_length=1)
    source_url: str | None = None
    redistribution_status: Literal[
        "public_domain",
        "redistribution_permitted",
        "existing_checked_fixture_terms_unverified",
        "download_only",
    ]
    license_note: str = Field(min_length=1)
    source_sha256: str
    expected_pdf_metadata: PdfMetadataExpectation | None = None

    @field_validator("source_sha256")
    @classmethod
    def validate_source_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("source_sha256 must be a lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def validate_external_provenance(self) -> "PdfCorpusSource":
        if self.provenance == "external_document" and not self.source_url:
            raise ValueError("external PDF corpus sources require source_url")
        return self


class ObservationBase(PdfCorpusModel):
    observation_id: str = Field(min_length=1)
    gate: bool = True
    notes: str | None = None


class TextSourceObservation(ObservationBase):
    kind: Literal["text_source"]
    quote: str = Field(min_length=1)
    page: int = Field(ge=1)
    expected_block_type: Literal["heading", "paragraph", "list", "table"] | None = None
    expected_section_path: list[str] | None = None
    required_expected_capabilities: list[EvidenceCapability] = Field(
        default_factory=lambda: ["text_range", "page_region"]
    )
    required_available_capabilities: list[EvidenceCapability] = Field(
        default_factory=lambda: ["text_range", "page_region"]
    )
    forbidden_available_capabilities: list[EvidenceCapability] = Field(
        default_factory=list
    )
    expect_page_region_available: bool | None = True


class HeadingPageObservation(ObservationBase):
    kind: Literal["heading_page"]
    page: int = Field(ge=1)
    expected_headings: list[str]


class ExpectedTable(PdfCorpusModel):
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    topology_status: Literal["complete", "sparse"] = "complete"
    continuation_role: Literal["single", "start", "continuation"] | None = None


class TablePageObservation(ObservationBase):
    kind: Literal["table_page"]
    page: int = Field(ge=1)
    expected_tables: list[ExpectedTable]


class FallbackBlockObservation(ObservationBase):
    kind: Literal["fallback_block"]
    quote: str = Field(min_length=1)
    page: int = Field(ge=1)
    required_expected_capabilities: list[EvidenceCapability] = Field(
        default_factory=lambda: ["text_range", "page_region", "table_cell"]
    )
    required_available_capabilities: list[EvidenceCapability] = Field(
        default_factory=lambda: ["text_range", "page_region"]
    )
    forbidden_available_capabilities: list[EvidenceCapability] = Field(
        default_factory=lambda: ["table_cell"]
    )


class ContinuationPairObservation(ObservationBase):
    kind: Literal["continuation_pair"]
    root_page: int = Field(ge=1)
    root_table_ordinal: int = Field(default=1, ge=1)
    continued_page: int = Field(ge=1)
    continued_table_ordinal: int = Field(default=1, ge=1)
    expected_linked: bool


PdfCorpusObservation = Annotated[
    TextSourceObservation
    | HeadingPageObservation
    | TablePageObservation
    | FallbackBlockObservation
    | ContinuationPairObservation,
    Field(discriminator="kind"),
]


class PdfCorpusCase(PdfCorpusModel):
    case_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    document: str = Field(min_length=1)
    tier: Literal["core", "extended"] = "core"
    source: PdfCorpusSource
    expected_parser_name: str = "pdf_parser_v2"
    expected_parser_version: str | None = None
    expected_evidence_fingerprint: str | None = None
    expected_evidence_fingerprints_by_platform: dict[str, str] = Field(
        default_factory=dict
    )
    observations: list[PdfCorpusObservation] = Field(min_length=1)

    @field_validator("expected_evidence_fingerprint")
    @classmethod
    def validate_expected_evidence_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is not None and (
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(
                "expected_evidence_fingerprint must be a lowercase SHA-256"
            )
        return value

    @field_validator("expected_evidence_fingerprints_by_platform")
    @classmethod
    def validate_platform_fingerprints(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        for platform_id, fingerprint in value.items():
            if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", platform_id):
                raise ValueError(
                    "Evidence fingerprint platform IDs must be normalized: "
                    f"{platform_id!r}"
                )
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdef"
                for character in fingerprint
            ):
                raise ValueError(
                    "platform Evidence fingerprints must be lowercase SHA-256"
                )
        return value

    @model_validator(mode="after")
    def validate_observation_ids(self) -> "PdfCorpusCase":
        identifiers = [item.observation_id for item in self.observations]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("observation IDs must be unique within a corpus case")
        if self.tier == "core":
            metadata = self.source.expected_pdf_metadata
            if metadata is None:
                raise ValueError(
                    "core PDF corpus cases require expected_pdf_metadata"
                )
            if not any((metadata.creator, metadata.producer)):
                raise ValueError(
                    "core PDF corpus metadata must lock creator or producer"
                )
            if self.source.redistribution_status == "download_only":
                raise ValueError(
                    "core PDF corpus cases cannot use download_only sources"
                )
        if (
            self.expected_evidence_fingerprints_by_platform
            and self.expected_evidence_fingerprint is None
        ):
            raise ValueError(
                "platform Evidence fingerprint overrides require a default "
                "expected_evidence_fingerprint"
            )
        invalid_report_only = [
            item.observation_id
            for item in self.observations
            if not item.gate and not isinstance(item, HeadingPageObservation)
        ]
        if invalid_report_only:
            raise ValueError(
                "only heading_page observations may use gate=false: "
                + ", ".join(invalid_report_only)
            )
        return self

    def expected_fingerprint_for_platform(
        self,
        platform_id: str,
    ) -> str | None:
        return self.expected_evidence_fingerprints_by_platform.get(
            platform_id,
            self.expected_evidence_fingerprint,
        )


class PdfCorpusManifest(PdfCorpusModel):
    schema_version: Literal["pdf_corpus_v1"] = "pdf_corpus_v1"
    name: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    cases: list[PdfCorpusCase] = Field(min_length=1)

    @field_validator("thresholds")
    @classmethod
    def validate_threshold_names(
        cls,
        value: dict[str, float],
    ) -> dict[str, float]:
        invalid = [
            name
            for name in value
            if not (
                (name.endswith("_min") and len(name) > 4)
                or (name.endswith("_max") and len(name) > 4)
            )
        ]
        if invalid:
            raise ValueError(
                "PDF corpus threshold names must end in _min or _max: "
                + ", ".join(sorted(invalid))
            )
        unknown = sorted(
            name
            for name in value
            if name[:-4] not in PDF_CORPUS_METRIC_NAMES
        )
        if unknown:
            raise ValueError(
                "PDF_CORPUS_THRESHOLD_UNKNOWN_METRIC: "
                + ", ".join(unknown)
            )
        return value

    @model_validator(mode="after")
    def validate_case_ids(self) -> "PdfCorpusManifest":
        identifiers = [item.case_id for item in self.cases]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("PDF corpus case IDs must be unique")
        return self


class PdfCorpusRunner:
    def run(
        self,
        manifest_path: str | Path,
        output_dir: str | Path,
        *,
        include_extended: bool = False,
    ) -> dict[str, Any]:
        manifest_file = Path(manifest_path)
        try:
            manifest = PdfCorpusManifest.model_validate(read_json(manifest_file))
        except (
            OSError,
            UnicodeError,
            JSONDecodeError,
            TypeError,
            ValidationError,
        ) as exc:
            raise ValueError(f"PDF_CORPUS_MANIFEST_INVALID: {exc}") from exc

        selected_cases = [
            case
            for case in manifest.cases
            if include_extended or case.tier == "core"
        ]
        if not selected_cases:
            raise ValueError("PDF_CORPUS_NO_SELECTED_CASES")

        output = _prepare_output(Path(output_dir))
        reports = [
            self._run_case(case, manifest_file.parent)
            for case in selected_cases
        ]
        runtime_platform_id = _runtime_platform_id()
        metrics = _build_suite_metrics(reports)
        threshold_results = _threshold_results(metrics, manifest.thresholds)
        cases_passed = sum(report["passed"] for report in reports)
        suite = {
            "schema_version": "pdf_corpus_report_v1",
            "name": manifest.name,
            "case_count": len(reports),
            "case_passed": cases_passed,
            "case_failed": len(reports) - cases_passed,
            "passed": (
                cases_passed == len(reports)
                and all(item["passed"] for item in threshold_results.values())
            ),
            "include_extended": include_extended,
            "runtime_platform_id": runtime_platform_id,
            "metrics": metrics,
            "threshold_results": threshold_results,
            "cases": reports,
        }
        _publish_reports(output, suite)
        return suite

    def _run_case(
        self,
        case: PdfCorpusCase,
        manifest_dir: Path,
    ) -> dict[str, Any]:
        unresolved_document = Path(case.document)
        document = (
            unresolved_document
            if unresolved_document.is_absolute()
            else manifest_dir / unresolved_document
        )
        identity_issues: list[str] = []
        observation_results: list[dict[str, Any]] = []
        actual_parser_identity: dict[str, Any] | None = None
        actual_evidence_fingerprint: str | None = None
        actual_source_sha256: str | None = None
        actual_pdf_metadata: dict[str, Any] | None = None
        try:
            document = _resolve_document(case.document, manifest_dir)
            actual_pdf_metadata = _read_pdf_metadata(document)
            parsed = parse_document(document, document_id="doc_001")
            evidence_index = parsed.evidence_index
            if evidence_index is None:
                raise ValueError("PDF parser did not provide an EvidenceIndex")
            validate_evidence_fingerprint(evidence_index)
            actual_parser_identity = (
                parsed.parser_identity.model_dump(mode="json")
                if parsed.parser_identity is not None
                else None
            )
            actual_evidence_fingerprint = evidence_index.evidence_fingerprint
            actual_source_sha256 = evidence_index.source_sha256
            runtime_platform_id = _runtime_platform_id()
            identity_issues = _identity_issues(
                case,
                parsed,
                evidence_index,
                actual_pdf_metadata,
                runtime_platform_id,
            )
            observation_results = [
                _evaluate_observation(item, parsed.blocks, evidence_index)
                for item in case.observations
            ]
            error = None
        except (OSError, ValueError) as exc:
            error = str(exc)
            if not identity_issues:
                identity_issues = [f"parse failed: {exc}"]

        gated_results = [
            item for item in observation_results if item["gate"]
        ]
        passed = (
            error is None
            and not identity_issues
            and all(item["passed"] for item in gated_results)
        )
        return {
            "case_id": case.case_id,
            "tier": case.tier,
            "document": document.as_posix(),
            "source": case.source.model_dump(mode="json"),
            "passed": passed,
            "identity_passed": not identity_issues,
            "identity_issues": identity_issues,
            "actual_parser_identity": actual_parser_identity,
            "actual_evidence_fingerprint": actual_evidence_fingerprint,
            "actual_source_sha256": actual_source_sha256,
            "actual_pdf_metadata": actual_pdf_metadata,
            "runtime_platform_id": _runtime_platform_id(),
            "error": error,
            "observation_count": len(case.observations),
            "gated_observation_count": len(gated_results),
            "gated_observation_passed": sum(
                item["passed"] for item in gated_results
            ),
            "observations": observation_results,
        }


def _identity_issues(
    case: PdfCorpusCase,
    parsed: Any,
    evidence_index: EvidenceIndex,
    pdf_metadata: dict[str, Any],
    runtime_platform_id: str,
) -> list[str]:
    issues: list[str] = []
    identity = parsed.parser_identity
    actual_name = identity.parser_name if identity is not None else parsed.parser_name
    actual_version = identity.parser_version if identity is not None else None
    if actual_name != case.expected_parser_name:
        issues.append(
            f"parser_name expected={case.expected_parser_name!r} actual={actual_name!r}"
        )
    if (
        case.expected_parser_version is not None
        and actual_version != case.expected_parser_version
    ):
        issues.append(
            "parser_version "
            f"expected={case.expected_parser_version!r} actual={actual_version!r}"
        )
    if evidence_index.source_sha256 != case.source.source_sha256:
        issues.append(
            "source_sha256 "
            f"expected={case.source.source_sha256!r} "
            f"actual={evidence_index.source_sha256!r}"
        )
    expected_fingerprint = case.expected_fingerprint_for_platform(
        runtime_platform_id
    )
    if (
        expected_fingerprint is not None
        and evidence_index.evidence_fingerprint != expected_fingerprint
    ):
        issues.append(
            "evidence_fingerprint "
            f"platform={runtime_platform_id!r} "
            f"expected={expected_fingerprint!r} "
            f"actual={evidence_index.evidence_fingerprint!r}"
        )
    expected_metadata = case.source.expected_pdf_metadata
    if expected_metadata is not None:
        for name, expected_value in expected_metadata.model_dump(
            mode="json",
            exclude_none=True,
        ).items():
            actual_value = pdf_metadata.get(name)
            if actual_value != expected_value:
                issues.append(
                    "PDF metadata "
                    f"{name} expected={expected_value!r} actual={actual_value!r}"
                )
    return issues


def _evaluate_observation(
    observation: PdfCorpusObservation,
    blocks: list[Any],
    evidence_index: EvidenceIndex,
) -> dict[str, Any]:
    if isinstance(observation, TextSourceObservation):
        return _evaluate_text_source(observation, blocks, evidence_index)
    if isinstance(observation, HeadingPageObservation):
        return _evaluate_heading_page(observation, blocks)
    if isinstance(observation, TablePageObservation):
        return _evaluate_table_page(observation, evidence_index)
    if isinstance(observation, FallbackBlockObservation):
        return _evaluate_fallback_block(observation, blocks, evidence_index)
    if isinstance(observation, ContinuationPairObservation):
        return _evaluate_continuation_pair(observation, evidence_index)
    raise AssertionError(f"unsupported PDF corpus observation: {observation}")


def _evaluate_text_source(
    observation: TextSourceObservation,
    blocks: list[Any],
    evidence_index: EvidenceIndex,
) -> dict[str, Any]:
    matches = _matching_blocks(blocks, observation.page, observation.quote)
    issues = _unique_match_issues(matches)
    evidence = None
    if len(matches) == 1:
        block = matches[0]
        evidence = _block_evidence(evidence_index, block.block_id)
        if observation.expected_block_type is not None and (
            block.type != observation.expected_block_type
        ):
            issues.append(
                f"block type expected={observation.expected_block_type} actual={block.type}"
            )
        if observation.expected_section_path is not None and [
            _normalize_text(item) for item in block.section_path
        ] != [
            _normalize_text(item)
            for item in observation.expected_section_path
        ]:
            issues.append(
                "section path mismatch: "
                f"expected={observation.expected_section_path!r} "
                f"actual={block.section_path!r}"
            )
        issues.extend(
            _capability_issues(
                evidence,
                required_expected=observation.required_expected_capabilities,
                required_available=observation.required_available_capabilities,
                forbidden_available=observation.forbidden_available_capabilities,
            )
        )
        if observation.expect_page_region_available is not None:
            actual = (
                evidence is not None
                and "page_region" in evidence.available_capabilities
            )
            if actual != observation.expect_page_region_available:
                issues.append(
                    "page_region availability "
                    f"expected={observation.expect_page_region_available} "
                    f"actual={actual}"
                )
    return _observation_result(
        observation,
        not issues,
        issues,
        {
            "match_count": len(matches),
            "block_id": matches[0].block_id if len(matches) == 1 else None,
            "page_region_available": (
                evidence is not None
                and "page_region" in evidence.available_capabilities
            ),
            "expected_page_region_available": (
                observation.expect_page_region_available
            ),
        },
    )


def _evaluate_heading_page(
    observation: HeadingPageObservation,
    blocks: list[Any],
) -> dict[str, Any]:
    actual = [
        _normalize_text(block.text)
        for block in blocks
        if block.page == observation.page and block.type == "heading"
    ]
    expected = [_normalize_text(item) for item in observation.expected_headings]
    actual_counter = Counter(actual)
    expected_counter = Counter(expected)
    true_positive = sum(
        min(actual_counter[value], expected_counter[value])
        for value in actual_counter.keys() | expected_counter.keys()
    )
    false_positive = len(actual) - true_positive
    false_negative = len(expected) - true_positive
    issues = []
    if false_positive or false_negative:
        issues.append(
            "heading set mismatch: "
            f"expected={expected!r} actual={actual!r}"
        )
    return _observation_result(
        observation,
        not issues,
        issues,
        {
            "expected": expected,
            "actual": actual,
            "heading_true_positive": true_positive,
            "heading_false_positive": false_positive,
            "heading_false_negative": false_negative,
        },
    )


def _evaluate_table_page(
    observation: TablePageObservation,
    evidence_index: EvidenceIndex,
) -> dict[str, Any]:
    actual = _page_tables(evidence_index, observation.page)
    expected = observation.expected_tables
    issues: list[str] = []
    matched = 0
    for index in range(max(len(actual), len(expected))):
        expected_table = expected[index] if index < len(expected) else None
        actual_table = actual[index] if index < len(actual) else None
        if expected_table is None:
            issues.append(f"unexpected table at ordinal {index + 1}")
            continue
        if actual_table is None:
            issues.append(f"missing table at ordinal {index + 1}")
            continue
        table_issues = _table_issues(expected_table, actual_table)
        if table_issues:
            issues.extend(
                f"table ordinal {index + 1}: {issue}"
                for issue in table_issues
            )
        else:
            matched += 1
    return _observation_result(
        observation,
        not issues,
        issues,
        {
            "expected_table_count": len(expected),
            "actual_table_count": len(actual),
            "table_true_positive": matched,
            "table_false_positive": len(actual) - matched,
            "table_false_negative": len(expected) - matched,
        },
    )


def _evaluate_fallback_block(
    observation: FallbackBlockObservation,
    blocks: list[Any],
    evidence_index: EvidenceIndex,
) -> dict[str, Any]:
    matches = _matching_blocks(blocks, observation.page, observation.quote)
    issues = _unique_match_issues(matches)
    if len(matches) == 1:
        evidence = _block_evidence(evidence_index, matches[0].block_id)
        issues.extend(
            _capability_issues(
                evidence,
                required_expected=observation.required_expected_capabilities,
                required_available=observation.required_available_capabilities,
                forbidden_available=observation.forbidden_available_capabilities,
            )
        )
    return _observation_result(
        observation,
        not issues,
        issues,
        {
            "match_count": len(matches),
            "block_id": matches[0].block_id if len(matches) == 1 else None,
        },
    )


def _evaluate_continuation_pair(
    observation: ContinuationPairObservation,
    evidence_index: EvidenceIndex,
) -> dict[str, Any]:
    root = _table_at(
        evidence_index,
        observation.root_page,
        observation.root_table_ordinal,
    )
    continued = _table_at(
        evidence_index,
        observation.continued_page,
        observation.continued_table_ordinal,
    )
    issues = []
    if root is None:
        issues.append("root table was not found")
    if continued is None:
        issues.append("continued table was not found")
    actual_linked = bool(
        root is not None
        and continued is not None
        and root.continuation_group_id is not None
        and root.continuation_group_id == continued.continuation_group_id
        and continued.continuation_role == "continuation"
        and continued.continuation_of_table_id == root.table_id
    )
    if root is not None and continued is not None and (
        actual_linked != observation.expected_linked
    ):
        issues.append(
            "continuation relationship "
            f"expected={observation.expected_linked} actual={actual_linked}"
        )
    return _observation_result(
        observation,
        not issues,
        issues,
        {
            "actual_linked": actual_linked,
            "continuation_false_positive": int(
                not observation.expected_linked and actual_linked
            ),
        },
    )


def _observation_result(
    observation: ObservationBase,
    passed: bool,
    issues: list[str],
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "kind": getattr(observation, "kind"),
        "gate": observation.gate,
        "passed": passed,
        "notes": observation.notes,
        "issues": issues,
        "details": details,
    }


def _matching_blocks(
    blocks: list[Any],
    page: int,
    quote: str,
) -> list[Any]:
    normalized_quote = _normalize_text(quote)
    return [
        block
        for block in blocks
        if block.page == page
        and normalized_quote in _normalize_text(block.text)
    ]


def _unique_match_issues(matches: list[Any]) -> list[str]:
    if not matches:
        return ["quote did not match a block on the expected page"]
    if len(matches) > 1:
        return [
            "quote matched multiple blocks on the expected page: "
            + ", ".join(block.block_id for block in matches)
        ]
    return []


def _block_evidence(
    evidence_index: EvidenceIndex,
    block_id: str,
) -> Any | None:
    return next(
        (item for item in evidence_index.blocks if item.block_id == block_id),
        None,
    )


def _capability_issues(
    evidence: Any | None,
    *,
    required_expected: list[EvidenceCapability],
    required_available: list[EvidenceCapability],
    forbidden_available: list[EvidenceCapability],
) -> list[str]:
    if evidence is None:
        return ["block Evidence record was not found"]
    expected = set(evidence.expected_capabilities)
    available = set(evidence.available_capabilities)
    issues = []
    missing_expected = sorted(set(required_expected) - expected)
    missing_available = sorted(set(required_available) - available)
    forbidden = sorted(set(forbidden_available) & available)
    if missing_expected:
        issues.append(f"missing expected capabilities: {missing_expected}")
    if missing_available:
        issues.append(f"missing available capabilities: {missing_available}")
    if forbidden:
        issues.append(f"forbidden capabilities are available: {forbidden}")
    return issues


def _page_tables(
    evidence_index: EvidenceIndex,
    page: int,
) -> list[TableRecord]:
    return sorted(
        [item for item in evidence_index.tables if item.page == page],
        key=lambda item: item.table_id,
    )


def _table_at(
    evidence_index: EvidenceIndex,
    page: int,
    ordinal: int,
) -> TableRecord | None:
    tables = _page_tables(evidence_index, page)
    return tables[ordinal - 1] if ordinal <= len(tables) else None


def _table_issues(
    expected: ExpectedTable,
    actual: TableRecord,
) -> list[str]:
    issues = []
    for name in ("row_count", "column_count", "topology_status"):
        expected_value = getattr(expected, name)
        actual_value = getattr(actual, name)
        if expected_value != actual_value:
            issues.append(
                f"{name} expected={expected_value!r} actual={actual_value!r}"
            )
    if (
        expected.continuation_role is not None
        and actual.continuation_role != expected.continuation_role
    ):
        issues.append(
            "continuation_role "
            f"expected={expected.continuation_role!r} "
            f"actual={actual.continuation_role!r}"
        )
    return issues


def _build_suite_metrics(reports: list[dict[str, Any]]) -> dict[str, Any]:
    observations = [
        item
        for report in reports
        for item in report["observations"]
    ]
    gated = [item for item in observations if item["gate"]]
    text = [item for item in observations if item["kind"] == "text_source"]
    fallback = [item for item in observations if item["kind"] == "fallback_block"]
    continuation = [
        item for item in observations if item["kind"] == "continuation_pair"
    ]
    heading_tp = sum(
        item["details"].get("heading_true_positive", 0)
        for item in observations
    )
    heading_fp = sum(
        item["details"].get("heading_false_positive", 0)
        for item in observations
    )
    heading_fn = sum(
        item["details"].get("heading_false_negative", 0)
        for item in observations
    )
    table_tp = sum(
        item["details"].get("table_true_positive", 0)
        for item in observations
    )
    table_fp = sum(
        item["details"].get("table_false_positive", 0)
        for item in observations
    )
    table_fn = sum(
        item["details"].get("table_false_negative", 0)
        for item in observations
    )
    page_region = [
        item
        for item in text
        if item["details"].get("expected_page_region_available") is not None
    ]
    return {
        "case_pass_rate": _ratio(
            sum(report["passed"] for report in reports),
            len(reports),
        ),
        "case_evaluated_count": len(reports),
        "producer_family_count": len(
            {
                report["source"]["producer_family_id"]
                for report in reports
            }
        ),
        "external_document_case_count": sum(
            report["source"]["provenance"] == "external_document"
            for report in reports
        ),
        "redistribution_reviewed_case_count": sum(
            report["source"]["redistribution_status"]
            in {"public_domain", "redistribution_permitted"}
            for report in reports
        ),
        "metadata_locked_case_count": sum(
            report["source"]["expected_pdf_metadata"] is not None
            for report in reports
        ),
        "gate_observation_pass_rate": _ratio(
            sum(item["passed"] for item in gated),
            len(gated),
        ),
        "gate_observation_evaluated_count": len(gated),
        "text_source_accuracy": _ratio(
            sum(item["passed"] for item in text),
            len(text),
        ),
        "text_source_evaluated_count": len(text),
        "page_region_availability_rate": _ratio(
            sum(
                item["details"].get("page_region_available")
                == item["details"].get("expected_page_region_available")
                for item in page_region
            ),
            len(page_region),
        ),
        "page_region_evaluated_count": len(page_region),
        "selected_table_topology_precision": _ratio(
            table_tp,
            table_tp + table_fp,
        ),
        "selected_table_topology_recall": _ratio(
            table_tp,
            table_tp + table_fn,
        ),
        "table_topology_evaluated_count": table_tp + table_fp + table_fn,
        "fallback_accuracy": _ratio(
            sum(item["passed"] for item in fallback),
            len(fallback),
        ),
        "fallback_evaluated_count": len(fallback),
        "continuation_pair_accuracy": _ratio(
            sum(item["passed"] for item in continuation),
            len(continuation),
        ),
        "continuation_pair_evaluated_count": len(continuation),
        "continuation_false_positive_count": sum(
            item["details"].get("continuation_false_positive", 0)
            for item in continuation
        ),
        "heading_precision": _ratio(
            heading_tp,
            heading_tp + heading_fp,
        ),
        "heading_recall": _ratio(
            heading_tp,
            heading_tp + heading_fn,
        ),
        "heading_evaluated_count": heading_tp + heading_fp + heading_fn,
    }


def _threshold_results(
    metrics: dict[str, Any],
    thresholds: dict[str, float],
) -> dict[str, dict[str, Any]]:
    results = {}
    for name, threshold in thresholds.items():
        metric_name = name[:-4]
        actual = metrics.get(metric_name)
        if name.endswith("_min"):
            operator = ">="
            passed = actual is not None and actual >= threshold
        else:
            operator = "<="
            passed = actual is not None and actual <= threshold
        results[name] = {
            "metric": metric_name,
            "actual": actual,
            "operator": operator,
            "threshold": threshold,
            "passed": passed,
        }
    return results


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _resolve_document(value: str, manifest_dir: Path) -> Path:
    path = Path(value)
    resolved = path if path.is_absolute() else manifest_dir / path
    if not resolved.is_file():
        raise FileNotFoundError(f"PDF corpus document not found: {resolved}")
    if resolved.suffix.lower() != ".pdf":
        raise ValueError(f"PDF corpus document must be a PDF: {resolved}")
    return resolved.resolve()


def _read_pdf_metadata(path: Path) -> dict[str, Any]:
    document = None
    try:
        document = fitz.open(path)
        return {
            key: value
            for key, value in document.metadata.items()
            if key in {"creator", "producer", "title"}
        }
    except Exception as exc:
        raise ValueError(f"PDF_CORPUS_METADATA_UNAVAILABLE: {path}") from exc
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass


def _prepare_output(output: Path) -> Path:
    output = output.resolve(strict=False)
    if output.exists() and not output.is_dir():
        raise ValueError(f"PDF_CORPUS_OUTPUT_NOT_DIRECTORY: {output}")
    marker = output / PDF_CORPUS_OUTPUT_MARKER
    if output.exists():
        entries = list(output.iterdir())
        if entries and not _valid_output_marker(marker):
            raise ValueError(
                "PDF_CORPUS_OUTPUT_NOT_OWNED: existing non-empty output "
                f"directory does not contain {PDF_CORPUS_OUTPUT_MARKER}: {output}"
            )
    else:
        output.mkdir(parents=True)
    if _read_output_marker(marker) != PDF_CORPUS_OUTPUT_MARKER_PAYLOAD:
        write_json(marker, PDF_CORPUS_OUTPUT_MARKER_PAYLOAD)
    managed_targets = [
        output / relative
        for relative in PDF_CORPUS_OUTPUT_MARKER_PAYLOAD["managed_paths"]
    ]
    invalid_targets = [
        target
        for target in managed_targets
        if target.exists() and not target.is_symlink() and not target.is_file()
    ]
    if invalid_targets:
        rendered = ", ".join(path.as_posix() for path in invalid_targets)
        raise ValueError(
            "PDF_CORPUS_MANAGED_PATH_NOT_FILE: " + rendered
        )
    for target in managed_targets:
        if target.is_symlink() or target.is_file():
            target.unlink()
    return output


def _valid_output_marker(marker: Path) -> bool:
    payload = _read_output_marker(marker)
    return payload == PDF_CORPUS_OUTPUT_MARKER_PAYLOAD or payload in (
        PDF_CORPUS_OUTPUT_MARKER_LEGACY_PAYLOADS
    )


def _read_output_marker(marker: Path) -> Any | None:
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        return read_json(marker)
    except (OSError, UnicodeError, JSONDecodeError):
        return None


def _runtime_platform_id() -> str:
    system = re.sub(r"[^a-z0-9]+", "_", platform.system().lower()).strip("_")
    machine = re.sub(r"[^a-z0-9]+", "_", platform.machine().lower()).strip("_")
    machine = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)
    return f"{system}-{machine}"


def _publish_reports(output: Path, suite: dict[str, Any]) -> None:
    json_staged = output / ".pdf_corpus_report.json.staged"
    markdown_staged = output / ".pdf_corpus_report.md.staged"
    json_target = output / "pdf_corpus_report.json"
    markdown_target = output / "pdf_corpus_report.md"
    markdown = _suite_markdown(suite)
    try:
        write_json(json_staged, suite)
        _fsync_file(json_staged)
        _write_text_durable(markdown_staged, markdown)
        if read_json(json_staged) != suite:
            raise ValueError("PDF_CORPUS_STAGED_JSON_INVALID")
        if markdown_staged.read_text(encoding="utf-8") != markdown:
            raise ValueError("PDF_CORPUS_STAGED_MARKDOWN_INVALID")

        # Markdown is a rebuildable projection. Publish it first and the
        # authoritative JSON last, so JSON presence marks a complete result.
        os.replace(markdown_staged, markdown_target)
        _fsync_directory(output)
        os.replace(json_staged, json_target)
        _fsync_directory(output)
    finally:
        for staged in (json_staged, markdown_staged):
            if staged.is_symlink() or staged.is_file():
                staged.unlink()


def _write_text_durable(path: Path, value: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _suite_markdown(suite: dict[str, Any]) -> str:
    lines = [
        f"# PDF Corpus Report: {suite['name']}",
        "",
        f"- Passed: {suite['passed']}",
        f"- Cases: {suite['case_passed']} / {suite['case_count']}",
        f"- Runtime platform: {suite['runtime_platform_id']}",
        "",
        "## Metrics",
        "",
    ]
    for name, value in suite["metrics"].items():
        lines.append(f"- {name}: {value}")
    lines.extend(["", "## Thresholds", ""])
    if suite["threshold_results"]:
        for name, result in suite["threshold_results"].items():
            lines.append(
                f"- {name}: {result['actual']} {result['operator']} "
                f"{result['threshold']} -> {result['passed']}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Cases", ""])
    for case in suite["cases"]:
        lines.append(
            f"### {case['case_id']} — {'PASS' if case['passed'] else 'FAIL'}"
        )
        lines.append("")
        if case["identity_issues"]:
            lines.append(
                "- Identity issues: " + "; ".join(case["identity_issues"])
            )
        for observation in case["observations"]:
            gate = "gate" if observation["gate"] else "report-only"
            lines.append(
                f"- {observation['observation_id']} "
                f"({observation['kind']}, {gate}): "
                f"{'PASS' if observation['passed'] else 'FAIL'}"
            )
            for issue in observation["issues"]:
                lines.append(f"  - {issue}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
