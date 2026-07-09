from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from pydantic import TypeAdapter

from spectrail.core.io import ensure_dir, model_list_dump, read_json, write_json
from spectrail.core.manifest import complete_manifest, fail_manifest, init_manifest
from spectrail.core.models import DocumentBlock, RequirementIR, ValidationReport
from spectrail.core.workflow import build_fixed_plan
from spectrail.exporters.source_map_exporter import build_source_map
from spectrail.exporters.xlsx_exporter import export_requirements_xlsx
from spectrail.extractors.ears_normalizer import normalize_requirements
from spectrail.extractors.reqir_extractor import ReqIRExtractor
from spectrail.llm.mock_model import MockModel
from spectrail.parsers.markdown_parser import MarkdownParser
from spectrail.review.review_log import collect_review_log
from spectrail.review.review_state import apply_review_action
from spectrail.validators.ears_validator import BasicEARSValidator
from spectrail.validators.schema_validator import SchemaValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator


ReqListAdapter = TypeAdapter(list[RequirementIR])
BlockListAdapter = TypeAdapter(list[DocumentBlock])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spectrail")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="run Markdown -> ReqIR pipeline")
    extract_parser.add_argument("document")
    extract_parser.add_argument("--model-mode", choices=["mock", "recorded", "live"], default="mock")
    extract_parser.add_argument("--model-name", default=None)
    extract_parser.add_argument("--output", default="outputs/demo")
    extract_parser.set_defaults(func=run_extract)

    validate_parser = subparsers.add_parser("validate", help="validate ReqIR against parsed blocks")
    validate_parser.add_argument("reqir")
    validate_parser.add_argument("--blocks", required=True)
    validate_parser.add_argument("--output", default=None)
    validate_parser.add_argument("--validated-output", default=None)
    validate_parser.set_defaults(func=run_validate)

    export_parser = subparsers.add_parser("export", help="export ReqIR to xlsx")
    export_parser.add_argument("reqir")
    export_parser.add_argument("--format", choices=["xlsx"], default="xlsx")
    export_parser.add_argument("--output", required=True)
    export_parser.set_defaults(func=run_export)

    review_parser = subparsers.add_parser("review", help="refresh review outputs from exported ReqIR")
    review_parser.add_argument("output_dir")
    review_parser.add_argument("--id", dest="requirement_id", default=None)
    review_parser.add_argument(
        "--action",
        choices=["approve", "reject", "edit", "restore", "request_recheck"],
        default=None,
    )
    review_parser.add_argument("--patch", default=None)
    review_parser.add_argument("--reviewer", default=None)
    review_parser.add_argument("--reason", default=None)
    review_parser.set_defaults(func=run_review)

    args = parser.parse_args(argv)
    return args.func(args)


def run_extract(args: argparse.Namespace) -> int:
    if args.model_mode != "mock":
        raise SystemExit("P0 currently supports --model-mode mock for deterministic local runs")

    document_path = Path(args.document)
    output = Path(args.output)
    parsed_dir = ensure_dir(output / "parsed")
    extracted_dir = ensure_dir(output / "extracted")
    review_dir = ensure_dir(output / "review")
    exports_dir = ensure_dir(output / "exports")

    task_id = output.name
    plan = build_fixed_plan(
        task_id=task_id,
        input_document=document_path.as_posix(),
        model_mode=args.model_mode,
    )
    write_json(output / "plan.json", plan.model_dump(mode="json"))
    manifest = init_manifest(
        task_id=task_id,
        input_document=document_path.as_posix(),
        output_dir=output.as_posix(),
        model_mode=args.model_mode,
    )
    write_json(output / "run_manifest.json", manifest)

    copied_document = parsed_dir / "document.md"
    shutil.copyfile(document_path, copied_document)

    parser = MarkdownParser()
    blocks = parser.parse_file(document_path)
    write_json(parsed_dir / "blocks.json", model_list_dump(blocks))

    payload = MockModel().generate(document_path.read_text(encoding="utf-8"))
    extractor = ReqIRExtractor()
    requirements = extractor.extract(
        payload=payload,
        blocks=blocks,
        document_name=document_path.name,
        model_mode=args.model_mode,
    )
    requirements = normalize_requirements(requirements)
    write_json(
        extracted_dir / "reqir.raw.json",
        {
            "metadata": {"model_mode": args.model_mode, "document": document_path.name},
            "items": model_list_dump(requirements),
        },
    )

    schema_report = SchemaValidator().validate(requirements)
    if not schema_report.valid:
        write_json(extracted_dir / "validation_report.json", schema_report.model_dump(mode="json"))
        write_json(
            output / "run_manifest.json",
            fail_manifest(manifest, "schema validation failed"),
        )
        raise SystemExit("schema validation failed")

    validated_requirements, source_report = SourceQuoteValidator().validate(requirements, blocks)
    ears_report = BasicEARSValidator().validate(validated_requirements)
    validation_report = merge_reports(schema_report, source_report, ears_report)
    write_json(extracted_dir / "validation_report.json", validation_report.model_dump(mode="json"))
    if not source_report.valid:
        write_json(
            output / "run_manifest.json",
            fail_manifest(manifest, "source quote validation failed"),
        )
        raise SystemExit("source quote validation failed")

    write_json(
        extracted_dir / "reqir.validated.json",
        {
            "metadata": {"validation_state": "validated", "document": document_path.name},
            "items": model_list_dump(validated_requirements),
        },
    )
    write_json(extracted_dir / "source_map.json", build_source_map(validated_requirements))
    write_json(review_dir / "review_log.json", collect_review_log(validated_requirements))
    write_json(
        exports_dir / "reqir.json",
        {
            "metadata": {"export_state": "unreviewed_snapshot", "document": document_path.name},
            "items": model_list_dump(validated_requirements),
        },
    )
    export_requirements_xlsx(validated_requirements, exports_dir / "requirements.xlsx")
    write_json(
        output / "run_manifest.json",
        complete_manifest(
            manifest,
            counts={
                "blocks": len(blocks),
                "raw_requirements": len(requirements),
                "validated_requirements": len(validated_requirements),
                "source_quote_passed": len(validated_requirements),
                "source_quote_failed": len(requirements) - len(validated_requirements),
            },
            outputs={
                "document": "parsed/document.md",
                "blocks": "parsed/blocks.json",
                "reqir_raw": "extracted/reqir.raw.json",
                "reqir_validated": "extracted/reqir.validated.json",
                "source_map": "extracted/source_map.json",
                "validation_report": "extracted/validation_report.json",
                "review_log": "review/review_log.json",
                "reqir_export": "exports/reqir.json",
                "xlsx": "exports/requirements.xlsx",
            },
        ),
    )

    print(f"Generated {len(validated_requirements)} requirements in {output}")
    return 0


def run_validate(args: argparse.Namespace) -> int:
    reqs = _load_requirements(args.reqir)
    blocks = BlockListAdapter.validate_python(read_json(args.blocks))
    schema_report = SchemaValidator().validate(reqs)
    validated, source_report = SourceQuoteValidator().validate(reqs, blocks)
    report = merge_reports(schema_report, source_report, BasicEARSValidator().validate(validated))
    if args.output:
        write_json(args.output, report.model_dump(mode="json"))
    if args.validated_output:
        write_json(
            args.validated_output,
            {
                "metadata": {"validation_state": "validated"},
                "items": model_list_dump(validated),
            },
        )
    print(report.model_dump_json(indent=2))
    return 0 if report.valid else 1


def run_export(args: argparse.Namespace) -> int:
    reqs = _load_requirements(args.reqir)
    export_requirements_xlsx(reqs, args.output)
    print(f"Exported {len(reqs)} requirements to {args.output}")
    return 0


def run_review(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    reqs_path = output / "exports" / "reqir.json"
    reqs = _load_requirements(reqs_path)

    if args.requirement_id:
        if not args.action:
            raise SystemExit("--action is required when --id is provided")
        patch = read_json(args.patch) if args.patch else None
        target = next((req for req in reqs if req.id == args.requirement_id), None)
        if target is None:
            raise SystemExit(f"requirement not found: {args.requirement_id}")
        try:
            apply_review_action(
                target,
                args.action,
                patch=patch,
                reviewer=args.reviewer,
                reason=args.reason,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    write_json(output / "review" / "review_log.json", collect_review_log(reqs))
    write_json(
        reqs_path,
        {"metadata": {"export_state": "review_snapshot"}, "items": model_list_dump(reqs)},
    )
    export_requirements_xlsx(reqs, output / "exports" / "requirements.xlsx")
    if args.requirement_id:
        print(f"Applied {args.action} to {args.requirement_id}")
    else:
        print(f"Refreshed review outputs for {len(reqs)} requirements")
    return 0


def _load_requirements(path: str | Path) -> list[RequirementIR]:
    payload = read_json(path)
    if isinstance(payload, dict) and "items" in payload:
        payload = payload["items"]
    return ReqListAdapter.validate_python(payload)


def merge_reports(*reports: ValidationReport) -> ValidationReport:
    merged = ValidationReport(valid=True)
    for report in reports:
        for issue in report.issues:
            merged.add_issue(issue)
    return merged
