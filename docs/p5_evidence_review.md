# P5 Evidence Review

P5 turns the typed Evidence locators into reviewer-visible source context. The
first vertical slice covers validated PDF page regions while preserving the
existing text-first review path.

## Current flow

```text
ReqIR SourceSpan.page_locator
  -> task-scoped PDF preview endpoint
  -> rotated page PNG
  -> proportional bbox overlay
  -> locator and capability diagnostics
```

The page preview uses the same
`pdf_preview_rotated_points_top_left_v1` coordinate space as `PageLocator`.
Preview scaling therefore does not change source identity or locator geometry;
the browser converts the bbox to percentages of `page_width` and `page_height`.

## API contract

```text
GET /api/tasks/{task_id}/pages/{page_number}/preview.png
```

Successful responses include:

```text
Content-Type: image/png
Cache-Control: private, max-age=300
X-Spectrail-Preview-Width: <rendered pixels>
X-Spectrail-Preview-Height: <rendered pixels>
```

The endpoint:

- requires a completed or completed-with-warnings task;
- renders only the task's uploaded PDF;
- resolves and contains the document path within the task directory;
- participates in the task transaction guard;
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
TASK_TRANSACTION_LOCKED
TASK_MIGRATION_INCOMPLETE
```

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
- table and canonical cell IDs when present;
- validation status for `text_range`, `page_region`, and `table_cell`;
- highlighted canonical block text as the fallback evidence view.

Canonical block highlighting uses the final `TextLocator` before considering the
legacy exact-quote fallback. Offsets are applied to `Array.from(text)` so the
`unicode_code_point` contract remains correct for emoji and supplementary CJK
characters. A normalized match with a valid locator is highlighted even when
the displayed canonical range is not byte-for-byte equal to `source.quote`.

Preview loading occurs only after ReqIR and block reads complete, avoiding
read-lock races. A failed image can be retried explicitly without losing the
text evidence view. The preview URL includes `source_evidence_key`, whose
identity includes the Evidence fingerprint, so the private browser cache cannot
reuse a page image across pipeline runs with different source evidence.
Preview failure state is also keyed by the selected source; legacy sources use
their block, text range, and quote so a failed same-page source cannot poison
the next source.

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

Run the frontend evidence tests and production build with:

```bash
cd frontend
npm test
npm run build
```

GitHub Actions runs `npm test` before the production frontend build. Component
coverage verifies final locator highlighting, source and requirement changes,
preview failure and retry, proportional overlay geometry, locator/block
mismatch behavior, and all four supported page rotations.

Backend geometry acceptance renders real PDFs at 0°, 90°, 180°, and 270°,
derives each `PageLocator` through the parser and enricher, decodes the same PNG
renderer used by the preview API, and confirms that the locator's proportional
pixel region contains the quoted glyphs. The renderer is the public, stateless
`spectrail.evidence.pdf_preview.render_pdf_page` function shared by TaskStore
and acceptance tests. Browser-level screenshot regression remains separate
from this deterministic cross-layer check.

## Next acceptance steps

- add focused table-cell visualization for DOCX row-group blocks;
- add pixel-based visual regression fixtures for 0°, 90°, 180°, and 270° PDF
  pages;
- expose preview metadata separately if non-PDF renderers are introduced;
- distinguish running decoration from repeated contextual headings in PDF
  section inference.
