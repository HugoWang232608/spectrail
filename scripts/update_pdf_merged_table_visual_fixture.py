"""Regenerate the checked M5.1 merged-table browser fixture."""

from pathlib import Path

from spectrail.core.io import write_json
from spectrail.evidence import (
    build_quote_match_registry,
    build_table_evidence_view,
)
from spectrail.evidence.enricher import SourceEvidenceEnricher
from spectrail.evidence.pdf_preview import render_pdf_page
from spectrail.evidence.source_identity import canonicalize_source_cell_ids
from spectrail.extractors.reqir_extractor import ReqIRExtractor
from spectrail.parsers.pdf_parser import PdfParserV2
from spectrail.validators.source_locator_validator import SourceLocatorValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator


ROOT = Path(__file__).resolve().parents[1]
PDF_FIXTURE = ROOT / "tests" / "fixtures" / "pdf_table_vertical_merge.pdf"
JSON_OUTPUT = (
    ROOT
    / "frontend"
    / "src"
    / "fixtures"
    / "pdf-merged-table-evidence.json"
)
PNG_OUTPUT = (
    ROOT
    / "frontend"
    / "tests"
    / "visual"
    / "fixtures"
    / "pdf-merged-table-page.png"
)


def main() -> None:
    parsed = PdfParserV2().parse(PDF_FIXTURE)
    index = parsed.evidence_index
    if index is None or not index.tables:
        raise RuntimeError("PDF merged-table fixture did not produce Evidence")
    table_block = next(block for block in parsed.blocks if block.type == "table")
    table = index.tables[0]
    source_cell_ids = [
        "cell_00000001_r0001_c0001",
        "cell_00000001_r0002_c0002",
    ]
    requirement = ReqIRExtractor().extract(
        {
            "items": [
                {
                    "statement": (
                        "The system shall preserve the shared control "
                        "in the second state."
                    ),
                    "source_block_id": table_block.block_id,
                    "source_quote": "Shared control | Second state",
                    "source_cell_ids": source_cell_ids,
                    "source_table_row_index": 2,
                }
            ]
        },
        parsed.blocks,
        document_name=parsed.document_name,
    )[0]
    requirement.id = "req_pdf_merged_table"
    requirement.title = "PDF merged-table structured evidence"
    requirement.tags = ["visual-acceptance", "pdf-table", "merged-cell"]
    canonicalize_source_cell_ids([requirement], index)
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
        evidence_index=index,
    )
    SourceEvidenceEnricher().enrich(
        [requirement],
        index,
        registry,
        parsed.blocks,
    )
    quote_validated, quote_report = SourceQuoteValidator().validate(
        [requirement],
        parsed.blocks,
        registry,
    )
    locator_validated, locator_report, failures = (
        SourceLocatorValidator().validate(
            quote_validated,
            index,
            registry,
            policy="structured_required",
            document_blocks=parsed.blocks,
        )
    )
    if (
        not quote_report.valid
        or not locator_report.valid
        or failures
        or locator_validated != [requirement]
    ):
        raise RuntimeError(
            "PDF merged-table visual fixture failed structured validation"
        )

    table_evidence = build_table_evidence_view(
        index,
        task_id="visual-task",
        table_id=table.table_id,
        block_id=table_block.block_id,
    )
    JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        JSON_OUTPUT,
        {
            "name": "PDF vertical merged-cell evidence",
            "evidenceFingerprint": index.evidence_fingerprint,
            "requirement": requirement.model_dump(mode="json"),
            "blocks": [table_block.model_dump(mode="json")],
            "tableEvidence": table_evidence.model_dump(mode="json"),
        },
    )

    preview, _, _ = render_pdf_page(PDF_FIXTURE, 1)
    PNG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PNG_OUTPUT.write_bytes(preview)


if __name__ == "__main__":
    main()
