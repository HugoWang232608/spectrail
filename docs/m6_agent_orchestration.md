# M6 Agent Orchestration

M6 implements the v0.9 bounded-agent architecture without weakening the
deterministic extraction, Evidence, validation, review, or generation
contracts. The Agent will choose only allowlisted coarse-grained tools and
mutable extraction arguments. Fixed orchestration remains the default until
the AgentRunner, replay gate, and API contracts are complete.

## M6.0 frozen baseline

Baseline date: 2026-08-12  
Code baseline: `abadb2b9` (`fix: tighten PDF corpus identity contracts`)

Runtime identity:

```text
Darwin 25.5.0 arm64
Python 3.10.11
Node.js 22.14.0
npm 10.9.2
PyMuPDF 1.28.0
Pydantic 2.13.4
FastAPI 0.139.0
openpyxl 3.1.5
```

Recorded gates:

| Gate | Command | Result |
| --- | --- | --- |
| Backend | `pytest` | 460 passed, 1 skipped |
| Frontend unit | `cd frontend && npm test` | 73 passed |
| Frontend build | `cd frontend && npm run build` | passed |
| Frontend visual | `cd frontend && npm run test:visual` | 9 passed |
| Extraction evaluation | `python -m spectrail evaluate eval/cases --output /private/tmp/spectrail-m6-baseline-evaluation` | 4/4 cases passed |
| PDF corpus | `python -m spectrail evaluate-pdf-corpus eval/pdf_corpus_v1/manifest.json --output /private/tmp/spectrail-m6-baseline-pdf-corpus` | 5/5 cases passed |

These are the fixed-mode non-regression gates for subsequent M6 stages.

After M6.1 was added, the complete backend suite passed with 478 tests and one
skip; frontend unit/build and both evaluation gates retained the baseline
results.

## M6.1 contracts

The first implementation slice adds:

```text
ParsedDocument + EvidenceIndex
  -> DocumentProfiler
  -> planner-safe DocumentProfile v1

validated planner arguments
  -> ToolRegistry
  -> allowlisted AgentTool
  -> validated ToolResult
  -> path-free PlannerObservation
```

`DocumentProfile v1` freezes these counting rules:

- `page_count` is the number of `EvidenceIndex.pages`, or `null` when no page
  Evidence exists.
- `block_count` is the number of parsed blocks.
- `section_count` is the number of distinct non-empty `section_path` tuples.
- capability counts count blocks declaring each capability, not distinct
  capability names.
- `rendered_text_chars` is the sum of Unicode code-point lengths of block text.
- `estimated_prompt_chars` is the exact length produced by the versioned ReqIR
  prompt renderer over the current blocks and Evidence projection.

The estimator identity is
`reqir_prompt_renderer_v1:reqir_extraction_v10_table_row_evidence_v5`.
`LARGE_DOCUMENT` starts at 4,000 estimated prompt characters and
`VERY_LARGE_DOCUMENT` at 16,000. A future estimator or threshold change must
change the versioned contract and its tests.

Planner-visible profiles contain no document body, block text, table-cell
text, artifact path, or parser warning prose. Document names are limited to
255 Unicode code points. Parser warnings are reduced to stable codes plus at
most eight bounded integer parameters.

`ToolRegistry` derives every input JSON Schema from the tool's Pydantic
arguments model, rejects duplicate and unknown tools, validates arguments
before invocation, requires argument models to reject extra fields, freezes the
registered argument-model contract, and validates that the returned
`ToolResult.tool` matches the invoked name. `PlannerObservation` intentionally
removes internal artifact paths and free-text summaries.

## M6.2 planner and transport contracts

The second implementation slice adds:

```text
AgentPlannerInput
  -> versioned planner prompt
  -> CompletionTransport
  -> strict agent_decision_v1 parser

canonical structured planner input
  -> request fingerprint
  -> RecordedAgentPlanner exact-step replay
```

`AgentPolicy v1` is frozen and uses immutable tool/chunking allowlists. It
freezes `fail_fast=false`, requires positive budgets, caps extraction attempts
at the product hard limit of four, and must validate every allowed tool against
the active registry.

Planner decisions are discriminated `invoke_tool` or `finish` objects. The
parser accepts one strict JSON object only: Markdown fences, prose, duplicate
keys, non-JSON numeric constants, extra fields, unknown actions, and reasons
over 512 Unicode code points are rejected. This parser is independent from the
ReqIR response parser.

The planner request fingerprint binds the prompt version, safe model request
profile, document profile and estimator identity, frozen policy, sorted tool
contracts, latest path-free observation, structured history, and budget.
Timestamps, artifact paths, display summaries, and planner reasons cannot enter
the replay identity.

`fixtures/agent/sample_srs_agent.json` demonstrates a strict two-step clean
replay. A fingerprint mismatch, exhausted fixture, invalid decision, or unused
required step fails closed.

The OpenAI-compatible provider code now lives in a generic
`CompletionTransport`. `OpenAICompatibleModel` remains the ReqIR-compatible
wrapper, while `AgentPlannerClient` uses the same transport with its own prompt
and parser. After M6.2, the complete backend suite passes with 506 tests and one
skip.

## M6.3 bounded single-attempt runtime

The third implementation slice executes the contracts from M6.1/M6.2:

```text
parse once
  -> profile_document prelude
  -> planner request
  -> policy + argument validation
  -> run_requirement_extraction
  -> path-free observation
  -> planner request
  -> deterministic finish validation
```

`AgentRunner` owns the outer task transaction and calls the new public
`PipelineRunner.extract_within_transaction()` entry. The entry fails unless
the caller already holds the task transaction. The parsed document is reused
by the extraction attempt, so the source parser runs once.

M6.3 supports one extraction attempt. A second attempt is rejected with
`AGENT_RETRY_NOT_AVAILABLE_M6_3`; inspect and same-generation retry are M6.4.
Planner arguments cannot set `fail_fast`, trust policies, model settings,
paths, or generation identity. Chunking mode, prompt size, and overlap are
checked against frozen policy before `tool_started` is emitted.

The deterministic finish lattice prevents the planner from relabeling a
failed, warning, zero-result, quarantined, or unreadable pipeline result as a
clean completion. `needs_human` without an extraction attempt is allowed and
maps to `completed_with_warnings` plus `AGENT_NEEDS_HUMAN`.

Agent artifacts now include:

```text
agent/policy.json
agent/profile.json
agent/events/000001.json ...
agent/trace.jsonl
agent/attempts/attempt_0001.json
agent/final_state.json
```

The numbered event files are authoritative immutable facts. Every event is
fsynced and atomically published; `trace.jsonl` is rebuilt from them. Sequence
gaps, mixed generations, unexpected event files, invalid attempts, symlinks,
or abandoned temporary artifacts fail closed with
`AGENT_TRACE_RECOVERY_REQUIRED`. Fixed runs now explicitly write
`orchestration.mode=fixed`; successful Agent runs replace it with bounded
planner metadata and generation-bound counters.

## M6.4 inspection and same-generation replanning

M6.4 adds a diagnostic tool whose observation is materially richer than the
extraction summary. `inspect_extraction_result` reports chunk completion and
failure counts, accepted/rejected candidates, validation/quarantine breakdown,
quote and locator failures, and deterministic retry facts such as
`CHUNK_PROMPT_OVER_BUDGET`. It validates the manifest generation before
returning planner-visible facts.

The runtime now has two explicit artifact cleanup contracts:

- `prepare_new_agent_generation()` removes the fixed pipeline allowlist and
  the previous `agent/` root before a top-level Agent run;
- `reset_pipeline_artifacts_for_agent_retry()` removes only `plan.json`,
  `run_manifest.json`, `parsed/`, `extracted/`, `review/`, and `exports/`.

Both require the outer task transaction, validate every target before deleting
anything, reject symlinked or type-mismatched managed targets, and preserve
`task.json`, `input/`, current Agent events/attempts, and unrelated sentinel
files. They do not recursively delete the task root.

`fixtures/agent/sample_srs_replan_agent.json` freezes the first true replanning
sequence:

```text
run(auto)
  -> completed_with_warnings / PARTIAL_CHUNK_FAILURE
inspect
  -> CHUNK_PROMPT_OVER_BUDGET
run(force, max_rendered_prompt_chars=8000)
  -> completed
finish(completed)
```

The acceptance test uses the real planner, policy, registry, reset, trace, and
AgentRunner contracts with a deterministic two-outcome pipeline harness. It
verifies four planner decisions, two extraction attempts, changed arguments,
one unchanged run generation, one reused `ParsedDocument`, and two
generation-bound attempt summaries. A production extraction retry uses the
same public within-transaction pipeline entry.

## M6.4.1 recoverable failure observations

The extraction tool boundary now converts only explicitly classified pipeline
failures with a generation-matching failed manifest into a failed
`ToolResult`. Provider, response parsing, payload contract, all-chunks-failed,
and no-valid-items failures are retryable; no-extractable-content is observable
but terminal. The Planner receives that observation and remains responsible
for choosing retry or finish. Transaction violations, evidence or generation
identity mismatches, artifact/trace corruption, policy failures, and unknown
exceptions still fail the Agent run immediately.

Tool exceptions also have distinct trace semantics. Every invocation can
publish a failed tool-result event, but only
`run_requirement_extraction` publishes an `AgentAttemptSummary`. An inspection
failure therefore cannot republish or overwrite the immutable summary for the
preceding extraction attempt.

## Roadmap

- [x] M6.0 — freeze backend, frontend, evaluation, PDF corpus, and visual gates
- [x] M6.1 — DocumentProfile v1 and internal Tool Registry contracts
- [x] M6.2 — typed planner decisions, strict Recorded Planner replay, and LLM
  completion transport separation
- [x] M6.3 — bounded AgentRunner, frozen policy, budgets, finish lattice, and
  durable trace events
- [x] M6.4 — inspect, same-generation extraction retry, and replanning
- [x] M6.4.1 — observable recoverable extraction failures and attempt isolation
- [ ] M6.5 — CLI/API orchestration mode
- [ ] M6.6 — deterministic Agent evaluation gate and read-only trace UI
