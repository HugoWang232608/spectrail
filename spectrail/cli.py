from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import TypeAdapter

from spectrail.core.io import read_json, reqir_package_dump, write_json
from spectrail.core.models import DocumentBlock, RequirementIR, ValidationReport
from spectrail.evidence import (
    EvidenceIndex,
    QuoteMatchRegistry,
    build_quote_match_registry,
    sha256_text,
    validate_source_evidence_keys,
    validate_evidence_fingerprint,
)
from spectrail.evidence.index_builder import (
    validate_evidence_index_against_parsed_document,
)
from spectrail.evidence.source_identity import canonicalize_source_cell_ids
from spectrail.exporters.xlsx_exporter import export_requirements_xlsx
from spectrail.llm.errors import ModelError
from spectrail.migrations import migrate_task
from spectrail.parsers import DocumentParseError, ParsedDocument
from spectrail.pipeline import PipelineError, PipelineRunner
from spectrail.evaluation.runner import EvaluationRunner
from spectrail.review.service import (
    apply_review_to_package,
    load_requirement_package,
    load_requirements,
    refresh_review_package,
)
from spectrail.validators.ears_validator import BasicEARSValidator
from spectrail.validators.schema_validator import SchemaValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator
from spectrail.validators.source_locator_validator import SourceLocatorValidator


BlockListAdapter = TypeAdapter(list[DocumentBlock])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spectrail")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="run document -> ReqIR pipeline")
    extract_parser.add_argument("document", help="input document path (.md, .markdown, .docx, or text-based .pdf)")
    extract_parser.add_argument("--model-mode", choices=["mock", "recorded", "live"], default="mock")
    extract_parser.add_argument("--model-name", default=None)
    extract_parser.add_argument("--recorded-fixture", default=None)
    extract_parser.add_argument("--dump-prompt", action="store_true")
    extract_parser.add_argument("--chunking", choices=["off", "auto", "force"], default="auto")
    extract_parser.add_argument("--max-rendered-prompt-chars", type=int, default=16000)
    extract_parser.add_argument("--overlap-blocks", type=int, default=1)
    extract_parser.add_argument("--validation-policy", choices=["strict", "quarantine"], default="strict")
    extract_parser.add_argument(
        "--evidence-policy",
        choices=["quote_only", "structured_if_available", "structured_required"],
        default="structured_if_available",
    )
    extract_parser.add_argument("--fail-fast", action="store_true")
    extract_parser.add_argument(
        "--insecure",
        action="store_true",
        help="live mode only: skip TLS certificate verification for provider requests",
    )
    extract_parser.add_argument("--output", default="outputs/demo")
    extract_parser.set_defaults(func=run_extract)

    validate_parser = subparsers.add_parser("validate", help="validate ReqIR against parsed blocks")
    validate_parser.add_argument("reqir")
    validate_parser.add_argument("--blocks", required=True)
    validate_parser.add_argument("--quote-matches", default=None)
    validate_parser.add_argument("--rebuild-quote-matches", action="store_true")
    validate_parser.add_argument("--quote-matches-output", default=None)
    validate_parser.add_argument("--evidence-index", default=None)
    validate_parser.add_argument("--output", default=None)
    validate_parser.add_argument("--validated-output", default=None)
    validate_parser.add_argument(
        "--evidence-policy",
        choices=["quote_only", "structured_if_available", "structured_required"],
        default="structured_if_available",
    )
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

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="migrate persisted task artifacts to current schemas",
    )
    migrate_parser.add_argument("task_dir")
    migrate_parser.set_defaults(func=run_migrate)

    evaluate_parser = subparsers.add_parser("evaluate", help="run deterministic extraction evaluation")
    evaluate_parser.add_argument("case", help="case.json or directory containing evaluation cases")
    evaluate_parser.add_argument("--output", default="outputs/evaluation")
    evaluate_parser.set_defaults(func=run_evaluate)

    args = parser.parse_args(argv)
    return args.func(args)


def run_extract(args: argparse.Namespace) -> int:
    try:
        result = PipelineRunner().extract(
            document_path=args.document,
            output_dir=args.output,
            model_mode=args.model_mode,
            model_name=args.model_name,
            recorded_fixture=args.recorded_fixture,
            dump_prompt=args.dump_prompt,
            insecure=args.insecure,
            chunking_mode=args.chunking,
            max_rendered_prompt_chars=args.max_rendered_prompt_chars,
            overlap_blocks=args.overlap_blocks,
            validation_policy=args.validation_policy,
            evidence_policy=args.evidence_policy,
            fail_fast=args.fail_fast,
        )
    except (PipelineError, DocumentParseError, ModelError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Generated {result.validated_count} requirements in {result.output_dir}")
    return 0


def run_evaluate(args: argparse.Namespace) -> int:
    try:
        report = EvaluationRunner().run(args.case, args.output)
    except (ValueError, PipelineError, DocumentParseError, ModelError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Evaluated {report['case_count']} case(s): {report['case_passed']} passed")
    return 0 if report["passed"] else 1


def run_validate(args: argparse.Namespace) -> int:
    if args.quote_matches_output and not args.rebuild_quote_matches:
        raise ValueError(
            "--quote-matches-output requires --rebuild-quote-matches"
        )
    try:
        reqs = _load_requirements(args.reqir)
    except ValueError as exc:
        message = str(exc)
        if message.startswith("REQIR_V3_") or "REQIR_LEGACY_" in message:
            task_dir = Path(args.reqir).parent.parent
            raise ValueError(
                "legacy ReqIR requires migration; run: "
                f"spectrail migrate {task_dir}"
            ) from exc
        raise
    blocks = BlockListAdapter.validate_python(read_json(args.blocks))
    schema_report = SchemaValidator().validate(reqs)
    quote_matches_path, evidence_index_path = _validation_artifact_paths(
        Path(args.reqir),
        quote_matches=args.quote_matches,
        evidence_index=args.evidence_index,
    )
    evidence_index = (
        EvidenceIndex.model_validate(read_json(evidence_index_path))
        if evidence_index_path is not None
        else None
    )
    if evidence_index is not None:
        validate_evidence_fingerprint(evidence_index)
        parsed_document = ParsedDocument(
            document_id=evidence_index.document_id,
            document_name=evidence_index.document_name,
            source_format=evidence_index.source_format,
            parser_name=evidence_index.parser_identity.parser_name,
            text="\n\n".join(block.text for block in blocks),
            blocks=blocks,
            parser_identity=evidence_index.parser_identity,
        )
        validate_evidence_index_against_parsed_document(
            evidence_index,
            parsed_document,
        )
        canonicalize_source_cell_ids(reqs, evidence_index)
    if args.rebuild_quote_matches:
        validation_fingerprint = (
            evidence_index.evidence_fingerprint
            if evidence_index is not None
            else sha256_text(
                "\n".join(
                    f"{block.document_id}:{block.block_id}:{block.text}"
                    for block in blocks
                )
            )
        )
        try:
            quote_matches = build_quote_match_registry(
                reqs,
                blocks,
                evidence_fingerprint=validation_fingerprint,
                evidence_index=evidence_index,
            )
        except ValueError as exc:
            if "source_evidence_key does not match" in str(exc):
                raise ValueError(
                    "source keys require migration; run spectrail migrate <task_dir>"
                ) from exc
            raise
        rebuild_output = Path(args.quote_matches_output) if args.quote_matches_output else (
            quote_matches_path
            or Path(args.reqir).parent.parent / "extracted" / "quote_matches.json"
        )
        write_json(rebuild_output, quote_matches.model_dump(mode="json"))
    elif quote_matches_path is not None:
        try:
            quote_matches = QuoteMatchRegistry.model_validate(
                read_json(quote_matches_path)
            )
        except ValueError as exc:
            if "QUOTE_MATCHES_V2_REBUILD_REQUIRED" in str(exc):
                raise ValueError(
                    "quote_matches_v2 requires rebuilding; rerun validate with "
                    "--rebuild-quote-matches"
                ) from exc
            raise
    else:
        validation_fingerprint = (
            evidence_index.evidence_fingerprint
            if evidence_index is not None
            else sha256_text(
                "\n".join(
                    f"{block.document_id}:{block.block_id}:{block.text}"
                    for block in blocks
                )
            )
        )
        quote_matches = build_quote_match_registry(
            reqs,
            blocks,
            evidence_fingerprint=validation_fingerprint,
            evidence_index=evidence_index,
        )
    if evidence_index is not None:
        validate_source_evidence_keys(
            reqs,
            evidence_fingerprint=evidence_index.evidence_fingerprint,
            bind_missing=True,
        )
    for item in reqs:
        for source in item.sources:
            if source.source_evidence_key is None:
                raise ValueError("source_evidence_key is required after registry loading")
            quote_matches.require(source.source_evidence_key)
    validated, source_report = SourceQuoteValidator().validate(
        reqs,
        blocks,
        quote_matches,
    )
    if evidence_index is None:
        if args.evidence_policy != "quote_only":
            raise ValueError(
                "evidence index is required unless --evidence-policy quote_only is used"
            )
        locator_validated = reqs
        locator_report = ValidationReport(valid=True)
    else:
        locator_validated, locator_report, _ = SourceLocatorValidator().validate(
            reqs,
            evidence_index,
            quote_matches,
            policy=args.evidence_policy,
            document_blocks=blocks,
        )
    valid_ids = {item.id for item in validated} & {item.id for item in locator_validated}
    validated = [item for item in reqs if item.id in valid_ids]
    report = merge_reports(
        schema_report,
        source_report,
        locator_report,
        BasicEARSValidator().validate(validated),
    )
    if args.output:
        write_json(args.output, report.model_dump(mode="json"))
    if args.validated_output:
        write_json(
            args.validated_output,
            reqir_package_dump(
                validated,
                metadata={"validation_state": "validated"},
            ),
        )
    print(report.model_dump_json(indent=2))
    return 0 if report.valid else 1


def run_migrate(args: argparse.Namespace) -> int:
    try:
        report = migrate_task(args.task_dir)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        "Migrated "
        f"{report['reqir_packages']} ReqIR package(s), "
        f"rebound {report['rebound_sources']} source(s), and wrote "
        f"{report['quote_matches_schema_version']}"
    )
    return 0


def _validation_artifact_paths(
    reqir_path: Path,
    *,
    quote_matches: str | None,
    evidence_index: str | None,
) -> tuple[Path | None, Path | None]:
    explicit_quote = Path(quote_matches) if quote_matches else None
    explicit_evidence = Path(evidence_index) if evidence_index else None
    if explicit_quote is not None and not explicit_quote.exists():
        raise ValueError(f"quote matches artifact not found: {explicit_quote}")
    if explicit_evidence is not None and not explicit_evidence.exists():
        raise ValueError(f"evidence index artifact not found: {explicit_evidence}")

    task_dir = reqir_path.parent.parent
    manifest_path = task_dir / "run_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    outputs = manifest.get("outputs", {})

    quote_candidates = [
        reqir_path.parent / "quote_matches.json",
        task_dir / outputs.get("quote_matches", "extracted/quote_matches.json"),
    ]
    evidence_candidates = [
        reqir_path.parent / "evidence_index.json",
        task_dir / outputs.get("evidence_index", "parsed/evidence_index.json"),
    ]
    resolved_quote = explicit_quote or next(
        (path for path in quote_candidates if path.exists()), None
    )
    resolved_evidence = explicit_evidence or next(
        (path for path in evidence_candidates if path.exists()), None
    )
    return resolved_quote, resolved_evidence


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
            raise SystemExit(_review_error_message(exc, output)) from exc
        print(f"Applied {args.action} to {args.requirement_id}")
        return 0

    try:
        package = load_requirement_package(reqs_path)
    except ValueError as exc:
        raise SystemExit(_review_error_message(exc, output)) from exc
    refresh_review_package(
        reqs_path,
        review_log_path,
        xlsx_path,
        package.items,
        metadata=package.metadata,
    )
    print(f"Refreshed review outputs for {len(package.items)} requirements")
    return 0


def _review_error_message(exc: ValueError, output: Path) -> str:
    message = str(exc)
    if message.startswith("REQIR_V3_") or "REQIR_LEGACY_" in message:
        return (
            f"legacy task artifacts require migration; run: "
            f"spectrail migrate {output}"
        )
    return message


def _load_requirements(path: str | Path) -> list[RequirementIR]:
    return load_requirements(path)


def merge_reports(*reports: ValidationReport) -> ValidationReport:
    merged = ValidationReport(valid=True)
    for report in reports:
        for issue in report.issues:
            merged.add_issue(issue)
    return merged
