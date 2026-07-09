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

The default recorded fixture is only for sample-aligned testing. It should not be treated as a model for arbitrary uploaded documents, because source grounding is block-level: `source_block_id` and `source_quote` must match the current `blocks.json`.

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
