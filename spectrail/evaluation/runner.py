from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from spectrail.core.io import ensure_dir, read_json, write_json
from spectrail.core.models import RequirementIR
from spectrail.chunking import ChunkPlanningError, ChunkingConfig
from spectrail.evaluation.matcher import match_requirements
from spectrail.evaluation.locator_metrics import build_locator_metrics
from spectrail.evaluation.metrics import build_evaluation_metrics
from spectrail.evaluation.models import EvaluationCase, GoldPackage
from spectrail.evidence.models import EvidenceIndex
from spectrail.evidence.fingerprint import validate_evidence_fingerprint
from spectrail.llm.errors import ModelError
from spectrail.parsers import DocumentParseError, ParsedDocument, parse_document
from spectrail.pipeline import PipelineConfig, PipelineError, PipelineRunner


RequirementList = TypeAdapter(list[RequirementIR])


class EvaluationRunner:
    def run(self, case_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
        path = Path(case_path)
        case_files = sorted(path.rglob("case.json")) if path.is_dir() else [path]
        if not case_files:
            raise ValueError(f"no evaluation cases found: {path}")
        output = ensure_dir(output_dir)
        reports = [self._run_case(case_file, output / "cases" / case_file.parent.name) for case_file in case_files]
        passed = sum(report["passed"] for report in reports)
        suite = {
            "case_count": len(reports),
            "case_passed": passed,
            "case_failed": len(reports) - passed,
            "passed": passed == len(reports),
            "cases": reports,
        }
        write_json(output / "evaluation_report.json", suite)
        (output / "evaluation_report.md").write_text(_suite_markdown(suite), encoding="utf-8")
        return suite

    def _run_case(self, case_file: Path, output: Path) -> dict[str, Any]:
        case = EvaluationCase.model_validate(read_json(case_file))
        document = _resolve_path(case.document, case_file.parent)
        gold_path = _resolve_path(case.gold, case_file.parent)
        gold = GoldPackage.model_validate(read_json(gold_path))
        pipeline_output = output / "pipeline"
        config = PipelineConfig(
            model_mode=case.model_mode,
            model_name=case.model_name,
            recorded_fixture=(
                _resolve_path(case.recorded_fixture, case_file.parent)
                if case.recorded_fixture
                else None
            ),
            request_profile=(case.request_profile.to_runtime() if case.request_profile else None),
            chunking=ChunkingConfig(
                mode=case.chunking_mode,
                max_rendered_prompt_chars=case.max_rendered_prompt_chars,
                overlap_blocks=case.overlap_blocks,
            ),
            validation_policy=case.validation_policy,
            evidence_policy=case.evidence_policy,
        )
        pipeline_exception: Exception | None = None
        try:
            parsed_document = _preflight_selected_scope(document, case, gold)
            PipelineRunner().extract(
                document,
                pipeline_output,
                config=config,
                parsed_document=parsed_document,
            )
        except (PipelineError, ChunkPlanningError, DocumentParseError, ModelError) as exc:
            pipeline_exception = exc

        manifest_path = pipeline_output / "run_manifest.json"
        manifest = (
            read_json(manifest_path)
            if manifest_path.exists()
            else {
                "status": "failed",
                "error": str(pipeline_exception) if pipeline_exception else "pipeline failed",
                "error_code": (
                    type(pipeline_exception).__name__ if pipeline_exception else "PIPELINE_FAILED"
                ),
                "counts": {},
                "execution": {},
            }
        )
        export_path = pipeline_output / "exports" / "reqir.json"
        if manifest.get("status") in {"completed", "completed_with_warnings"} and export_path.exists():
            actual_package = read_json(export_path)
            candidates = RequirementList.validate_python(actual_package.get("items", []))
        else:
            candidates = []
        matches = match_requirements(candidates, gold.items, scope_block_ids=case.scope_block_ids)
        counts = manifest.get("counts", {})
        execution = manifest.get("execution", {})
        evidence_index_path = pipeline_output / "parsed" / "evidence_index.json"
        evidence_index = (
            EvidenceIndex.model_validate(read_json(evidence_index_path))
            if evidence_index_path.exists()
            else None
        )
        if evidence_index is not None:
            validate_evidence_fingerprint(evidence_index)
        metrics = {
            **build_evaluation_metrics(
                gold_count=matches.evaluated_gold_count,
                candidate_count=matches.evaluated_candidate_count,
                matches=matches,
                aggregated_count=counts.get("aggregated_requirements", len(candidates)),
                validated_count=counts.get("validated_requirements", len(candidates)),
                exported_count=len(candidates),
                grounded_exported_count=sum(
                    any(
                        source.match_status in {"PASS_EXACT", "PASS_NORMALIZED"}
                        for source in item.sources
                    )
                    for item in candidates
                ),
                quarantined_count=counts.get("quarantined_requirements", 0),
                model_items_total=counts.get("model_items_total", 0),
                rejected_item_count=counts.get("model_items_rejected", 0),
                raw_candidate_count=counts.get(
                    "raw_candidates", counts.get("raw_requirements", 0)
                ),
                collapsed_duplicate_count=counts.get(
                    "collapsed_overlap_duplicates", 0
                ),
                chunk_count=counts.get("chunks", 0),
                chunk_completed_count=(
                    counts.get("chunks_completed", 0)
                    + counts.get("chunks_completed_with_warnings", 0)
                ),
                chunk_failed_count=counts.get("chunks_failed", 0),
                model_call_count=counts.get("model_call_count", 0),
                elapsed_ms=execution.get("elapsed_ms", 0),
                rendered_prompt_chars=execution.get("rendered_prompt_chars", 0),
                response_chars=execution.get("response_chars", 0),
                estimated_tokens=execution.get("estimated_tokens", 0),
            ),
            **build_locator_metrics(
                candidates=candidates,
                gold=gold.items,
                matches=matches,
                block_evidence=evidence_index.blocks if evidence_index else (),
            ),
            "evidence_index_available": evidence_index is not None,
        }
        threshold_results = _threshold_results(metrics, case.thresholds)
        outcome_pass = (
            manifest.get("status") in case.allowed_pipeline_statuses
            and manifest.get("zero_result_reason") in case.allowed_zero_result_reasons
        )
        report = {
            "name": case.name,
            "passed": outcome_pass and all(item["passed"] for item in threshold_results.values()),
            "pipeline_status": manifest.get("status"),
            "error_code": manifest.get("error_code"),
            "error": manifest.get("error"),
            "warning_codes": manifest.get("warning_codes", []),
            "zero_result_reason": manifest.get("zero_result_reason"),
            "annotation_scope": "selected_blocks" if case.scope_block_ids else "full_document",
            "scope_block_ids": case.scope_block_ids,
            "full_gold_requirements": len(gold.items),
            **metrics,
            "threshold_results": threshold_results,
            "source_alignment_matches": [pair.__dict__ for pair in matches.source_alignment_matches],
            "requirement_exact_matches": [pair.__dict__ for pair in matches.requirement_exact_matches],
        }
        ensure_dir(output)
        write_json(output / "case_report.json", report)
        (output / "case_report.md").write_text(_case_markdown(report), encoding="utf-8")
        return report


def _resolve_path(value: str | Path, case_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    return case_dir / path


def _preflight_selected_scope(
    document: Path,
    case: EvaluationCase,
    gold: GoldPackage,
) -> ParsedDocument | None:
    if not case.scope_block_ids:
        return None

    parsed = parse_document(document, document_id="doc_001")
    scope = set(case.scope_block_ids)
    parsed_block_ids = {block.block_id for block in parsed.blocks}
    missing_scope_ids = sorted(scope - parsed_block_ids)
    if missing_scope_ids:
        raise ValueError(
            "scope_block_ids not found in parsed blocks: " + ", ".join(missing_scope_ids)
        )

    scoped_gold_count = sum(
        any(source.block_id in scope for source in requirement.sources)
        for requirement in gold.items
    )
    if scoped_gold_count == 0 and not case.allow_empty_gold_scope:
        raise ValueError(
            "selected scope contains no gold requirements; "
            "set allow_empty_gold_scope=true only for intentional empty-scope cases"
        )
    return parsed


def _threshold_results(metrics: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any]:
    results = {}
    for name, threshold in thresholds.items():
        if name.endswith("_min"):
            metric_name = name[:-4]
            actual = metrics.get(metric_name)
            passed = actual is not None and actual >= threshold
            operator = ">="
        elif name.endswith("_max"):
            metric_name = name[:-4]
            actual = metrics.get(metric_name)
            passed = actual is not None and actual <= threshold
            operator = "<="
        else:
            raise ValueError(f"threshold must end in _min or _max: {name}")
        results[name] = {
            "metric": metric_name,
            "operator": operator,
            "threshold": threshold,
            "actual": actual,
            "passed": passed,
        }
    return results


def _suite_markdown(report: dict[str, Any]) -> str:
    lines = ["# SpecTrail Evaluation", "", f"Passed: {report['case_passed']}/{report['case_count']}", ""]
    for case in report["cases"]:
        lines.append(f"- {'PASS' if case['passed'] else 'FAIL'} — {case['name']}")
    return "\n".join(lines) + "\n"


def _case_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['name']}",
        "",
        f"Status: {'PASS' if report['passed'] else 'FAIL'}",
        "",
        "## Outcome",
        "",
        f"- Pipeline status: {report['pipeline_status']}",
        f"- Error code: {report.get('error_code') or 'None'}",
        f"- Zero result reason: {report.get('zero_result_reason') or 'None'}",
        f"- Warning codes: {', '.join(report.get('warning_codes', [])) or 'None'}",
        "",
        "## Counts and execution",
        "",
        f"- Gold requirements: {report['gold_requirements']}",
        f"- Full gold requirements: {report['full_gold_requirements']}",
        f"- Candidates in scope: {report['validated_candidates_in_scope']}",
        f"- Raw / aggregated / validated / exported: {report['raw_candidates']} / "
        f"{report['aggregated_requirements']} / {report['validated_requirements']} / "
        f"{report['exported_requirements']}",
        f"- Source / exact matches: {report['source_matching_cardinality']} / "
        f"{report['requirement_matching_cardinality']}",
        f"- Chunks completed / planned / failed: {report['chunk_completed_count']} / "
        f"{report['chunk_count']} / {report['chunk_failed_count']}",
        f"- Model calls: {report['model_call_count']}",
        f"- Elapsed ms: {report['elapsed_ms']}",
        f"- Prompt / response chars / estimated tokens: {report['rendered_prompt_chars']} / "
        f"{report['response_chars']} / {report['estimated_tokens']}",
        "",
        "## Metrics",
        "",
        f"- Source precision / recall / F1: {report['source_alignment_precision']:.4f} / "
        f"{report['source_alignment_recall']:.4f} / {report['source_alignment_f1']:.4f}",
        f"- Requirement exact precision / recall / F1: "
        f"{report['requirement_exact_precision']:.4f} / "
        f"{report['requirement_exact_recall']:.4f} / "
        f"{report['requirement_exact_f1']:.4f}",
        f"- Export grounding: {report['export_grounding_pass_rate']:.4f}",
        f"- Duplicate / quarantine / rejected rates: {report['duplicate_rate']:.4f} / "
        f"{report['quarantine_rate']:.4f} / {report['rejected_item_rate']:.4f}",
        f"- Local top-edge ties: {report['local_top_edge_tie_count']}",
        f"- Evidence index available: {report['evidence_index_available']}",
        f"- Text locator pass rate (evaluated): {report['text_locator_pass_rate']:.4f} "
        f"({report['text_locator_evaluated_count']})",
        f"- Page accuracy (evaluated): {report['page_accuracy']:.4f} "
        f"({report['page_evaluated_count']})",
        f"- Table cell precision / recall / F1 (evaluated): "
        f"{report['table_cell_precision']:.4f} / {report['table_cell_recall']:.4f} / "
        f"{report['table_cell_f1']:.4f} ({report['table_cell_evaluated_count']})",
        f"- BBox IoU mean / pass rate (evaluated): {report['bbox_iou_mean']:.4f} / "
        f"{report['bbox_iou_pass_rate']:.4f} ({report['bbox_evaluated_count']})",
        f"- Structured grounding coverage (passed / eligible): "
        f"{report['structured_grounding_coverage']:.4f} "
        f"({report['structured_grounding_pass_count']} / "
        f"{report['structured_grounding_eligible_count']})",
        f"- Structured source missing / ambiguous / unverified / failed: "
        f"{report['structured_grounding_missing_count']} / "
        f"{report['structured_grounding_ambiguous_count']} / "
        f"{report['structured_grounding_unverified_count']} / "
        f"{report['structured_grounding_failed_count']}",
        f"- Structured capability pass / expected: "
        f"{report['structured_capability_pass_count']} / "
        f"{report['structured_capability_expected_count']}",
        f"- Structured capability missing / ambiguous / unverified / failed: "
        f"{report['structured_capability_missing_count']} / "
        f"{report['structured_capability_ambiguous_count']} / "
        f"{report['structured_capability_unverified_count']} / "
        f"{report['structured_capability_failed_count']}",
        f"- Structured invalid reference / derivation failure: "
        f"{report['structured_invalid_reference_count']} / "
        f"{report['structured_derivation_failed_count']}",
        "",
        "## Thresholds",
        "",
    ]
    if report["threshold_results"]:
        for name, result in report["threshold_results"].items():
            lines.append(
                f"- {'PASS' if result['passed'] else 'FAIL'} {name}: "
                f"{result['actual']} {result['operator']} {result['threshold']}"
            )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"
