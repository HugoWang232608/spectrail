# P5 Evidence Review

P5 turns the typed Evidence locators into reviewer-visible source context. The
current vertical slices cover validated PDF page regions and DOCX table cells
while preserving the existing text-first review path.

## Current flow

```text
ReqIR SourceSpan.page_locator
  -> task-scoped PDF preview endpoint
  -> rotated page PNG
  -> proportional bbox overlay
  -> locator and capability diagnostics

ReqIR SourceSpan.table_locator
  -> task-scoped, block-scoped table evidence endpoint
  -> occurrence-aware HTML table grid
  -> physical-row + canonical-cell highlight
  -> locator and capability diagnostics

ReqIR metadata.evidence_fingerprint
  -> task-scoped canonical blocks endpoint
  -> fingerprint-bound block snapshot
  -> TextLocator highlight or explicit evidence reload

TaskStatusResponse.run_generation
  -> conditional ReqIR, blocks, table, and page reads
  -> one task-run snapshot across independent HTTP requests
```

The page preview uses the same
`pdf_preview_rotated_points_top_left_v1` coordinate space as `PageLocator`.
Preview scaling therefore does not change source identity or locator geometry;
the browser converts the bbox to percentages of `page_width` and `page_height`.

## API contract

```text
GET /api/tasks/{task_id}/pages/{page_number}/preview.png
  ?expected_evidence_fingerprint=<ReqIR metadata.evidence_fingerprint>
  &expected_run_generation=<GET task run_generation>
```

Successful responses include:

```text
Content-Type: image/png
Cache-Control: private, max-age=300
X-Spectrail-Preview-Width: <rendered pixels>
X-Spectrail-Preview-Height: <rendered pixels>
X-Spectrail-Evidence-Fingerprint: <validated Evidence fingerprint>
X-Spectrail-Run-Generation: <validated task run generation>
```

The endpoint:

- requires a completed or completed-with-warnings task;
- renders only the task's uploaded PDF;
- resolves and contains the document path within the task directory;
- participates in the task transaction guard;
- validates the current `EvidenceIndex` and compares the caller's expected
  fingerprint before rendering;
- hashes the current PDF and requires it to equal
  `EvidenceIndex.source_sha256`;
- uses RGB output without transparency;
- caps scale at 2× and either output dimension at 2000 pixels;
- permits a five-minute private browser cache so revisiting the same source
  does not repeatedly render the PDF. Explicit Retry uses a new query parameter
  and therefore bypasses the cached response.

Errors remain structured:

```text
TASK_NOT_FOUND
TASK_NOT_COMPLETED
PAGE_PREVIEW_NOT_FOUND
PAGE_PREVIEW_UNAVAILABLE
EVIDENCE_VERSION_CHANGED
RUN_GENERATION_CHANGED
TASK_TRANSACTION_LOCKED
TASK_MIGRATION_INCOMPLETE
```

`EVIDENCE_VERSION_CHANGED` covers both a stale ReqIR fingerprint and a current
PDF whose bytes no longer match the validated EvidenceIndex. The frontend
fetches the PNG as a blob, verifies the response fingerprint header, and only
then creates the image URL and draws the bbox overlay.

The table evidence endpoint is:

```text
GET /api/tasks/{task_id}/tables/{table_id}/blocks/{block_id}/evidence
  ?expected_evidence_fingerprint=<ReqIR metadata.evidence_fingerprint>
  &expected_run_generation=<GET task run_generation>
```

It returns a versioned `table_evidence_view_v1` projection rather than exposing
the complete `EvidenceIndex`. The response contains the table dimensions and
topology status, the block's primary row range, rendered physical rows, logical
cell coordinates and spans, stable cell IDs, occurrence roles and canonical
ranges. It is derived only after loading and fingerprint-validating the task's
`evidence_v5` artifact under the task transaction guard.

Rows are ordered by their canonical occurrence ranges, so a repeated header
projected into a later row-group remains before that group's primary rows.
Occurrences for the same logical cell and physical row are grouped into one
grid cell. This preserves the logical cell identity while still exposing
`original`, `row_span_projection`, `repeated_header`, and
`duplicate_text_occurrence` diagnostics.

Successful table responses use `Cache-Control: private, no-store`; rerunning a
task therefore cannot reuse a stale table projection. Table errors are
structured:

```text
TABLE_EVIDENCE_NOT_FOUND
TABLE_EVIDENCE_UNAVAILABLE
EVIDENCE_VERSION_CHANGED
RUN_GENERATION_CHANGED
```

The former covers unknown or foreign table/block references. The latter covers
a missing, invalid, or stale-fingerprint `EvidenceIndex`.
`EVIDENCE_VERSION_CHANGED` means the ReqIR package and the current task
EvidenceIndex are from different pipeline generations.

Canonical block context uses the same conditional-read contract:

```text
GET /api/tasks/{task_id}/blocks
  ?expected_evidence_fingerprint=<ReqIR metadata.evidence_fingerprint>
  &expected_run_generation=<GET task run_generation>
```

The response contains `task_id`, the validated `run_generation`,
`evidence_fingerprint`, and `items`. Before returning it, the service validates
the expected run generation, the Evidence fingerprint, and the complete blocks
artifact against the current `EvidenceIndex`. A changed task run returns
`RUN_GENERATION_CHANGED`; changed Evidence content returns
`EVIDENCE_VERSION_CHANGED`; an internally inconsistent blocks artifact returns
`BLOCKS_UNAVAILABLE`. Successful responses use
`Cache-Control: private, no-store` and return the generation again in
`X-Spectrail-Run-Generation`.

ReqIR uses the same task-run condition:

```text
GET /api/tasks/{task_id}/reqir
  ?expected_run_generation=<GET task run_generation>
```

The body remains the versioned ReqIR package for artifact compatibility, while
`X-Spectrail-Run-Generation` binds that package to the requested task run.
All four Evidence reads compare the expected generation while holding the same
task transaction lock used to read the artifact. A concurrent rerun therefore
fails closed before returning ReqIR, blocks, a table projection, or a page PNG.

Every pipeline ReqIR artifact now carries:

```text
metadata.evidence_fingerprint
```

Review snapshots preserve it, validation outputs copy it when Evidence is
available, and migration rewrites it to the migrated Evidence fingerprint.
The frontend sends the task generation with ReqIR, blocks, table, and page
requests and independently checks each response generation. It also sends the
exact Evidence fingerprint with blocks, table, and page requests and checks
each response fingerprint. A task rerun between any snapshot request therefore
produces an explicit reload message even if the Evidence content and stable
block, table, and cell IDs remain unchanged. Until reload succeeds, the UI
withholds canonical block text, table grid, and page preview. A
`BLOCKS_UNAVAILABLE` response also suppresses the table endpoint request
entirely; only the single block-context reload action is shown.
Legacy packages without the metadata cannot request trusted evidence and must
be migrated or reloaded.

`TaskStatusResponse` and `TaskRunResponse` use formal `TaskRecord` and
`RunManifest` Pydantic models. Response validation requires task IDs and
`run_generation` values to agree across the top-level response, nested task
record, and manifest, preventing a malformed mixed-generation status snapshot
from being serialized as a valid API response.

Review writes are conditional on the same snapshot:

```text
POST /api/tasks/{task_id}/review
{
  "expected_run_generation": <loaded task generation>,
  "expected_review_revision": <loaded requirement review revision>,
  "requirement_id": "...",
  "action": "approve | reject | edit | restore | request_recheck",
  ...
}
```

One outer task transaction covers the generation and readable-status checks,
ReqIR mutation, reviewer log update, and XLSX regeneration. A concurrent rerun
therefore returns `RUN_GENERATION_CHANGED` before any review artifact is
written, even when the new run happens to reuse the same requirement ID.
Each `RequirementIR` also carries a monotonically increasing
`review_revision`. The server compares `expected_review_revision` while holding
the same task lock and returns `REVIEW_REVISION_CHANGED` before mutation when a
second reviewer submits an edit based on an older item snapshot.
The three resulting artifacts are first generated in a same-filesystem staging
directory. The staged review log and ReqIR are read back and validated, and the
workbook must reopen with the expected sheet and row count. Durable backups and
a `review_transaction_v1` state are fsynced before the preparation directory is
atomically published as `.review_transaction`. The state records
`prepared`, `committing`, and `committed`, plus the old and new hashes of the
fixed review target set. Every task read or write holds the task lock and first
recovers this marker: pre-commit state rolls all targets back from immutable
backups, while committed state verifies the new target hashes and completes
publication. Invalid state fails closed as `TASK_REVIEW_RECOVERY_REQUIRED`.
Abandoned pre-publication directories are safely removed. Successful
`ReviewResponse` bodies return the actual `run_generation` and incremented
`review_revision`.

If publication and its immediate rollback both fail, the same review request
returns `409 TASK_REVIEW_RECOVERY_REQUIRED` with `retryable=false`; callers do
not need to issue a second request to discover the damaged transaction state.
Transaction targets are lexical, fixed allowlist paths. The `review/` and
`exports/` path chains, including the target files themselves, must not contain
symbolic links, even when a link resolves to another location inside the same
task directory.

Downloads and auxiliary review artifacts are conditional reads as well:

```text
GET /api/tasks/{task_id}/exports/{filename}
  ?expected_run_generation=<loaded task generation>
GET /api/tasks/{task_id}/chunks
  ?expected_run_generation=<loaded task generation>
GET /api/tasks/{task_id}/quarantined
  ?expected_run_generation=<loaded task generation>
```

Successful responses use `Cache-Control: private, no-store` and return
`X-Spectrail-Run-Generation`. The Review UI downloads exports with `fetch`,
validates the response generation, and creates a temporary Blob URL only for a
successful response. `RUN_GENERATION_CHANGED` remains a structured React error
with a **Reload task evidence** action, preventing a page showing generation N
from silently downloading generation N+1 exports or presenting an opaque
browser download failure.

`LocalTaskStore` caches the fully validated `EvidenceIndex`, validated blocks,
and block-scoped table projections by task ID plus artifact device, inode, size,
modification time and change time. Repeated source navigation therefore avoids
re-reading, Pydantic-validating and re-hashing the whole index. The cache is an
access-ordered LRU limited to 16 tasks by default; the least recently used task
is evicted when the limit is exceeded. Upload, pipeline reset, or any Evidence
artifact signature change invalidates the affected entry before evidence can be
returned. Blocks are cached as an immutable tuple of validated `DocumentBlock`
models; each caller receives a fresh JSON projection, so an in-process consumer
cannot mutate the trusted cache entry.

The same cache entry retains the validated PDF source SHA-256 together with its
device, inode, size, modification time, and change time. An unchanged source is
not re-hashed for every page request. A signature change forces a new full hash,
and a file whose signature changes during hashing is rejected as unavailable
rather than cached. The renderer checks the same validated signature again
after producing the PNG; a change during rendering clears the cached source
snapshot and discards the image instead of returning it with the earlier
Evidence fingerprint.

The public renderer preserves the primary page lookup or render exception if
closing the PDF also fails. Cleanup errors therefore cannot turn
`PAGE_PREVIEW_NOT_FOUND` into `PAGE_PREVIEW_UNAVAILABLE`; a close failure is
reported only when no earlier operation failed. When a primary error already
exists, the close failure is logged with that error type for diagnostics
without changing the API classification.

## UI behavior

For each source, the Review UI shows:

- quote match status;
- aggregate locator status and score;
- PDF page preview and bbox derivation;
- occurrence-aware table grid, row/column coordinates, spans, and canonical
  cell IDs when present;
- validation status for `text_range`, `page_region`, and `table_cell`;
- highlighted canonical block text as the fallback evidence view.

`SourceViewer` requires Evidence fingerprints and task run generations for both
ReqIR and blocks. Callers handling legacy data must pass `null` explicitly;
omitting either version context is not a trusted compatibility mode and fails
TypeScript compilation.

Canonical block highlighting uses the final `TextLocator` before considering the
legacy exact-quote fallback. Offsets are applied to `Array.from(text)` so the
`unicode_code_point` contract remains correct for emoji and supplementary CJK
characters. A normalized match with a valid locator is highlighted even when
the displayed canonical range is not byte-for-byte equal to `source.quote`.

Preview loading occurs only after same-generation ReqIR and block reads
complete, avoiding read-lock races. The browser requests the page with the
ReqIR Evidence fingerprint and task run generation, checks both response
headers, and uses a short-lived blob URL only after both checks pass. A failed
image can be retried explicitly without losing the text evidence view. Preview
state is keyed by the selected source, so a failed same-page source cannot
poison the next source.

If a blocks or table request reports a changed Evidence version, the Review UI
does not offer the ordinary single-request retry. It shows **Reload task
evidence**, which reloads task status, ReqIR, and canonical blocks as one client
operation before visual evidence is shown again.

Source selection itself uses the same stable identity plus its occurrence
ordinal instead of a numeric list index, and the selection context is scoped by
both task ID and requirement ID. The initially displayed source is committed to
that selection state even before the reviewer navigates.
Reordering therefore preserves the current source, exact duplicate sources
remain individually selectable, and removal or replacement synchronously falls
back to the first remaining source without an intermediate `No source` render.
Legacy fallback identity follows the same lifecycle priority as backend source
identity:

```text
source_evidence_key
  -> document + block + quote + canonical_source_cell_ids + source_table_row_index
  -> document + block + quote + source_cell_ids_raw + source_table_row_index
  -> document + block + quote + TableLocator identity
  -> document + block + quote + text occurrence range
```

The first available identity in that order is the primary selection key. Lower
priority identities remain matching aliases, allowing an existing raw-only
selection to migrate to canonical cells or a final `source_evidence_key`
without losing its occurrence. Raw aliases and derived `TableLocator` fields
never replace an existing canonical primary identity. Table sources with
identical text but different structured cells remain stable across reorder,
enrichment lifecycle transitions, and task switches.

Once selected, a valid final `source_evidence_key` is matched exactly. Replacing
it with a different key denotes a new Evidence version and explicitly resets
selection to the first source, even when a canonical alias still describes the
same logical quote. Initial assignment of a final key remains continuous
because the prior canonical/raw identity can match a lower-priority alias on
the newly keyed source.

Identity generation, alias matching, and occurrence resolution live in the
pure `frontend/src/evidence/sourceSelection.ts` module. Its migration matrix is
tested independently from SourceViewer, while component tests verify that
canonical selection survives final evidence-key assignment and list reorder.

The page image and red bbox are rendered only when the `page_region` capability
status is `PASS`. A legacy, edited, or migrated source with an invalid or
unverified locator cannot choose the preview page or aspect ratio; the UI
withholds the image, reports the locator status, and retains canonical block
text as the review evidence. Its metadata shows the canonical block page as
`Block Page` and, when they differ, lists `Claimed Page` separately as invalid,
ambiguous, unavailable, or not verified.

Non-pass states retain their validation meaning in the UI:

```text
UNVERIFIED           -> Page locator not verified
WARNING_UNAVAILABLE  -> Page locator unavailable
WARNING_AMBIGUOUS    -> Page locator ambiguous
FAIL_*               -> Page locator invalid
```

The table grid follows the same trust boundary. It is fetched and rendered only
when the `table_cell` capability status is `PASS` and a `TableLocator` exists.
The UI then checks that the response belongs to the current task, table and
block, contains the selected physical row, and maps each selected cell to the
locator's canonical row and column anchors. Only the cells matching both
`selected_row_index` and `cell_ids` receive the red selection highlight.

Merged cells retain their logical `column_span`; row-span projections and
repeated headers are displayed on the physical row represented in the source
block. Original header cells render as semantic column headers, while data
cells use grid-cell semantics with `aria-selected`. Stable cell IDs, logical
coordinates, occurrence roles, and canonical occurrence ranges remain visible
to the reviewer.
Sparse/unknown column gaps are rendered as unavailable grid slots rather than
invented cells.

For `UNVERIFIED`, warning, or failure statuses, the UI does not request table
evidence and does not draw a grid. API failures are retryable without losing
the canonical block text. A response that disagrees with the already validated
locator is withheld instead of producing a plausible but untrusted highlight.

Run the frontend evidence tests and production build with:

```bash
cd frontend
npm test
npx playwright install chromium
npm run test:visual
npm run build
```

`npm run test:visual` always executes the DOM, capability, source-selection,
and locator-geometry assertions. Pixel screenshot comparison is enabled only
on Linux, which is the owned baseline environment used by GitHub Actions.
Running the suite on macOS or Windows therefore remains useful for functional
browser checks without comparing platform-specific font rasterization.

Screenshot baselines must be updated on Linux:

```bash
cd frontend
npm run test:visual:update
```

The update command rejects non-Linux hosts. Use an Ubuntu 24.04 CI runner or an
equivalent Playwright Linux container; do not regenerate checked-in images
directly on macOS. The baseline environment is fixed by `package-lock.json`,
the Playwright Chromium revision, the checked-in Inter webfont, a 1180 × 940
viewport, device scale factor 1, light color scheme, and reduced motion.
The manually dispatched `Update frontend visual baselines` GitHub Actions
workflow is the canonical update path: download its
`frontend-visual-baselines-linux` artifact, inspect the images, and replace the
checked-in baseline directory in a normal reviewed change.

GitHub Actions runs both `npm test` and `npm run test:visual` before the
production frontend build, and uploads Playwright reports and image diffs when
the visual gate fails. Component
coverage verifies final locator highlighting, source and requirement changes,
preview failure and retry, proportional overlay geometry, locator/block
mismatch behavior, all four supported page rotations, table API failure and
retry, merged-column rendering, repeated-header projection, and precise
selected-cell highlighting. A dedicated row-span test fixes the projection
contract: the same logical cell is rendered on each represented physical row,
and only its selected `row_span_projection` occurrence is highlighted.

Backend geometry acceptance renders real PDFs at 0°, 90°, 180°, and 270°,
derives each `PageLocator` through the parser and enricher, decodes the same PNG
renderer used by the preview API, and confirms that the locator's proportional
pixel region contains the quoted glyphs. The renderer is the public, stateless
`spectrail.evidence.pdf_preview.render_pdf_page` function shared by TaskStore
and acceptance tests.

The browser acceptance suite supplies those validated locators to the
production `SourceViewer`, checks the overlay geometry to a 1.5-pixel tolerance,
and compares checked-in Chromium screenshots for 0°, 90°, 180°, and 270°
canonical preview spaces. Separate screenshots fix the table presentation
contract for a vertically merged DOCX cell (`row_span_projection`) and for the
second block of a large table, including its projected repeated header and
selected primary row. Table visual fixtures validate the same row-range
invariant as `build_table_evidence_view()`: `rendered_start` is the minimum
cell-occurrence start and `rendered_end` is the maximum occurrence end for the
physical row. These browser fixtures complement rather than replace the
real-PDF parser/renderer pixel test.

## M5 PDF table detection

PDF V2 now runs PyMuPDF
`find_tables(strategy="lines_strict", snap_tolerance=0.5)` and treats a result
as structured evidence only when its physical grid can be proven complete,
non-overlapping, and geometrically valid in canonical preview space. A trusted
table produces:

- `TableRecord(parser_method="pymupdf_find_tables", topology_status="complete")`;
- stable canonical `TableCellRecord` IDs with page-space bounding boxes;
- occurrence-aware canonical row text using the same escaped-cell serializer as
  DOCX;
- row-group blocks capped at 20 primary rows, with the first detected header row
  projected into later groups using the original logical cell IDs; and
- `text_range`, `page_region`, and `table_cell` as both expected and available
  capabilities.

Physical-grid validation is independent from the logical Evidence topology
check. With a 0.5-point boundary tolerance, every detected cell must remain
inside the table bbox, adjacent boundaries must align without a gap or area
overlap, and the occupied cells must tile the whole physical grid exactly once.
A detector result that violates any invariant is downgraded before an
`EvidenceIndex` can claim `topology_status="complete"`.

M5.1 extends that proof to merged cells through a deterministic boundary
lattice. The parser clusters the table and cell x/y boundaries, maps every
unique detector bbox to one contiguous lattice rectangle, and derives its
anchor, `row_span`, and `column_span`. It accepts the result only when:

- every boundary maps uniquely within tolerance;
- every physical coordinate is covered by exactly one logical cell;
- every detector projection refers to the same inferred owner;
- every merged cell has one unambiguous anchor; and
- all occupied slots, including bbox-less projection slots, assign at most one
  unique normalized non-empty text value to their inferred logical owner.

An accepted vertical merge is serialized once per occupied physical row. The
anchor occurrence is `original`; subsequent rows use
`row_span_projection` while retaining the same canonical cell ID. Horizontal
merges retain one logical cell with `column_span > 1`. These records flow
unchanged through prompt cell maps, source identity canonicalization,
`TableLocator`, `PageLocator(table_cell_union)`, `table_evidence_view_v1`, and
the existing Review grid.

Ambiguous merges, incomplete grids, and physically invalid PDF geometry are not
guessed. Their ordinary PDF text blocks remain available, a
`PDF_TABLE_CELL_EVIDENCE_UNAVAILABLE` warning records the reason, and no
`TableRecord` or available `table_cell` capability is exposed. The rejected
candidate bbox is retained long enough to mark a fallback block when at least
80% of that block lies in the table region or any of its text-fragment centers
lies inside the region. The fragment rule safely covers mixed blocks that
contain both a table row and adjacent prose without widening the association
to nearby captions or geometry-free text. Associated blocks expect
`text_range + page_region + table_cell`, while available capabilities remain
`text_range + page_region`. Consequently, `structured_if_available` preserves
the source with `table_cell=WARNING_UNAVAILABLE`, whereas
`structured_required` rejects it.

The checked-in `pdf_table_requirements.pdf` fixture runs the simple-grid path.
Three deterministic M5.1 fixtures additionally cover a horizontal merged
header, a vertical merged cell, and a boundary-ambiguous candidate that must
downgrade. The vertical fixture runs through prompt cell mapping, source
identity canonicalization, quote matching, enrichment, quote validation, and
`structured_required` locator validation. All three capabilities pass, and the
page locator is independently derived from the selected cell bbox union. The
browser fixture is generated from that checked backend projection, reuses
`table_evidence_view_v1`, and fixes the real PDF page overlay, selected grid
cells, and second-row `row_span_projection` in the Playwright screenshot gate.

The multi-producer corpus also includes
`pdf_table_merged_libreoffice.pdf`. LibreOffice, rather than ReportLab,
produces its page content: page 1 contains a horizontal merged header and page
2 contains a vertical merged cell. A checked `pdf_fixture_manifest_v1` records
the LibreOffice build identity, pypdf metadata-normalizer version, page count,
topology cases, and final PDF SHA-256. Acceptance verifies that manifest
against the checked bytes and PDF metadata, then runs both pages through
parsing, Evidence cross-validation, source canonicalization, quote matching,
`structured_required`, table projection, public page rendering, and
locator-to-pixel alignment. The generator intentionally keeps LibreOffice out
of the runtime dependency set; it is needed only when the checked corpus
artifact is deliberately regenerated.

Install the locked fixture toolchain and regenerate the LibreOffice corpus with:

```bash
python3 -m pip install \
  -c constraints-pdf-fixtures.txt -e ".[fixtures]"
SPECTRAIL_SOFFICE=/path/to/soffice \
  python3 tests/fixtures/build_pdf_merged_table_libreoffice_fixture.py
```

The generator prints the active python-docx, LibreOffice, and pypdf identities
and compares them with the checked manifest before publishing checked files. A
mismatch fails with `FIXTURE_TOOLCHAIN_MISMATCH`. It stages and validates the
PDF and manifest together, then publishes them with two sequential
`os.replace()` calls. This is fail-closed on the next acceptance run, but is not
a strictly pair-atomic filesystem transaction. An intentional
producer/toolchain migration must use
`SPECTRAIL_ACCEPT_FIXTURE_TOOLCHAIN_CHANGE=1`, then review the regenerated PDF,
manifest, rendered pages, and acceptance results together. The manifest cases
are the test driver's source of truth for table dimensions, logical cells,
source selection, span assertion, and occurrence role.

Backend acceptance projects a real detected table at 0°, 90°, 180°, and 270°,
derives `TableLocator` and `PageLocator(table_cell_union)`, renders the same
preview PNG used by the API, and verifies that the selected bbox contains
rendered pixels. The checked visual projection is compared with a freshly
built backend projection, and the checked page PNG is byte-hash compared with
the current public renderer output. A stale JSON or PNG fixture therefore
fails Python acceptance before Playwright can approve an impossible or old
browser response.

### M5.2 multi-page table continuation

PDF table continuation keeps the page-local trust boundary intact. Every page
still owns a separate `TableRecord`, canonical cell ID namespace, bbox, and
`TableLocator`; no source or locator spans pages. The parser links adjacent
page-local records only when all of the following evidence agrees:

- exactly one complete table reaches the previous page's bottom edge and
  exactly one complete table begins at the next page's top edge;
- the root table has exactly one nearby stable label, and every following page
  has exactly one matching explicit continuation marker;
- both tables have the same column count;
- their complete first-row header cells preserve canonical text, anchor
  columns, row/column spans, and normalized horizontal boundaries; and
- the pages are adjacent and every group sequence is contiguous.

Accepted records expose `continuation_group_id`, `continuation_role`,
`continuation_sequence`, the root `continuation_of_table_id`, the normalized
`continuation_label`, and
`continuation_basis=explicit_marker_page_edge_header_match`.
`continued_header_cell_ids` maps each page-local repeated header cell to the
root table's canonical header cell. Evidence validation independently checks
the group ordering, adjacent pages, root identity, one-to-one header coverage,
text, and topology. A mismatch or ambiguous page edge leaves both tables as
independent `single` records. In particular, adjacent complete tables with the
same header and column geometry remain independent when the authored
continuation marker is absent. Artifacts produced by the short-lived earlier
geometry-only implementation are not silently migrated: loading fails with
`EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED` before fingerprint
verification, and the task must be rerun with the current PDF parser.
Review recognizes that code across canonical blocks, page previews, and table
projections. It suppresses ineffective reload/retry actions and offers one
`Rerun task` recovery action, which clears the stale ReqIR, blocks, and source
selection before running the pipeline and loading the rebuilt Evidence. The
action is disabled while any other task operation is active and requires
confirmation because rerunning deletes existing review decisions, edits,
review history, and exports. If the rerun fails, Review refreshes the task
snapshot so failed status and export availability match the backend while the
original pipeline error remains visible. The ordinary sidebar `Run Pipeline`
action uses the same clear-run-refresh-reload coordinator; rerunning a readable
task has a separate destructive confirmation and cannot retain stale completed
status, ReqIR, blocks, or export availability after a backend failure.
Reconciliation runs even when the `POST /run` response is lost: a readable
refreshed snapshot reloads ReqIR and blocks. Task creation persists
`run_generation=0`; every backend run transaction increments it before
removing prior pipeline artifacts, and the same positive generation is written
to `task.json`, the run response, and `run_manifest.json`. If the refreshed
generation advanced and its readable manifest plus canonical blocks form a
trusted Evidence context, the response loss is reported as a non-blocking
`RUN_RESPONSE_LOST` notice instead of a failed run. Evidence fingerprint
continues to identify deterministic Evidence content and is deliberately not
used as proof that a run executed: first runs and deterministic reruns may
produce unchanged content. An unchanged generation or untrusted Evidence
context keeps the original network error visible. If the run succeeds but the
follow-up task read fails, the run response's status, generation, and manifest
provide the fallback snapshot used to load the rebuilt Evidence.

Only a task whose status is explicitly `created` or `uploaded` and whose
generation is still zero is treated as a first run without destructive
confirmation. Completed, failed, `status_unavailable`, previously run uploads,
and unknown states all require confirmation because review artifacts may still
exist. Cancelling confirmation leaves the current ReqIR, blocks, source
selection, notices, and errors untouched.

The authored-marker v1 grammar is intentionally strict and fail-closed. It
accepts an ASCII `Table <token>` root label plus `Table <token> (continued)`,
`Table <token> - continued`, or `Table <token>: continued` on later pages.
Mismatched labels, duplicate nearby markers, markers beyond the allowed
distance, em-dash/CJK variants, and other unrecognized forms leave the tables
independent. They are not inferred from header geometry alone.

The checked `pdf_table_continuation.pdf` fixture contains a three-page chain.
Acceptance proves page-local table identities, root header lineage, prompt
source canonicalization, quote matching, `structured_required`, page/cell
locators, `table_evidence_view_v1`, and the Review UI continuation label. A
Linux Chromium baseline fixes page 2's real PDF overlay, selected local cells,
root-lineage label, continued header mapping, capability diagnostics, and
canonical block fallback in one browser screenshot.

## Next acceptance steps

- expand `pdf_corpus_v1` beyond its initial Microsoft Word seed with
  redistribution-reviewed files from additional PDF producers and real
  customer samples;
- expose preview metadata separately if non-PDF renderers are introduced;
- distinguish running decoration from repeated contextual headings in PDF
  section inference, using the report-only corpus heading metrics as the
  baseline.
