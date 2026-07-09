from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import TypeAdapter

from spectrail.core.io import model_list_dump, read_json, write_json
from spectrail.core.models import DocumentBlock, RequirementIR, ValidationReport
from spectrail.exporters.xlsx_exporter import export_requirements_xlsx
from spectrail.pipeline import PipelineError, PipelineRunner
from spectrail.review.service import apply_review_to_package, load_requirements, refresh_review_package
from spectrail.validators.ears_validator import BasicEARSValidator
from spectrail.validators.schema_validator import SchemaValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator


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
    try:
        result = PipelineRunner().extract(
            document_path=args.document,
            output_dir=args.output,
            model_mode=args.model_mode,
            model_name=args.model_name,
        )
    except PipelineError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Generated {result.validated_count} requirements in {result.output_dir}")
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
    review_log_path = output / "review" / "review_log.json"
    xlsx_path = output / "exports" / "requirements.xlsx"

    if args.requirement_id:
        if not args.action:
            raise SystemExit("--action is required when --id is provided")
        patch = read_json(args.patch) if args.patch else None
        try:
            apply_review_to_package(
                reqir_path=reqs_path,
                review_log_path=review_log_path,
                xlsx_path=xlsx_path,
                requirement_id=args.requirement_id,
                action=args.action,
                patch=patch,
                reviewer=args.reviewer,
                reason=args.reason,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Applied {args.action} to {args.requirement_id}")
        return 0

    reqs = load_requirements(reqs_path)
    refresh_review_package(reqs_path, review_log_path, xlsx_path, reqs)
    print(f"Refreshed review outputs for {len(reqs)} requirements")
    return 0


def _load_requirements(path: str | Path) -> list[RequirementIR]:
    return load_requirements(path)


def merge_reports(*reports: ValidationReport) -> ValidationReport:
    merged = ValidationReport(valid=True)
    for report in reports:
        for issue in report.issues:
            merged.add_issue(issue)
    return merged
