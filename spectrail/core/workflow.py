from __future__ import annotations

from spectrail.core.models import PlanSpec, PlanStep


def build_fixed_plan(task_id: str, input_document: str, model_mode: str, *, p4: bool = False) -> PlanSpec:
    if p4:
        return PlanSpec(
            task_id=task_id,
            goal="extract_requirements",
            planner="fixed_workflow_v2",
            model_mode=model_mode,
            input_document=input_document,
            steps=[
                PlanStep(id="parse", tool="document_parser_registry", output="parsed/blocks.json"),
                PlanStep(id="plan_chunks", tool="section_aware_chunker", depends_on=["parse"], output="parsed/chunks.json"),
                PlanStep(id="extract_chunks", tool="chunk_executor", depends_on=["plan_chunks"], output="extracted/chunk_results"),
                PlanStep(id="aggregate_candidates", tool="candidate_aggregator", depends_on=["extract_chunks"], output="extracted/reqir.raw.json"),
                PlanStep(id="normalize_ears", tool="ears_normalizer", depends_on=["aggregate_candidates"]),
                PlanStep(id="validate_schema", tool="schema_validator", depends_on=["normalize_ears"]),
                PlanStep(id="validate_source_quote", tool="source_quote_validator", depends_on=["validate_schema"], output="extracted/reqir.validated.json"),
                PlanStep(id="build_quarantine", tool="validation_policy", depends_on=["validate_source_quote"], output="extracted/reqir.quarantined.json"),
                PlanStep(id="init_review", tool="review_snapshot_builder", depends_on=["build_quarantine"], output="review/review_log.json"),
                PlanStep(id="export_json", tool="json_exporter", depends_on=["init_review"], output="exports/reqir.json"),
                PlanStep(id="export_xlsx", tool="xlsx_exporter", depends_on=["export_json"], output="exports/requirements.xlsx"),
            ],
        )
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
