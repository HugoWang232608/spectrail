from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from spectrail.core.io import ensure_dir, model_list_dump, write_json
from spectrail.core.manifest import complete_manifest, fail_manifest, init_manifest
from spectrail.core.models import ValidationIssue, ValidationReport
from spectrail.core.workflow import build_fixed_plan
from spectrail.exporters.source_map_exporter import build_source_map
from spectrail.exporters.xlsx_exporter import export_requirements_xlsx
from spectrail.extractors.ears_normalizer import normalize_requirements
from spectrail.extractors.reqir_extractor import ReqIRExtractor
from spectrail.llm.base import ModelRequest
from spectrail.llm.factory import create_model_client
from spectrail.llm.prompt_builder import PROMPT_VERSION, build_reqir_prompt
from spectrail.parsers.registry import parse_document
from spectrail.review.review_log import collect_review_log
from spectrail.validators.ears_validator import BasicEARSValidator
from spectrail.validators.schema_validator import SchemaValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator


class PipelineError(ValueError):
    pass


class UnsupportedModelModeError(PipelineError):
    pass


class PipelineValidationError(PipelineError):
    pass


@dataclass(frozen=True)
class PipelineResult:
    task_id: str
    output_dir: Path
    plan_path: Path
    manifest_path: Path
    validated_reqir_path: Path
    exported_reqir_path: Path
    xlsx_path: Path
    validated_count: int


class PipelineRunner:
    def extract(
        self,
        document_path: str | Path,
        output_dir: str | Path,
        model_mode: str = "mock",
        model_name: str | None = None,
        recorded_fixture: str | Path | None = None,
        dump_prompt: bool = False,
    ) -> PipelineResult:
        if model_mode not in {"mock", "recorded", "live"}:
            raise UnsupportedModelModeError(
                "P3 currently supports --model-mode mock, recorded, or live for local runs"
            )

        document = Path(document_path)
        output = Path(output_dir)
        parsed_dir = ensure_dir(output / "parsed")
        extracted_dir = ensure_dir(output / "extracted")
        review_dir = ensure_dir(output / "review")
        exports_dir = ensure_dir(output / "exports")

        task_id = output.name
        plan_path = output / "plan.json"
        manifest_path = output / "run_manifest.json"
        validated_reqir_path = extracted_dir / "reqir.validated.json"
        exported_reqir_path = exports_dir / "reqir.json"
        xlsx_path = exports_dir / "requirements.xlsx"

        plan = build_fixed_plan(
            task_id=task_id,
            input_document=document.as_posix(),
            model_mode=model_mode,
        )
        write_json(plan_path, plan.model_dump(mode="json"))
        manifest = init_manifest(
            task_id=task_id,
            input_document=document.as_posix(),
            output_dir=output.as_posix(),
            model_mode=model_mode,
        )
        write_json(manifest_path, manifest)

        try:
            parsed_document = parse_document(document, document_id="doc_001")
            plan.steps[0].config = {
                "selected_parser": parsed_document.parser_name,
                "source_format": parsed_document.source_format,
                "warnings": parsed_document.warnings,
            }
            write_json(plan_path, plan.model_dump(mode="json"))
            (parsed_dir / "document.md").write_text(parsed_document.text, encoding="utf-8")
            blocks = parsed_document.blocks
            write_json(parsed_dir / "blocks.json", model_list_dump(blocks))

            model_request = ModelRequest(
                document_text=parsed_document.text,
                blocks=blocks,
                document_name=document.name,
                source_format=parsed_document.source_format,
                parser_name=parsed_document.parser_name,
                model_mode=model_mode,
                model_name=model_name,
                metadata={"prompt_version": PROMPT_VERSION},
            )
            prompt = build_reqir_prompt(model_request)
            if dump_prompt:
                (extracted_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            model_client = create_model_client(
                model_mode=model_mode,
                model_name=model_name,
                recorded_fixture=recorded_fixture,
            )
            model_response = model_client.generate(model_request)
            payload = model_response.payload
            if model_response.raw_text:
                (extracted_dir / "model_response.raw.txt").write_text(model_response.raw_text, encoding="utf-8")
            extractor = ReqIRExtractor()
            try:
                requirements = extractor.extract(
                    payload=payload,
                    blocks=blocks,
                    document_name=document.name,
                    model_mode=model_mode,
                )
            except ValueError as exc:
                report = ValidationReport(valid=False)
                report.add_issue(
                    ValidationIssue(
                        level="error",
                        code="MODEL_OUTPUT_VALIDATION_FAILED",
                        message=str(exc),
                    )
                )
                write_json(extracted_dir / "validation_report.json", report.model_dump(mode="json"))
                write_json(manifest_path, fail_manifest(manifest, str(exc)))
                raise PipelineValidationError(str(exc)) from exc
            requirements = normalize_requirements(requirements)
            write_json(
                extracted_dir / "reqir.raw.json",
                {
                    "metadata": {
                        "model_mode": model_mode,
                        "model_name": model_response.model_name,
                        "prompt_version": model_response.metadata.get("prompt_version", PROMPT_VERSION),
                        "document": document.name,
                        "source_format": parsed_document.source_format,
                        "parser": parsed_document.parser_name,
                        "parser_warnings": parsed_document.warnings,
                        "llm": model_response.metadata,
                    },
                    "items": model_list_dump(requirements),
                },
            )

            schema_report = SchemaValidator().validate(requirements)
            if not schema_report.valid:
                write_json(extracted_dir / "validation_report.json", schema_report.model_dump(mode="json"))
                write_json(manifest_path, fail_manifest(manifest, "schema validation failed"))
                raise PipelineValidationError("schema validation failed")

            validated_requirements, source_report = SourceQuoteValidator().validate(requirements, blocks)
            ears_report = BasicEARSValidator().validate(validated_requirements)
            validation_report = _merge_reports(schema_report, source_report, ears_report)
            write_json(extracted_dir / "validation_report.json", validation_report.model_dump(mode="json"))
            if not source_report.valid:
                write_json(manifest_path, fail_manifest(manifest, "source quote validation failed"))
                raise PipelineValidationError("source quote validation failed")

            write_json(
                validated_reqir_path,
                {
                    "metadata": {
                        "validation_state": "validated",
                        "document": document.name,
                        "source_format": parsed_document.source_format,
                        "parser": parsed_document.parser_name,
                    },
                    "items": model_list_dump(validated_requirements),
                },
            )
            write_json(extracted_dir / "source_map.json", build_source_map(validated_requirements))
            write_json(review_dir / "review_log.json", collect_review_log(validated_requirements))
            write_json(
                exported_reqir_path,
                {
                    "metadata": {
                        "export_state": "unreviewed_snapshot",
                        "document": document.name,
                        "source_format": parsed_document.source_format,
                        "parser": parsed_document.parser_name,
                    },
                    "items": model_list_dump(validated_requirements),
                },
            )
            export_requirements_xlsx(validated_requirements, xlsx_path)
            completed_manifest = complete_manifest(
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
            )
            completed_manifest["model"] = {
                "mode": model_mode,
                "name": model_response.model_name,
                "prompt_version": model_response.metadata.get("prompt_version", PROMPT_VERSION),
                "recorded_fixture": model_response.metadata.get("fixture_path") if model_mode == "recorded" else None,
            }
            completed_manifest["parser"] = {
                "source_format": parsed_document.source_format,
                "parser_name": parsed_document.parser_name,
                "warnings": parsed_document.warnings,
            }
            write_json(
                manifest_path,
                completed_manifest,
            )
        except Exception as exc:
            write_json(manifest_path, fail_manifest(manifest, str(exc)))
            raise

        return PipelineResult(
            task_id=task_id,
            output_dir=output,
            plan_path=plan_path,
            manifest_path=manifest_path,
            validated_reqir_path=validated_reqir_path,
            exported_reqir_path=exported_reqir_path,
            xlsx_path=xlsx_path,
            validated_count=len(validated_requirements),
        )


def _merge_reports(*reports: ValidationReport) -> ValidationReport:
    merged = ValidationReport(valid=True)
    for report in reports:
        for issue in report.issues:
            merged.add_issue(issue)
    return merged
