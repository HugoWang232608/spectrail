from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from spectrail.core.io import ensure_dir, read_json, write_json
from spectrail.core.models import DocumentBlock, RequirementIR
from spectrail.evaluation.matcher import match_requirements
from spectrail.evaluation.metrics import build_evaluation_metrics
from spectrail.evaluation.models import EvaluationCase, GoldPackage
from spectrail.pipeline import PipelineRunner


RequirementList = TypeAdapter(list[RequirementIR])
BlockList = TypeAdapter(list[DocumentBlock])


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
        pipeline_output = output / "pipeline"
        result = PipelineRunner().extract(
            document,
            pipeline_output,
            model_mode=case.model_mode,
            model_name=case.model_name,
            recorded_fixture=(
                _resolve_path(case.recorded_fixture, case_file.parent)
                if case.recorded_fixture
                else None
            ),
            chunking_mode=case.chunking_mode,
            max_rendered_prompt_chars=case.max_rendered_prompt_chars,
            overlap_blocks=case.overlap_blocks,
            validation_policy=case.validation_policy,
        )
        gold = GoldPackage.model_validate(read_json(gold_path))
        actual_package = read_json(result.exported_reqir_path)
        candidates = RequirementList.validate_python(actual_package.get("items", []))
        blocks = BlockList.validate_python(read_json(pipeline_output / "parsed" / "blocks.json"))
        manifest = read_json(result.manifest_path)
        in_scope = [
            candidate
            for candidate in candidates
            if not case.scope_block_ids
            or any(source.block_id in set(case.scope_block_ids) for source in candidate.sources)
        ]
        matches = match_requirements(candidates, gold.items, scope_block_ids=case.scope_block_ids)
        counts = manifest.get("counts", {})
        metrics = build_evaluation_metrics(
            gold_count=len(gold.items),
            candidate_count=len(in_scope),
            matches=matches,
            aggregated_count=counts.get("aggregated_requirements", len(candidates)),
            validated_count=counts.get("validated_requirements", len(candidates)),
            exported_count=len(candidates),
            grounded_exported_count=sum(
                any(source.match_status in {"PASS_EXACT", "PASS_NORMALIZED"} for source in item.sources)
                for item in candidates
            ),
            quarantined_count=counts.get("quarantined_requirements", 0),
            model_items_total=counts.get("model_items_total", 0),
            rejected_item_count=counts.get("model_items_rejected", 0),
        )
        threshold_results = _threshold_results(metrics, case.thresholds)
        outcome_pass = (
            manifest.get("status") in case.allowed_pipeline_statuses
            and manifest.get("zero_result_reason") in case.allowed_zero_result_reasons
        )
        report = {
            "name": case.name,
            "passed": outcome_pass and all(item["passed"] for item in threshold_results.values()),
            "pipeline_status": manifest.get("status"),
            "zero_result_reason": manifest.get("zero_result_reason"),
            "annotation_scope": "selected_blocks" if case.scope_block_ids else "full_document",
            "scope_block_ids": case.scope_block_ids,
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
    return (
        f"# {report['name']}\n\n"
        f"Status: {'PASS' if report['passed'] else 'FAIL'}\n\n"
        f"- Pipeline: {report['pipeline_status']}\n"
        f"- Source recall: {report['source_alignment_recall']:.4f}\n"
        f"- Requirement exact recall: {report['requirement_exact_recall']:.4f}\n"
        f"- Export grounding: {report['export_grounding_pass_rate']:.4f}\n"
    )
