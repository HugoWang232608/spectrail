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

## Roadmap

- [x] M6.0 — freeze backend, frontend, evaluation, PDF corpus, and visual gates
- [x] M6.1 — DocumentProfile v1 and internal Tool Registry contracts
- [ ] M6.2 — typed planner decisions, strict Recorded Planner replay, and LLM
  completion transport separation
- [ ] M6.3 — bounded AgentRunner, frozen policy, budgets, finish lattice, and
  durable trace events
- [ ] M6.4 — inspect, same-generation extraction retry, and replanning
- [ ] M6.5 — CLI/API orchestration mode
- [ ] M6.6 — deterministic Agent evaluation gate and read-only trace UI
