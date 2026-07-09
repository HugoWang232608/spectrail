from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from spectrail.core.io import ensure_dir, model_list_dump, write_json
from spectrail.core.manifest import complete_manifest, fail_manifest, init_manifest
from spectrail.core.models import ValidationReport
from spectrail.core.workflow import build_fixed_plan
from spectrail.exporters.source_map_exporter import build_source_map
from spectrail.exporters.xlsx_exporter import export_requirements_xlsx
from spectrail.extractors.ears_normalizer import normalize_requirements
from spectrail.extractors.reqir_extractor import ReqIRExtractor
from spectrail.llm.mock_model import MockModel
from spectrail.parsers.markdown_parser import MarkdownParser
from spectrail.review.review_log import collect_review_log
from spectrail.validators.ears_validator import BasicEARSValidator
from spectrail.validators.schema_validator import SchemaValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator


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
    ) -> PipelineResult:
        if model_mode != "mock":
            raise SystemExit("P0 currently supports --model-mode mock for deterministic local runs")

        del model_name

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
            copied_document = parsed_dir / "document.md"
            shutil.copyfile(document, copied_document)

            parser = MarkdownParser()
            blocks = parser.parse_file(document)
            write_json(parsed_dir / "blocks.json", model_list_dump(blocks))

            payload = MockModel().generate(document.read_text(encoding="utf-8"))
            extractor = ReqIRExtractor()
            requirements = extractor.extract(
                payload=payload,
                blocks=blocks,
                document_name=document.name,
                model_mode=model_mode,
            )
            requirements = normalize_requirements(requirements)
            write_json(
                extracted_dir / "reqir.raw.json",
                {
                    "metadata": {"model_mode": model_mode, "document": document.name},
                    "items": model_list_dump(requirements),
                },
            )

            schema_report = SchemaValidator().validate(requirements)
            if not schema_report.valid:
                write_json(extracted_dir / "validation_report.json", schema_report.model_dump(mode="json"))
                write_json(manifest_path, fail_manifest(manifest, "schema validation failed"))
                raise SystemExit("schema validation failed")

            validated_requirements, source_report = SourceQuoteValidator().validate(requirements, blocks)
            ears_report = BasicEARSValidator().validate(validated_requirements)
            validation_report = _merge_reports(schema_report, source_report, ears_report)
            write_json(extracted_dir / "validation_report.json", validation_report.model_dump(mode="json"))
            if not source_report.valid:
                write_json(manifest_path, fail_manifest(manifest, "source quote validation failed"))
                raise SystemExit("source quote validation failed")

            write_json(
                validated_reqir_path,
                {
                    "metadata": {"validation_state": "validated", "document": document.name},
                    "items": model_list_dump(validated_requirements),
                },
            )
            write_json(extracted_dir / "source_map.json", build_source_map(validated_requirements))
            write_json(review_dir / "review_log.json", collect_review_log(validated_requirements))
            write_json(
                exported_reqir_path,
                {
                    "metadata": {"export_state": "unreviewed_snapshot", "document": document.name},
                    "items": model_list_dump(validated_requirements),
                },
            )
            export_requirements_xlsx(validated_requirements, xlsx_path)
            write_json(
                manifest_path,
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
