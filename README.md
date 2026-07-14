# SpecTrail

SpecTrail is a local-first pipeline that turns requirements documents into grounded ReqIR JSON and Excel exports. It currently supports Markdown, DOCX, and text-based PDF inputs with human review and source quote validation.

[![CI](https://github.com/HugoWang232608/spectrail/actions/workflows/ci.yml/badge.svg)](https://github.com/HugoWang232608/spectrail/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](pyproject.toml)

## From source document to reviewed requirement

SpecTrail keeps the generated requirement, its source evidence, review state, and export row connected throughout the workflow.

```json
{
  "id": "REQ-0006",
  "type": "functional",
  "ears_pattern": "event_driven",
  "statement": "授权用户刷卡成功后，门禁控制器应在一秒内释放门锁。",
  "source": {
    "block_id": "blk_0012",
    "quote": "授权用户刷卡成功后，门禁控制器应在一秒内释放门锁。",
    "match_status": "PASS_EXACT"
  },
  "review_status": "pending"
}
```

> **Source quote:** “授权用户刷卡成功后，门禁控制器应在一秒内释放门锁。”
>
> **Validation:** exact match in `blk_0012`; the requirement is eligible for human review and export.

The Review UI keeps the candidate, editable fields, structured ReqIR detail, and highlighted source block visible in one workspace:

![SpecTrail Review UI showing REQ-0006 and its exact source evidence](docs/assets/review-ui.png)

The Excel export preserves the same traceability fields for downstream review and handoff:

| ID | Statement | EARS Pattern | Review Status | Source Block | Source Match |
| --- | --- | --- | --- | --- | --- |
| REQ-0006 | 授权用户刷卡成功后，门禁控制器应在一秒内释放门锁。 | event_driven | pending | blk_0012 | PASS_EXACT |

The generated workbook contains 17 columns, including the normalized statement, subject, condition, response, confidence, review status, source quote, match status, and tags.

## Architecture

```mermaid
flowchart LR
    A[Markdown / DOCX / text PDF] --> B[Parser Registry]
    B --> C[Canonical text + DocumentBlock]
    C --> D[Section-aware chunk planner]
    D --> E{Model mode}
    E --> F[Mock]
    E --> G[Recorded]
    E --> H[Live OpenAI-compatible]
    F --> I[Per-item ReqIR extraction]
    G --> I
    H --> I
    I --> J[Deterministic aggregation]
    J --> K[Schema / EARS / source quote validation]
    K --> L[Review UI + review log]
    L --> M[ReqIR JSON / Excel / source map]
```

## Quick start

```bash
python -m pip install -e ".[dev]"
python -m spectrail extract docs/sample_srs.md --model-mode mock --output outputs/demo
```

Then inspect `outputs/demo/exports/reqir.json` and `outputs/demo/exports/requirements.xlsx`, or start the API and Review UI using the walkthrough below.

## P0 Demo

Install dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the deterministic mock pipeline:

```bash
python -m spectrail extract docs/sample_srs.md --model-mode mock --output outputs/demo
```

Expected outputs:

```text
outputs/demo/plan.json
outputs/demo/run_manifest.json
outputs/demo/parsed/document.md
outputs/demo/parsed/blocks.json
outputs/demo/extracted/reqir.raw.json
outputs/demo/extracted/reqir.validated.json
outputs/demo/extracted/source_map.json
outputs/demo/extracted/validation_report.json
outputs/demo/review/review_log.json
outputs/demo/exports/reqir.json
outputs/demo/exports/requirements.xlsx
```

Re-run validation and write the report:

```bash
python -m spectrail validate outputs/demo/extracted/reqir.raw.json \
  --blocks outputs/demo/parsed/blocks.json \
  --output outputs/demo/extracted/validation_report.json
```

Apply a review action:

```bash
python -m spectrail review outputs/demo --id REQ-0001 --action approve --reviewer local
```

Run tests:

```bash
pytest
```

## P1 API Demo

Start the local API:

```bash
uvicorn spectrail.api.app:app --reload
```

Create a task:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"goal":"extract_requirements","model_mode":"mock"}'
```

Upload Markdown:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/documents \
  -F "file=@docs/sample_srs.md"
```

Run the pipeline:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/run
```

Review a requirement:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/review \
  -H "Content-Type: application/json" \
  -d '{"requirement_id":"REQ-0001","action":"approve","reviewer":"local"}'
```

Download Excel:

```bash
curl -L http://127.0.0.1:8000/api/tasks/{task_id}/exports/requirements.xlsx \
  -o requirements.xlsx
```

## P1b Review UI Demo

See the full walkthrough in [docs/p1b_review_ui.md](docs/p1b_review_ui.md).

Install Python and frontend dependencies:

```bash
python -m pip install -e ".[dev]"
cd frontend
npm install
```

Start the API from the repository root:

```bash
uvicorn spectrail.api.app:app --reload
```

Start the UI:

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173/`, then run this flow:

```text
Create Task
Upload docs/sample_srs.md
Run Pipeline
Select a ReqIR row
Review source quote and highlighted block text
Approve / reject / restore or edit statement / tags / priority
Download reqir.json or requirements.xlsx
```

Build the frontend:

```bash
cd frontend
npm run build
```

## P2 DOCX / Text PDF Demo

SpecTrail P2 adds best-effort input adapters for DOCX and text-based PDF files. The downstream pipeline is the same as Markdown:

```text
DOCX / text PDF
  -> parsed/document.md + parsed/blocks.json
  -> mock ReqIR extraction
  -> source quote validation
  -> reqir.json + requirements.xlsx
```

Install runtime and dev dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the dynamic end-to-end format tests:

```bash
pytest tests/test_pipeline_document_formats.py tests/test_api_tasks.py
```

Run the included DOCX and text-based PDF demo files:

```bash
python -m spectrail extract docs/sample_srs.docx --model-mode mock --output outputs/demo_docx
python -m spectrail extract docs/sample_srs_text.pdf --model-mode mock --output outputs/demo_pdf
```

Sample files:

```text
docs/sample_srs.md        Project-authored Markdown sample
docs/sample_srs.docx      DOCX demo generated from docs/sample_srs.md blocks
docs/sample_srs_text.pdf  Text PDF demo generated from docs/sample_srs.md blocks
```

The external PDF fixture `tests/fixtures/ieee29148_srs_example.pdf` is downloaded from:

```text
https://www.cin.ufpe.br/~in1020/docs/publicacoes/IEEE29148-srs_example.pdf
```

It is used only for parser smoke testing with a real text-based SRS PDF; the mock end-to-end pipeline demos use the project-authored `docs/sample_srs.*` files so source block IDs stay aligned with `fixtures/mock_reqir_response.json`.

P2 boundaries:

```text
Supported: Markdown, DOCX, text-based PDF
Not supported: scanned PDF, OCR, complex two-column layout recovery, image/chart understanding
PDF page numbers are best-effort source context; bbox highlighting is not implemented
```

See [docs/p2_docx_pdf_best_effort.md](docs/p2_docx_pdf_best_effort.md) for details.

## P3 LLM Adapter Demo

SpecTrail P3 adds a model-client layer with deterministic `mock`, replayable `recorded`, and locally configured `live` modes. All modes still pass through ReqIR extraction and source quote validation before export.

Run the sample-aligned recorded mode:

```bash
python -m spectrail extract docs/sample_srs.md \
  --model-mode recorded \
  --recorded-fixture fixtures/recorded/sample_srs_reqir_response.json \
  --output outputs/demo_recorded
```

Run the fuller recorded regression fixture:

```bash
python -m spectrail extract docs/sample_srs.md \
  --model-mode recorded \
  --recorded-fixture fixtures/recorded/sample_srs_reqir_response_full.json \
  --output outputs/demo_recorded_full
```

Run live mode with an OpenAI-compatible provider:

```bash
cp .env.example .env
# edit .env and set SPECTRAIL_LLM_API_KEY / SPECTRAIL_LLM_MODEL
# for a custom base URL, also set a stable logical SPECTRAIL_LLM_ENDPOINT_ID
python -m spectrail extract docs/sample_srs.md --model-mode live --output outputs/demo_live
```

If your local provider uses a self-signed certificate chain and you accept that risk for local testing, add `--insecure`:

```bash
python -m spectrail extract docs/sample_srs.md --model-mode live --output outputs/demo_live --insecure
```

Recorded fixtures are tied to their source document blocks; the default recorded fixture is for `docs/sample_srs.md` regression testing, not arbitrary uploads. See [docs/p3_llm_extraction_adapter.md](docs/p3_llm_extraction_adapter.md) for details.

Migrate a persisted task created with an older ReqIR, quote-match, or Evidence
schema before reviewing it:

```bash
python -m spectrail migrate outputs/demo
```

Migration stages and revalidates every new artifact before replacing task files.
Original files are retained under `outputs/demo/.migration_backup/`, and the
source-key and locator status changes are recorded in
`outputs/demo/migration/migration_report.json`. If a commit is interrupted, the
next migration run restores the recorded backup before retrying.

For a current `reqir_v4` task whose quote-match registry is missing or still uses
`quote_matches_v2`, validation can rebuild the registry explicitly:

```bash
python -m spectrail validate outputs/demo/exports/reqir.json \
  --blocks outputs/demo/parsed/blocks.json \
  --rebuild-quote-matches
```

Key changes from an older ReqIR identity algorithm require `migrate`; ordinary
validation never silently rebinds persisted source identities.

## P4 Evaluation and chunked extraction

P4 adds deterministic, section-aware chunking for long documents, overlap-safe candidate aggregation, per-item model-output isolation, request fingerprints, quarantine mode, and a checked-in evaluation quality gate.

Split sections retain budgeted heading context, while top-level response failures are isolated only when they are known model/contract errors. Unexpected code and file-system failures stop the pipeline instead of being converted into warning completion.

Force a small prompt budget to exercise the multi-chunk path:

```bash
python -m spectrail extract docs/sample_srs.md \
  --model-mode mock \
  --chunking force \
  --max-rendered-prompt-chars 1600 \
  --overlap-blocks 1 \
  --validation-policy quarantine \
  --output outputs/demo_chunked
```

Run the baseline evaluation case:

```bash
python -m spectrail evaluate eval/cases/sample_srs/case.json \
  --output outputs/evaluation
```

The evaluation command exits non-zero when a checked threshold fails, making it suitable for CI. See [docs/p4_evaluation_chunking.md](docs/p4_evaluation_chunking.md) for artifact formats, statuses, API parameters, and validation behavior.

The checked evaluation suite currently covers the original single-pass sample, a three-chunk long-document mock run, strict replay of the same long document from a request-fingerprint-bound Recorded bundle, and a selected-scope case over the included IEEE 29148 text PDF. All four gate source alignment recall, requirement exact recall, and export grounding at `1.0`.
