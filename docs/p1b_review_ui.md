# SpecTrail P1b Review UI Demo

P1b adds a minimal browser UI for the task API. It keeps the backend pipeline unchanged and provides the same local review workflow for Markdown, DOCX, and text-based PDF input.

## Install

```bash
python -m pip install -e ".[dev]"

cd frontend
npm install
```

## Start

Start the API from the repository root:

```bash
uvicorn spectrail.api.app:app --reload
```

Start the frontend:

```bash
cd frontend
npm run dev
```

Open:

```text
http://127.0.0.1:5173/
```

The frontend uses `/api` through the Vite dev proxy by default. You can also set:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000/api npm run dev
```

## Demo Flow

```text
1. Create Task
2. Upload docs/sample_srs.md
3. Run Pipeline
4. Select a ReqIR item
5. Review statement, status, source quote, and block text
6. Approve / reject / restore a requirement
7. Edit statement, tags, or priority
8. Download reqir.json or requirements.xlsx
```

Expected result:

```text
Task status is completed
ReqIR table shows at least 14 items
Source viewer shows quote and block text
Exact source matches highlight the quote in block text
Review summary updates after approve / reject / restore / edit
Exports download from the browser
```

## Verification

```bash
pytest

cd frontend
npm run build
```

## Current Non-Goals

P1b does not include login, a database, async queues, multi-document tasks, OCR or scanned-PDF support, complex PDF viewing, Agent Planner, Gherkin, ReqIF, SysML, or production deployment.
