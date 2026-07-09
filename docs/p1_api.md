# SpecTrail P1 API Demo

P1 exposes the deterministic P0 Markdown pipeline through a local FastAPI service.

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

Read ReqIR:

```bash
curl http://127.0.0.1:8000/api/tasks/{task_id}/reqir
```

## Review

Approve:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/review \
  -H "Content-Type: application/json" \
  -d '{"requirement_id":"REQ-0001","action":"approve","reviewer":"local"}'
```

Edit:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/{task_id}/review \
  -H "Content-Type: application/json" \
  -d '{"requirement_id":"REQ-0003","action":"edit","patch":{"statement":"系统应记录完整的用户账号状态变更审计信息。"},"reviewer":"local"}'
```

## Exports

```bash
curl -L http://127.0.0.1:8000/api/tasks/{task_id}/exports/reqir.json \
  -o reqir.json

curl -L http://127.0.0.1:8000/api/tasks/{task_id}/exports/requirements.xlsx \
  -o requirements.xlsx
```

## Current Non-Goals

P1 does not include DOCX/PDF input, OCR, a database, async queues, authentication, a frontend UI, live LLM calls, Agent Planner, Gherkin, ReqIF, or SysML.

## Common Errors

```text
TASK_NOT_FOUND        task_id does not exist
DOCUMENT_NOT_UPLOADED run was requested before Markdown upload
TASK_NOT_COMPLETED    ReqIR/review was requested before a completed run
INVALID_DOCUMENT      upload was not .md or .markdown
INVALID_REVIEW_ACTION review action or patch is not allowed
EXPORT_NOT_FOUND      requested export file is missing
```
