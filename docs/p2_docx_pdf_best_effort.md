# P2 DOCX / Text PDF Best-Effort Adapter

SpecTrail P2 extends the input layer from Markdown to DOCX and text-based PDF while keeping the existing ReqIR extraction, source quote validation, review, and export pipeline.

> Historical scope note: this document records the P2 adapter boundary. P5 now
> adds typed `TextLocator`, PDF `PageLocator`, DOCX/PDF table-cell grounding,
> generation-bound Evidence APIs, and reviewer-visible highlights. See
> [p5_evidence_review.md](p5_evidence_review.md) for the current contract.

## Supported Inputs

```text
.md / .markdown
.docx
.pdf with extractable text
```

The parser registry converts each supported input into:

```text
parsed/document.md
parsed/blocks.json
```

The rest of the pipeline remains format-agnostic:

```text
DocumentBlock[]
  -> mock ReqIR payload
  -> RequirementIR
  -> SourceQuoteValidator
  -> reqir.json / requirements.xlsx
```

## P2 Non-Goals

```text
No OCR
No scanned PDF support
No complex PDF layout restoration
No image or chart understanding
No bbox highlighting at the P2 milestone
No table-cell grounding at the P2 milestone
No multi-document task support
```

At the P2 milestone, PDF page numbers were carried as best-effort source
context through `DocumentBlock.page` and `SourceSpan.page`; they were not
visual grounding. P5 supersedes this limitation for supported text PDFs while
OCR, scanned PDFs, image/chart understanding, and arbitrary complex-layout
restoration remain out of scope.

## Mock Fixture Constraint

The current `MockModel` reads `fixtures/mock_reqir_response.json` and ignores the document text. That fixture is intentionally bound to `source_block_id` values such as `blk_0006`.

Because `SourceQuoteValidator` checks each quote inside the referenced block, P2 end-to-end tests generate DOCX and PDF fixtures from `docs/sample_srs.md` blocks. This keeps block order aligned with the Markdown sample and proves the full chain:

```text
DOCX / PDF
  -> blocks.json
  -> mock ReqIR
  -> source quote validation
  -> reqir.json
  -> requirements.xlsx
```

Relevant tests:

```bash
pytest tests/test_docx_parser.py
pytest tests/test_pdf_parser.py
pytest tests/test_pipeline_document_formats.py
pytest tests/test_api_tasks.py
```

## CLI Examples

Run the included DOCX demo:

```bash
python -m spectrail extract docs/sample_srs.docx --model-mode mock --output outputs/demo_docx
```

Run the included text-based PDF demo:

```bash
python -m spectrail extract docs/sample_srs_text.pdf --model-mode mock --output outputs/demo_pdf
```

Expected outputs are the same as the Markdown demo:

```text
parsed/document.md
parsed/blocks.json
extracted/reqir.raw.json
extracted/reqir.validated.json
extracted/source_map.json
extracted/validation_report.json
review/review_log.json
exports/reqir.json
exports/requirements.xlsx
```

If a PDF has no extractable text, the parser fails with `DOCUMENT_PARSE_FAILED` through the API. A single empty page becomes a parser warning if other pages contain text.
