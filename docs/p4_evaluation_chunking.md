# P4 Evaluation and Chunked Extraction

P4 keeps the existing single-pass behavior for small documents while adding a deterministic multi-chunk path for documents that exceed the configured rendered-prompt budget.

## Extraction

```bash
python -m spectrail extract DOCUMENT \
  --model-mode mock \
  --chunking auto \
  --max-rendered-prompt-chars 16000 \
  --overlap-blocks 1 \
  --validation-policy strict \
  --output outputs/run
```

`--chunking` accepts `auto`, `force`, or `off`. The prompt budget applies to the final provider request, not only source text. A single indivisible block may exceed the budget and is emitted with `CHUNK_OVERSIZED_BLOCK` instead of being silently truncated.

Each chunk gets an eight-digit stable ID such as `chk_00000001`. The request fingerprint is SHA-256 over the canonical sanitized provider body, so secrets are excluded and the same body is used for both identity and transmission.

Important artifacts include:

```text
parsed/chunks.json
extracted/chunk_results/{chunk_id}/request.sanitized.json
extracted/chunk_results/{chunk_id}/request_profile.json
extracted/chunk_results/{chunk_id}/model_response.json
extracted/chunk_results/{chunk_id}/candidates.accepted.json
extracted/chunk_results/{chunk_id}/candidates.rejected.json
extracted/model_response.index.json
extracted/rejected_model_items.json
extracted/duplicate_groups.json
extracted/aggregation_report.json
extracted/reqir.quarantined.json
```

Malformed model items are rejected individually. Valid siblings continue through aggregation and validation. Exact overlap duplicates collapse deterministically; conflicting concrete structured fields are retained as aggregation variants and marked for recheck.

## Outcomes

Successful runs use either `completed` or `completed_with_warnings`. Warning completion remains readable, reviewable, and exportable through the API and UI. `run_manifest.json` records `warning_codes`, `zero_result_reason`, chunk counts, rejected items, quarantined requirements, collapsed duplicates, and field conflicts.

`--validation-policy strict` fails the run when source grounding fails. `quarantine` excludes invalid requirements from normal exports, writes them to `reqir.quarantined.json`, and finishes with warnings.

## Evaluation

An evaluation case points to a source document, a required gold package, execution settings, allowed outcomes, and metric thresholds:

```bash
python -m spectrail evaluate eval/cases/sample_srs/case.json \
  --output outputs/evaluation
```

Reports are written as JSON and Markdown. The matcher first performs deterministic one-to-one source alignment and then exact requirement matching. Precision/recall and operational pipeline rates use explicit zero-denominator rules. Any failed threshold makes the command exit with status 1.

The repository also includes `fixtures/recorded/chunked/sample_srs_long`. Its manifest binds each response to the root request profile, final request fingerprint, chunk fingerprint, chunk ID, and exact ordered block IDs. A normal single-file Recorded fixture is rejected when a run produces more than one chunk.

## API

`POST /api/tasks` accepts these optional P4 fields:

```json
{
  "chunking_mode": "auto",
  "max_rendered_prompt_chars": 16000,
  "overlap_blocks": 1,
  "validation_policy": "strict",
  "fail_fast": false
}
```

After a readable completion, `GET /api/tasks/{task_id}/chunks` returns the chunk plan and `GET /api/tasks/{task_id}/quarantined` returns quarantined ReqIR items.
