# SpecTrail

SpecTrail P0 is a local, Markdown-first pipeline that turns a requirements document into grounded ReqIR JSON and Excel exports.

## P0 Demo

Install dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the deterministic mock pipeline:

```bash
python -m spectrail extract docs/sample_srs.md --model-mode mock --output outputs/demo
```

Expected outputs:

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

Re-run validation and write the report:

```bash
python -m spectrail validate outputs/demo/extracted/reqir.raw.json \
  --blocks outputs/demo/parsed/blocks.json \
  --output outputs/demo/extracted/validation_report.json
```

Apply a review action:

```bash
python -m spectrail review outputs/demo --id REQ-0001 --action approve --reviewer local
```

Run tests:

```bash
pytest
```
