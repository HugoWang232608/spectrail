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

For live providers, `SPECTRAIL_LLM_BASE_URL` is used only for transport. Set a stable logical `SPECTRAIL_LLM_ENDPOINT_ID` such as `openai-public` or `company-internal-gateway` for manifests and fingerprints. Custom URLs require this ID. Request options are allowlisted, cannot replace `model`, `messages`, `temperature`, or `response_format`, and are recursively checked for secret-like keys.

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

A chunk counts as successful only after its top-level payload has passed the `items` array contract. Provider, response parsing, and top-level payload contract failures may be isolated per chunk; configuration errors, file-system failures, and unexpected programming exceptions fail the whole pipeline. If no chunk passes the top-level contract, the result is `failed` with `ALL_CHUNKS_FAILED`.

When a section is split, the planner carries its most recent heading hierarchy as context blocks when the real rendered-prompt budget allows. Context blocks are recorded separately in `context_block_ids`, are not new or overlap blocks, and cannot be cited as requirement sources. Under budget pressure the planner reduces overlap first and then optional heading context.

`chunk_fingerprint` represents chunk planning identity and content. It includes the chunker version, counter width, content-affecting planner settings, and ordered new/overlap/context blocks. Execution-only settings such as `fail_fast` are excluded.

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

Evaluation cases may declare a complete `request_profile`, including adapter identity, logical endpoint ID, model name, temperature, response format, and safe request options. A failed extraction writes a structured case report from `run_manifest.json` and does not prevent later cases from running; malformed case schemas and gold packages remain suite-level configuration errors. Reports include raw matching counts, duplicate/quarantine/rejection rates, chunk and model-call counts, execution sizes and timing, outcome fields, and every threshold comparison.

For selected-scope evaluation, the document is parsed and every configured block ID is validated before the extraction pipeline starts, so an invalid scope cannot trigger live model calls. Both candidates and a reusable full-document gold package are filtered to requirements with at least one source in scope, and matching edges may use only in-scope sources. Reports expose both the full gold count and the evaluated scoped gold count.

An empty selected-scope gold set is a configuration error by default, even though the metric zero-denominator policy defines an empty/empty score as `1.0`. Set `allow_empty_gold_scope` to `true` only for an intentional negative case. CI cases can additionally require a minimum annotation count with a threshold such as `"gold_requirements_min": 1`.

Live transport settings are resolved once and frozen for the model client. An explicit request profile must use the same logical endpoint ID and model name as the transport mapping; the profile controls canonical request identity while the frozen transport supplies the URL and credentials. Per-response prompt metadata uses the prompt version carried by the final request.

The report field `local_top_edge_tie_count` describes local equal-quality edge ties only. It does not claim that multiple globally optimal bipartite matchings exist; deterministic stable ranking still selects one reproducible result.

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
