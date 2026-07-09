# SpecTrail P0 Demo

This demo runs the deterministic Markdown-first pipeline:

```text
Markdown -> ReqIR -> Source Quote Validation -> Human Review -> JSON / Excel Export
```

## Install

```bash
python -m pip install -e ".[dev]"
```

## Run Extract

```bash
python -m spectrail extract docs/sample_srs.md --model-mode mock --output outputs/demo
```

The command writes:

```text
outputs/demo/plan.json
outputs/demo/run_manifest.json
outputs/demo/parsed/document.md
outputs/demo/parsed/blocks.json
outputs/demo/extracted/reqir.raw.json
outputs/demo/extracted/reqir.validated.json
outputs/demo/extracted/source_map.json
outputs/demo/extracted/validation_report.json
outputs/demo/review/review_log.json
outputs/demo/exports/reqir.json
outputs/demo/exports/requirements.xlsx
```

## Re-run Validation

```bash
python -m spectrail validate outputs/demo/extracted/reqir.raw.json \
  --blocks outputs/demo/parsed/blocks.json \
  --output outputs/demo/extracted/validation_report.json \
  --validated-output outputs/demo/extracted/reqir.validated.json
```

## Review One Requirement

```bash
python -m spectrail review outputs/demo --id REQ-0001 --action approve --reviewer local
python -m spectrail review outputs/demo --id REQ-0002 --action reject --reviewer local --reason "not required"
python -m spectrail review outputs/demo --id REQ-0002 --action restore --reviewer local
```

For an edit action, write a small patch file:

```json
{
  "statement": "当用户提交有效登录凭证时，系统应完成身份验证并建立会话。"
}
```

Then run:

```bash
python -m spectrail review outputs/demo --id REQ-0003 --action edit --patch patches/req-0003.json --reviewer local
```

Structural edits such as `statement` move the requirement to `needs_recheck`.
Non-structural edits such as `tags`, `priority`, and `metadata` keep the current review status.

## Export Excel

```bash
python -m spectrail export outputs/demo/exports/reqir.json --format xlsx --output outputs/demo/exports/requirements.xlsx
```

## Run Tests

```bash
pytest
```

## Current Non-goals

SpecTrail P0 does not include FastAPI, database storage, DOCX/PDF parsing, live LLM calls, Agent Planner, Gherkin, ReqIF, or SysML export.
