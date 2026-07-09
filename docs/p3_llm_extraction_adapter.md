# P3 LLM Extraction Adapter

P3 adds a small model-client layer between parsed documents and ReqIR extraction. The downstream contract is unchanged: every model output still goes through `ReqIRExtractor`, schema validation, source quote validation, EARS validation, review outputs, and exports.

## Model Modes

```text
mock
  Uses fixtures/mock_reqir_response.json.
  Deterministic and intended for CI, demos, and regression tests.

recorded
  Replays a recorded model fixture.
  The default fixture is sample-aligned with docs/sample_srs.md and exercises raw_text -> response_parser -> payload.

live
  Calls an OpenAI-compatible chat completions endpoint from local environment variables.
  It is intended for local manual validation and is not part of default CI.
```

## Recorded Mode

Run the recorded pipeline:

```bash
python -m spectrail extract docs/sample_srs.md \
  --model-mode recorded \
  --recorded-fixture fixtures/recorded/sample_srs_reqir_response.json \
  --output outputs/demo_recorded \
  --dump-prompt
```

Run the fuller recorded regression fixture:

```bash
python -m spectrail extract docs/sample_srs.md \
  --model-mode recorded \
  --recorded-fixture fixtures/recorded/sample_srs_reqir_response_full.json \
  --output outputs/demo_recorded_full
```

The default recorded fixture is only for sample-aligned testing. It should not be treated as a model for arbitrary uploaded documents, because source grounding is block-level: `source_block_id` and `source_quote` must match the current `blocks.json`.

The full fixture covers multiple requirement types and EARS patterns, omitted optional fields, missing tags, and common enum drift such as `unwanted` or unknown type labels. Such drift is normalized before Pydantic model construction and reported as `MODEL_ENUM_NORMALIZED` warnings in `validation_report.json`.

## Audit Metadata

`run_manifest.json` includes model and parser context:

```json
{
  "model": {
    "mode": "recorded",
    "name": "recorded-sample-fixture",
    "prompt_version": "reqir_extraction_v1",
    "recorded_fixture": "fixtures/recorded/sample_srs_reqir_response.json"
  },
  "parser": {
    "source_format": "markdown",
    "parser_name": "markdown_parser_v1",
    "warnings": []
  }
}
```

`plan.json` uses `document_parser_registry` for the parse step and records the selected parser after parsing.

## Live Mode

Configure an OpenAI-compatible provider:

```bash
export SPECTRAIL_LLM_API_KEY=...
export SPECTRAIL_LLM_MODEL=...
export SPECTRAIL_LLM_BASE_URL=https://api.openai.com/v1/chat/completions
```

Run live extraction:

```bash
python -m spectrail extract docs/sample_srs.md \
  --model-mode live \
  --output outputs/demo_live \
  --dump-prompt
```

`outputs/` may contain prompt text and raw model responses. Do not commit live outputs that include private documents.

## Boundaries

```text
P3 does not add agent planning, RAG, chunking, multi-document extraction, OCR, or automatic quote repair.
SourceQuoteValidator remains the hard gate before exports.
```
