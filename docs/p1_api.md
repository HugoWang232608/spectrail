# SpecTrail P1 API Demo

P1 introduced the deterministic P0 Markdown pipeline through a local FastAPI
service. The examples below follow the current API concurrency contract:
pipeline artifacts are read by `run_generation`, and requirement mutations are
additionally guarded by each item's `review_revision`.

## Install

```bash
python -m pip install -e ".[dev]"
```

## Start

```bash
uvicorn spectrail.api.app:app --reload
```

## Flow

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

Run:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/run
```

Check status:

```bash
curl http://127.0.0.1:8000/api/tasks/{task_id}
```

Save the returned `run_generation`. The examples below use generation `1`.

Read ReqIR:

```bash
curl "http://127.0.0.1:8000/api/tasks/{task_id}/reqir?expected_run_generation=1"
```

The response repeats the accepted generation in
`X-Spectrail-Run-Generation`. Each ReqIR item contains a `review_revision`;
newly extracted items start at revision `0`.

## Review

Approve:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/review \
  -H "Content-Type: application/json" \
  -d '{"requirement_id":"REQ-0001","expected_run_generation":1,"expected_review_revision":0,"action":"approve","reviewer":"local"}'
```

Edit:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/review \
  -H "Content-Type: application/json" \
  -d '{"requirement_id":"REQ-0003","expected_run_generation":1,"expected_review_revision":0,"action":"edit","patch":{"statement":"系统应记录完整的用户账号状态变更审计信息。"},"reviewer":"local"}'
```

The server checks generation and revision while holding the same task
transaction used to update ReqIR, the review log, and the Excel export. A
successful response returns the actual `run_generation` and incremented
`review_revision`.

## Exports

```bash
curl -L "http://127.0.0.1:8000/api/tasks/{task_id}/exports/reqir.json?expected_run_generation=1" \
  -o reqir.json

curl -L "http://127.0.0.1:8000/api/tasks/{task_id}/exports/requirements.xlsx?expected_run_generation=1" \
  -o requirements.xlsx
```

Export responses repeat the accepted generation in
`X-Spectrail-Run-Generation`.

## Current Non-Goals

P1 does not include DOCX/PDF input, OCR, a database, async queues, authentication, a frontend UI, live LLM calls, Agent Planner, Gherkin, ReqIF, or SysML.

## Common Errors

```text
TASK_NOT_FOUND        task_id does not exist
DOCUMENT_NOT_UPLOADED run was requested before Markdown upload
TASK_NOT_COMPLETED    ReqIR/review was requested before a completed run
INVALID_DOCUMENT      upload was not .md or .markdown
INVALID_REVIEW_ACTION review action or patch is not allowed
RUN_GENERATION_CHANGED the task was rerun after the client loaded it
REVIEW_REVISION_CHANGED the requirement changed after the client loaded it
TASK_REVIEW_RECOVERY_REQUIRED an interrupted review publication must be recovered
EXPORT_NOT_FOUND      requested export file is missing
```
