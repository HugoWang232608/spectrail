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
Cache-Control: private, no-store
X-Spectrail-Preview-Width: <rendered pixels>
X-Spectrail-Preview-Height: <rendered pixels>
```

The endpoint:

- requires a completed or completed-with-warnings task;
- renders only the task's uploaded PDF;
- resolves and contains the document path within the task directory;
- participates in the task transaction guard;
- uses RGB output without transparency;
- caps scale at 2× and either output dimension at 2000 pixels.

Errors remain structured:

```text
TASK_NOT_FOUND
TASK_NOT_COMPLETED
PAGE_PREVIEW_NOT_FOUND
PAGE_PREVIEW_UNAVAILABLE
TASK_TRANSACTION_LOCKED
TASK_MIGRATION_INCOMPLETE
```

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
text evidence view.

Run the frontend evidence tests and production build with:

```bash
cd frontend
npm test
npm run build
```

## Next acceptance steps

- add focused table-cell visualization for DOCX row-group blocks;
- add frontend component tests for overlay math and failure/retry states;
- add visual regression fixtures for 0°, 90°, 180°, and 270° PDF pages;
- expose preview metadata separately if non-PDF renderers are introduced;
- distinguish running decoration from repeated contextual headings in PDF
  section inference.
