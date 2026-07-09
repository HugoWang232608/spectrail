from __future__ import annotations

from spectrail.core.models import PlanSpec, PlanStep


def build_fixed_plan(task_id: str, input_document: str, model_mode: str) -> PlanSpec:
    return PlanSpec(
        task_id=task_id,
        goal="extract_requirements",
        planner="fixed_workflow_v1",
        model_mode=model_mode,
        input_document=input_document,
        steps=[
            PlanStep(
                id="parse",
                tool="document_parser_registry",
                output="parsed/blocks.json",
            ),
            PlanStep(
                id="extract",
                tool="reqir_extractor",
                depends_on=["parse"],
                output="extracted/reqir.raw.json",
            ),
            PlanStep(
                id="normalize_ears",
                tool="ears_normalizer",
                depends_on=["extract"],
            ),
            PlanStep(
                id="validate_schema",
                tool="schema_validator",
                depends_on=["normalize_ears"],
            ),
            PlanStep(
                id="validate_source_quote",
                tool="source_quote_validator",
                depends_on=["validate_schema"],
                output="extracted/reqir.validated.json",
            ),
            PlanStep(
                id="init_review",
                tool="review_snapshot_builder",
                depends_on=["validate_source_quote"],
                output="review/review_log.json",
            ),
            PlanStep(
                id="export_json",
                tool="json_exporter",
                depends_on=["init_review"],
                output="exports/reqir.json",
            ),
            PlanStep(
                id="export_xlsx",
                tool="xlsx_exporter",
                depends_on=["export_json"],
                output="exports/requirements.xlsx",
            ),
        ],
    )
