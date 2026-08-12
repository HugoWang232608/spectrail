from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import statistics
import time
from typing import Any

from spectrail.agent import AgentRunner, build_default_agent_policy
from spectrail.agent.profiler import DocumentProfiler
from spectrail.chunking import ChunkingConfig
from spectrail.core.io import read_json, write_json
from spectrail.evidence.index_builder import ensure_evidence_index
from spectrail.llm.agent_planner import AgentPlannerClient
from spectrail.llm.openai_compatible import OpenAICompatibleModel
from spectrail.llm.openai_compatible_transport import OpenAICompatibleTransport
from spectrail.llm.transport import CompletionRequest, CompletionResponse
from spectrail.parsers import parse_document
from spectrail.pipeline import PipelineConfig, PipelineRunner


COMPLETED_STATUSES = {"completed", "completed_with_warnings"}
SUPPORTED_SOURCE_STATUSES = {"PASS_EXACT", "PASS_NORMALIZED"}


@dataclass
class UsageLedger:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        *,
        role: str,
        request: CompletionRequest,
        elapsed_ms: int,
        response: CompletionResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        usage = response.usage if response is not None else None
        raw_response = response.raw_text if response is not None else None
        self.calls.append(
            {
                "sequence": len(self.calls) + 1,
                "role": role,
                "status": "failed" if error is not None else "completed",
                "elapsed_ms": elapsed_ms,
                "prompt_chars": len(request.prompt),
                "response_chars": len(response.raw_text) if response is not None else 0,
                "input_tokens": _usage_value(usage, "prompt_tokens", "input_tokens"),
                "output_tokens": _usage_value(
                    usage,
                    "completion_tokens",
                    "output_tokens",
                ),
                "total_tokens": _usage_value(usage, "total_tokens"),
                "response_sha256": (
                    hashlib.sha256(raw_response.encode("utf-8")).hexdigest()
                    if raw_response is not None
                    else None
                ),
                "response_preview": (
                    raw_response[:2_000] if raw_response is not None else None
                ),
                "error_code": type(error).__name__ if error is not None else None,
            }
        )

    def summary(self) -> dict[str, Any]:
        by_role = {
            role: _summarize_calls(
                [call for call in self.calls if call["role"] == role]
            )
            for role in ("extraction", "planner")
        }
        return {
            **_summarize_calls(self.calls),
            "by_role": by_role,
            "calls": self.calls,
        }


class RecordingTransport:
    def __init__(
        self,
        delegate: OpenAICompatibleTransport,
        ledger: UsageLedger,
        role: str,
    ) -> None:
        self.delegate = delegate
        self.ledger = ledger
        self.role = role

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        started = time.perf_counter()
        try:
            response = self.delegate.complete(request)
        except Exception as exc:
            self.ledger.record(
                role=self.role,
                request=request,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                error=exc,
            )
            raise
        self.ledger.record(
            role=self.role,
            request=request,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            response=response,
        )
        return response

    def resolve_request_profile(self, explicit_profile, *, insecure: bool = False):
        return self.delegate.resolve_request_profile(
            explicit_profile,
            insecure=insecure,
        )

    def resolve_transport(self, *, insecure: bool = False):
        return self.delegate.resolve_transport(insecure=insecure)


def _usage_value(
    usage: dict[str, Any] | None,
    *keys: str,
) -> int | None:
    if not usage:
        return None
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _summarize_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    known_totals = [call["total_tokens"] for call in calls if call["total_tokens"] is not None]
    known_inputs = [call["input_tokens"] for call in calls if call["input_tokens"] is not None]
    known_outputs = [call["output_tokens"] for call in calls if call["output_tokens"] is not None]
    return {
        "provider_calls": len(calls),
        "failed_provider_calls": sum(call["status"] == "failed" for call in calls),
        "elapsed_ms": sum(call["elapsed_ms"] for call in calls),
        "prompt_chars": sum(call["prompt_chars"] for call in calls),
        "response_chars": sum(call["response_chars"] for call in calls),
        "input_tokens": sum(known_inputs) if len(known_inputs) == len(calls) else None,
        "output_tokens": sum(known_outputs) if len(known_outputs) == len(calls) else None,
        "total_tokens": sum(known_totals) if len(known_totals) == len(calls) else None,
        "usage_available_for_calls": len(known_totals),
    }


def load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if payload.get("schema_version") != "real_world_agent_corpus_v1":
        raise ValueError("unsupported real-world corpus schema")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not 10 <= len(documents) <= 20:
        raise ValueError("real-world corpus must contain 10-20 documents")
    case_ids = [document.get("case_id") for document in documents]
    if any(not isinstance(case_id, str) for case_id in case_ids):
        raise ValueError("every corpus document requires a case_id")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("real-world corpus case_id values must be unique")
    return documents


def verify_document(document: dict[str, Any], corpus_dir: Path) -> Path:
    path = corpus_dir / document["filename"]
    if not path.is_file():
        raise ValueError(f"corpus document is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != document["sha256"]:
        raise ValueError(f"corpus document hash differs: {document['case_id']}")
    return path


def profile_document(document: dict[str, Any], path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    parsed = parse_document(path, document_id="doc_001")
    try:
        evidence_index = ensure_evidence_index(path, parsed)
        profile_payload = DocumentProfiler().build(
            parsed,
            evidence_index,
        ).model_dump(mode="json")
        profile_error = None
    except Exception as exc:
        block_type_counts: dict[str, int] = {}
        for block in parsed.blocks:
            block_type_counts[block.type] = block_type_counts.get(block.type, 0) + 1
        page_values = [
            block.page for block in parsed.blocks if block.page is not None
        ]
        profile_payload = {
            "document_id": parsed.document_id,
            "document_name": parsed.document_name,
            "source_format": parsed.source_format,
            "parser_name": parsed.parser_name,
            "page_count": parsed.metadata.get("page_count")
            or (max(page_values) if page_values else None),
            "block_count": len(parsed.blocks),
            "block_type_counts": block_type_counts,
            "rendered_text_chars": len(parsed.text),
            "estimated_prompt_chars": len(parsed.text),
            "warnings": parsed.warnings,
        }
        profile_error = {
            "error_code": type(exc).__name__,
            "reason": _safe_exception_message(exc),
        }
    return {
        "case_id": document["case_id"],
        "filename": document["filename"],
        "format": document["format"],
        "length_class": document["length_class"],
        "structure_class": document["structure_class"],
        "file_bytes": path.stat().st_size,
        "profile_elapsed_ms": int((time.perf_counter() - started) * 1000),
        "profile_error": profile_error,
        **profile_payload,
    }


def pipeline_config(*, insecure: bool, model_name: str | None) -> PipelineConfig:
    return PipelineConfig(
        model_mode="live",
        model_name=model_name,
        chunking=ChunkingConfig(
            mode="auto",
            max_rendered_prompt_chars=16_000,
            overlap_blocks=1,
            fail_fast=False,
        ),
        validation_policy="strict",
        evidence_policy="structured_if_available",
        insecure=insecure,
    )


def instrumented_pipeline_runner(
    *,
    ledger: UsageLedger,
    endpoint_id: str,
) -> PipelineRunner:
    def model_factory(**kwargs) -> OpenAICompatibleModel:
        model = OpenAICompatibleModel(
            model_name=kwargs.get("model_name"),
            endpoint_id=endpoint_id,
        )
        model.transport = RecordingTransport(
            model.transport,
            ledger,
            "extraction",
        )
        return model

    return PipelineRunner(model_client_factory=model_factory)


def live_planner(
    *,
    ledger: UsageLedger,
    endpoint_id: str,
    planner_model_name: str | None,
    insecure: bool,
) -> AgentPlannerClient:
    transport = OpenAICompatibleTransport(
        model_name=planner_model_name,
        endpoint_id=endpoint_id,
    )
    profile = transport.resolve_request_profile(None, insecure=insecure)
    return AgentPlannerClient(
        RecordingTransport(transport, ledger, "planner"),
        profile,
        insecure=insecure,
    )


def run_mode(
    *,
    mode: str,
    document: dict[str, Any],
    document_path: Path,
    run_dir: Path,
    endpoint_id: str,
    model_name: str | None,
    planner_model_name: str | None,
    insecure: bool,
) -> dict[str, Any]:
    if run_dir.exists():
        raise ValueError(f"run directory already exists: {run_dir}")
    ledger = UsageLedger()
    runner = instrumented_pipeline_runner(
        ledger=ledger,
        endpoint_id=endpoint_id,
    )
    config = pipeline_config(insecure=insecure, model_name=model_name)
    started = time.perf_counter()
    caught: Exception | None = None
    try:
        if mode == "fixed":
            runner.extract(document_path, run_dir, config=config)
        elif mode == "agent":
            planner = live_planner(
                ledger=ledger,
                endpoint_id=endpoint_id,
                planner_model_name=planner_model_name,
                insecure=insecure,
            )
            AgentRunner(
                planner=planner,
                policy=build_default_agent_policy(config),
                pipeline_config=config,
                pipeline_runner=runner,
            ).run(document_path, run_dir)
        else:
            raise ValueError(f"unsupported orchestration mode: {mode}")
    except Exception as exc:
        caught = exc
    wall_elapsed_ms = int((time.perf_counter() - started) * 1000)
    usage = ledger.summary()
    write_json(run_dir / "evaluation_provider_calls.json", usage)
    result = collect_result(
        mode=mode,
        document=document,
        run_dir=run_dir,
        wall_elapsed_ms=wall_elapsed_ms,
        usage=usage,
        caught=caught,
    )
    return result


def collect_result(
    *,
    mode: str,
    document: dict[str, Any],
    run_dir: Path,
    wall_elapsed_ms: int,
    usage: dict[str, Any],
    caught: Exception | None,
) -> dict[str, Any]:
    manifest_path = run_dir / "run_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    reqir_path = run_dir / "extracted" / "reqir.validated.json"
    reqir = read_json(reqir_path) if reqir_path.is_file() else {"items": []}
    items = reqir.get("items", []) if isinstance(reqir, dict) else []
    if not isinstance(items, list):
        items = []
    quality = quality_metrics(items, manifest)
    status = manifest.get("status", "failed")
    final_outcome = manifest.get("orchestration", {}).get("outcome")
    success = (
        status in COMPLETED_STATUSES
        and quality["final_requirements"] > 0
        and (mode == "fixed" or final_outcome in COMPLETED_STATUSES)
    )
    sequence, agent_state = agent_sequence(run_dir) if mode == "agent" else ([], {})
    counts = manifest.get("counts", {}) if isinstance(manifest.get("counts"), dict) else {}
    chunk_errors_path = run_dir / "extracted" / "chunk_errors.json"
    chunk_errors = read_json(chunk_errors_path) if chunk_errors_path.is_file() else []
    execution = (
        manifest.get("execution", {})
        if isinstance(manifest.get("execution"), dict)
        else {}
    )
    error_code = manifest.get("error_code")
    failure_reason = manifest.get("error")
    if mode == "agent" and agent_state:
        failure_reason = agent_state.get("reason") if not success else None
        if not success and not error_code:
            error_code = agent_state.get("reason")
    if caught is not None:
        error_code = error_code or type(caught).__name__
        failure_reason = failure_reason or _safe_exception_message(caught)
    attempts = int(agent_state.get("pipeline_attempts", 0)) if agent_state else 0
    return {
        "case_id": document["case_id"],
        "mode": mode,
        "run_order": None,
        "status": status,
        "agent_outcome": final_outcome,
        "success": success,
        "error_code": error_code,
        "failure_reason": failure_reason,
        "chunk_errors": chunk_errors,
        "warning_codes": manifest.get("warning_codes", []),
        "wall_elapsed_ms": wall_elapsed_ms,
        "pipeline_elapsed_ms": execution.get("elapsed_ms"),
        "estimated_prompt_tokens": execution.get("estimated_tokens"),
        "actual_usage": usage,
        "counts": counts,
        "quality": quality,
        "pipeline_attempts": attempts if mode == "agent" else 1,
        "retry_count": max(0, attempts - 1) if mode == "agent" else 0,
        "planner_calls": int(agent_state.get("planner_calls", 0)) if agent_state else 0,
        "tool_invocations": int(agent_state.get("tool_invocations", 0)) if agent_state else 0,
        "planner_tool_sequence": sequence,
        "sample_requirements": [
            {
                "id": item.get("id"),
                "statement": item.get("statement"),
                "type": item.get("type"),
                "confidence": item.get("confidence"),
                "source_quotes": [
                    source.get("quote")
                    for source in item.get("sources", [])[:2]
                    if isinstance(source, dict)
                ],
            }
            for item in items[:3]
            if isinstance(item, dict)
        ],
    }


def quality_metrics(items: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    count = len(items)
    sources = [
        source
        for item in items
        if isinstance(item, dict)
        for source in item.get("sources", [])
        if isinstance(source, dict)
    ]
    exact_sources = sum(source.get("match_status") == "PASS_EXACT" for source in sources)
    supported_sources = sum(
        source.get("match_status") in SUPPORTED_SOURCE_STATUSES for source in sources
    )
    fully_grounded = sum(
        bool(item.get("sources"))
        and all(
            isinstance(source, dict)
            and source.get("match_status") in SUPPORTED_SOURCE_STATUSES
            for source in item.get("sources", [])
        )
        for item in items
        if isinstance(item, dict)
    )
    known_type = sum(item.get("type") not in {None, "unknown"} for item in items)
    known_pattern = sum(
        item.get("ears_pattern") not in {None, "unknown"} for item in items
    )
    known_verification = sum(
        item.get("verification_method") not in {None, "unknown"} for item in items
    )
    structured_response = sum(bool(item.get("response")) for item in items)
    confidences = [
        float(item["confidence"])
        for item in items
        if isinstance(item.get("confidence"), (int, float))
    ]
    grounding_scores = [
        float(item["grounding_score"])
        for item in items
        if isinstance(item.get("grounding_score"), (int, float))
    ]
    statement_lengths = [
        len(item.get("statement", ""))
        for item in items
        if isinstance(item, dict)
    ]
    fully_grounded_rate = _rate(fully_grounded, count)
    exact_source_rate = _rate(exact_sources, len(sources))
    known_type_rate = _rate(known_type, count)
    known_pattern_rate = _rate(known_pattern, count)
    known_verification_rate = _rate(known_verification, count)
    structured_response_rate = _rate(structured_response, count)
    quality_proxy_score = (
        0.30 * fully_grounded_rate
        + 0.25 * exact_source_rate
        + 0.15 * known_type_rate
        + 0.10 * known_pattern_rate
        + 0.10 * known_verification_rate
        + 0.10 * structured_response_rate
    ) if count else 0.0
    counts = manifest.get("counts", {}) if isinstance(manifest.get("counts"), dict) else {}
    return {
        "quality_method": "structural_and_grounding_proxy_v1_no_recall_gold",
        "final_requirements": count,
        "source_spans": len(sources),
        "fully_grounded_requirement_rate": fully_grounded_rate,
        "exact_source_rate": exact_source_rate,
        "supported_source_rate": _rate(supported_sources, len(sources)),
        "known_type_rate": known_type_rate,
        "known_ears_pattern_rate": known_pattern_rate,
        "known_verification_method_rate": known_verification_rate,
        "structured_response_rate": structured_response_rate,
        "average_confidence": statistics.fmean(confidences) if confidences else None,
        "average_grounding_score": (
            statistics.fmean(grounding_scores) if grounding_scores else None
        ),
        "average_statement_chars": (
            statistics.fmean(statement_lengths) if statement_lengths else None
        ),
        "source_quote_passed": counts.get("source_quote_passed", 0),
        "source_quote_failed": counts.get("source_quote_failed", 0),
        "source_locator_passed": counts.get("source_locator_passed", 0),
        "source_locator_failed": counts.get("source_locator_failed", 0),
        "quality_proxy_score": quality_proxy_score,
    }


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def agent_sequence(run_dir: Path) -> tuple[list[str], dict[str, Any]]:
    events_dir = run_dir / "agent" / "events"
    sequence: list[str] = []
    if events_dir.is_dir():
        for path in sorted(events_dir.glob("*.json")):
            event = read_json(path)
            if event.get("event_type") != "decision":
                continue
            payload = event.get("payload", {})
            if payload.get("action") == "invoke_tool":
                arguments = json.dumps(
                    payload.get("arguments", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                sequence.append(f"{payload.get('tool')}({arguments})")
            elif payload.get("action") == "finish":
                sequence.append(f"finish({payload.get('outcome')})")
    final_state_path = run_dir / "agent" / "final_state.json"
    final_state = read_json(final_state_path) if final_state_path.is_file() else {}
    return sequence, final_state


def _safe_exception_message(error: Exception) -> str:
    message = str(error).strip()
    return message[:512] if message else type(error).__name__


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    modes: dict[str, dict[str, Any]] = {}
    for mode in ("fixed", "agent"):
        selected = [result for result in results if result["mode"] == mode]
        token_values = [
            result["actual_usage"]["total_tokens"]
            for result in selected
            if result["actual_usage"]["total_tokens"] is not None
        ]
        modes[mode] = {
            "runs": len(selected),
            "successes": sum(result["success"] for result in selected),
            "success_rate": _rate(
                sum(result["success"] for result in selected),
                len(selected),
            ),
            "requirements": sum(
                result["quality"]["final_requirements"] for result in selected
            ),
            "mean_quality_proxy_score": statistics.fmean(
                result["quality"]["quality_proxy_score"] for result in selected
            ) if selected else 0.0,
            "mean_fully_grounded_requirement_rate": statistics.fmean(
                result["quality"]["fully_grounded_requirement_rate"]
                for result in selected
            ) if selected else 0.0,
            "wall_elapsed_ms": sum(result["wall_elapsed_ms"] for result in selected),
            "actual_total_tokens": (
                sum(token_values) if len(token_values) == len(selected) else None
            ),
            "provider_calls": sum(
                result["actual_usage"]["provider_calls"] for result in selected
            ),
            "retry_count": sum(result["retry_count"] for result in selected),
        }
    paired = []
    by_case = {
        result["case_id"]: {} for result in results
    }
    for result in results:
        by_case[result["case_id"]][result["mode"]] = result
    for case_id, pair in sorted(by_case.items()):
        if set(pair) != {"fixed", "agent"}:
            continue
        paired.append(
            {
                "case_id": case_id,
                "agent_success_delta": int(pair["agent"]["success"])
                - int(pair["fixed"]["success"]),
                "agent_requirement_delta": (
                    pair["agent"]["quality"]["final_requirements"]
                    - pair["fixed"]["quality"]["final_requirements"]
                ),
                "agent_quality_proxy_delta": (
                    pair["agent"]["quality"]["quality_proxy_score"]
                    - pair["fixed"]["quality"]["quality_proxy_score"]
                ),
                "agent_wall_elapsed_ratio": _ratio(
                    pair["agent"]["wall_elapsed_ms"],
                    pair["fixed"]["wall_elapsed_ms"],
                ),
                "agent_token_ratio": _ratio(
                    pair["agent"]["actual_usage"]["total_tokens"],
                    pair["fixed"]["actual_usage"]["total_tokens"],
                ),
            }
        )
    return {
        "schema_version": "real_world_agent_evaluation_v1",
        "case_count": len(by_case),
        "run_count": len(results),
        "modes": modes,
        "paired_comparisons": paired,
        "limitations": [
            "The corpus has no exhaustive gold requirement set, so recall is not measured.",
            "quality_proxy_score measures structure and Evidence grounding, not semantic completeness or business correctness.",
            "Provider results can vary despite temperature=0; this is one observed run per mode and document.",
        ],
    }


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def render_report(
    documents: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    by_case = {document["case_id"]: document for document in documents}
    profile_by_case = {profile["case_id"]: profile for profile in profiles}
    result_by_key = {
        (result["case_id"], result["mode"]): result for result in results
    }
    lines = [
        "# SpecTrail v0.9 real-world fixed-vs-Agent evaluation",
        "",
        "This is an observed production-path comparison using live extraction and a live bounded Planner. It is not a release gate and does not introduce v0.10 behavior.",
        "",
        "## Method",
        "",
        "Both modes use the same live provider, temperature 0, strict validation, structured-if-available Evidence, automatic chunking, a 16,000-character base prompt budget, one overlap block, and fail-fast disabled. Run order alternates by case. Agent retains the v0.9 policy limits and may change only its allowlisted extraction arguments.",
        "",
        "There is no exhaustive gold set for these external documents. `quality_proxy_score` combines final-artifact structure and Evidence grounding; it cannot measure recall, semantic completeness, or business correctness. Manual sample review must therefore accompany the automatic metrics.",
        "",
        "## Corpus",
        "",
        "| Case | Format | Size class | Structure | Parsed pages | Blocks | Prompt chars |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for document in documents:
        profile = profile_by_case[document["case_id"]]
        lines.append(
            f"| `{document['case_id']}` | {document['format']} | {document['length_class']} | {document['structure_class']} | "
            f"{profile.get('page_count') or '-'} | {profile['block_count']} | {profile['estimated_prompt_chars']} |"
        )
    lines.extend(
        [
            "",
            "## Aggregate observation",
            "",
            "| Mode | Success | Requirements | Mean quality proxy | Mean fully grounded | Provider calls | Retries | Actual tokens | Wall time |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for mode in ("fixed", "agent"):
        item = summary["modes"][mode]
        tokens = item["actual_total_tokens"] if item["actual_total_tokens"] is not None else "n/a"
        lines.append(
            f"| {mode} | {item['successes']}/{item['runs']} ({item['success_rate']:.0%}) | "
            f"{item['requirements']} | {item['mean_quality_proxy_score']:.3f} | "
            f"{item['mean_fully_grounded_requirement_rate']:.1%} | {item['provider_calls']} | "
            f"{item['retry_count']} | {tokens} | {item['wall_elapsed_ms'] / 1000:.1f}s |"
        )
    lines.extend(
        [
            "",
            "## Per-document results",
            "",
            "| Case | Mode | Success | Status / outcome | Requirements | Grounded | Quality proxy | Retries | Tokens | Time |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for document in documents:
        for mode in ("fixed", "agent"):
            result = result_by_key[(document["case_id"], mode)]
            quality = result["quality"]
            tokens = result["actual_usage"]["total_tokens"]
            status = result["status"]
            if result["agent_outcome"]:
                status += f" / {result['agent_outcome']}"
            lines.append(
                f"| `{document['case_id']}` | {mode} | {'yes' if result['success'] else 'no'} | {status} | "
                f"{quality['final_requirements']} | {quality['fully_grounded_requirement_rate']:.1%} | "
                f"{quality['quality_proxy_score']:.3f} | {result['retry_count']} | "
                f"{tokens if tokens is not None else 'n/a'} | {result['wall_elapsed_ms'] / 1000:.1f}s |"
            )
    lines.extend(["", "## Agent decisions and failures", ""])
    for document in documents:
        fixed = result_by_key[(document["case_id"], "fixed")]
        agent = result_by_key[(document["case_id"], "agent")]
        lines.append(f"### `{document['case_id']}`")
        lines.append("")
        lines.append(
            "- Sequence: "
            + (" → ".join(agent["planner_tool_sequence"]) or "no valid decision")
        )
        lines.append(
            f"- Fixed failure: {fixed['error_code'] or 'none'}; Agent failure: {agent['error_code'] or 'none'}."
        )
        lines.append("")
    lines.extend(
        [
            "## Interpretation boundary",
            "",
            *[f"- {limitation}" for limitation in summary["limitations"]],
            "",
            "The final value judgment is added after manual review of paired outputs and failure traces.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        default="eval/real_world_agent_v1/manifest.json",
    )
    parser.add_argument(
        "--corpus-dir",
        default="outputs/real-world-agent-v1/corpus",
    )
    parser.add_argument(
        "--output",
        default="outputs/real-world-agent-v1",
    )
    parser.add_argument("--endpoint-id", default="real-world-eval-v1")
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--planner-model-name", default=None)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--profile-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    corpus_dir = Path(args.corpus_dir)
    output = Path(args.output)
    documents = load_manifest(manifest_path)
    if args.case:
        selected = set(args.case)
        unknown = selected - {document["case_id"] for document in documents}
        if unknown:
            raise ValueError(f"unknown corpus cases: {', '.join(sorted(unknown))}")
        documents = [
            document for document in documents if document["case_id"] in selected
        ]

    profiles = []
    document_paths = {}
    for document in documents:
        path = verify_document(document, corpus_dir)
        document_paths[document["case_id"]] = path
        profiles.append(profile_document(document, path))
    write_json(output / "corpus_profile.json", {"documents": profiles})
    if args.profile_only:
        print(f"Profiled {len(profiles)} real-world documents")
        return 0

    results = []
    for index, document in enumerate(documents):
        order = ("agent", "fixed") if index % 2 == 0 else ("fixed", "agent")
        for order_index, mode in enumerate(order, start=1):
            result_path = output / "results" / document["case_id"] / f"{mode}.json"
            if args.resume and result_path.is_file():
                result = read_json(result_path)
            else:
                result = run_mode(
                    mode=mode,
                    document=document,
                    document_path=document_paths[document["case_id"]],
                    run_dir=output / "runs" / document["case_id"] / mode,
                    endpoint_id=args.endpoint_id,
                    model_name=args.model_name,
                    planner_model_name=args.planner_model_name,
                    insecure=args.insecure,
                )
                result["run_order"] = order_index
                write_json(result_path, result)
            results.append(result)
            print(
                f"{document['case_id']} {mode}: "
                f"success={result['success']} "
                f"requirements={result['quality']['final_requirements']} "
                f"tokens={result['actual_usage']['total_tokens']}"
            )

    summary = aggregate_results(results)
    write_json(output / "summary.json", summary)
    (output / "report.md").write_text(
        render_report(documents, profiles, results, summary),
        encoding="utf-8",
    )
    print(f"Wrote real-world evaluation report to {output / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
