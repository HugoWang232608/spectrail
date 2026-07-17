from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spectrail.aggregation import CandidateAggregator
from spectrail.chunking import ChunkPlanningError, ChunkingConfig, SectionAwareChunker
from spectrail.core.io import (
    ensure_dir,
    model_list_dump,
    read_json,
    reqir_package_dump,
    write_json,
)
from spectrail.core.manifest import complete_manifest, fail_manifest, init_manifest
from spectrail.core.models import RequirementIR, ValidationIssue, ValidationReport
from spectrail.core.workflow import build_fixed_plan
from spectrail.exporters.source_map_exporter import build_source_map
from spectrail.exporters.xlsx_exporter import export_requirements_xlsx
from spectrail.extractors.ears_normalizer import normalize_requirements
from spectrail.extractors.reqir_extractor import (
    ExtractionBatchResult,
    RejectedModelItem,
    ReqIRExtractor,
)
from spectrail.evidence.errors import EvidenceReferenceError
from spectrail.evidence.index_builder import ensure_evidence_index
from spectrail.evidence.enricher import SourceEvidenceEnricher
from spectrail.evidence.models import EvidenceIndex
from spectrail.evidence.quote_matcher import build_quote_match_registry
from spectrail.evidence.source_identity import canonicalize_source_cell_ids
from spectrail.llm.base import ModelRequest, ModelResponse
from spectrail.llm.errors import (
    ModelPayloadContractError,
    ModelProviderError,
    ModelResponseParseError,
)
from spectrail.llm.factory import create_model_client
from spectrail.llm.fingerprints import build_request_identity
from spectrail.llm.openai_compatible import OpenAICompatibleModel
from spectrail.llm.prompt_builder import CHUNKED_PROMPT_VERSION, PROMPT_VERSION, build_reqir_prompt
from spectrail.llm.request_profile import ModelRequestProfile, adapter_for_profile
from spectrail.parsers import ParsedDocument, parse_document
from spectrail.pipeline.config import PipelineConfig
from spectrail.review.review_log import collect_review_log
from spectrail.task_transactions import task_operation
from spectrail.validators.ears_validator import BasicEARSValidator
from spectrail.validators.schema_validator import SchemaValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator
from spectrail.validators.source_locator_validator import SourceLocatorValidator


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
    status: str = "completed"


class PipelineRunner:
    def extract(
        self,
        document_path: str | Path,
        output_dir: str | Path,
        model_mode: str = "mock",
        model_name: str | None = None,
        recorded_fixture: str | Path | None = None,
        dump_prompt: bool = False,
        insecure: bool = False,
        *,
        chunking_mode: str = "auto",
        max_rendered_prompt_chars: int = 16000,
        overlap_blocks: int = 1,
        validation_policy: str = "strict",
        evidence_policy: str = "structured_if_available",
        fail_fast: bool = False,
        config: PipelineConfig | None = None,
        parsed_document: ParsedDocument | None = None,
    ) -> PipelineResult:
        with task_operation(output_dir, "pipeline"):
            return self._extract_locked(
                document_path=document_path,
                output_dir=output_dir,
                model_mode=model_mode,
                model_name=model_name,
                recorded_fixture=recorded_fixture,
                dump_prompt=dump_prompt,
                insecure=insecure,
                chunking_mode=chunking_mode,
                max_rendered_prompt_chars=max_rendered_prompt_chars,
                overlap_blocks=overlap_blocks,
                validation_policy=validation_policy,
                evidence_policy=evidence_policy,
                fail_fast=fail_fast,
                config=config,
                parsed_document=parsed_document,
            )

    def _extract_locked(
        self,
        document_path: str | Path,
        output_dir: str | Path,
        model_mode: str = "mock",
        model_name: str | None = None,
        recorded_fixture: str | Path | None = None,
        dump_prompt: bool = False,
        insecure: bool = False,
        *,
        chunking_mode: str = "auto",
        max_rendered_prompt_chars: int = 16000,
        overlap_blocks: int = 1,
        validation_policy: str = "strict",
        evidence_policy: str = "structured_if_available",
        fail_fast: bool = False,
        config: PipelineConfig | None = None,
        parsed_document: ParsedDocument | None = None,
    ) -> PipelineResult:
        pipeline_config = config or PipelineConfig(
            model_mode=model_mode,
            model_name=model_name,
            recorded_fixture=recorded_fixture,
            chunking=ChunkingConfig(
                mode=chunking_mode,  # type: ignore[arg-type]
                max_rendered_prompt_chars=max_rendered_prompt_chars,
                overlap_blocks=overlap_blocks,
                fail_fast=fail_fast,
            ),
            validation_policy=validation_policy,  # type: ignore[arg-type]
            evidence_policy=evidence_policy,  # type: ignore[arg-type]
            dump_prompt=dump_prompt,
            insecure=insecure,
        )
        model_mode = pipeline_config.model_mode
        if model_mode not in {"mock", "recorded", "live"}:
            raise UnsupportedModelModeError(
                "P4 supports --model-mode mock, recorded, or live for local runs"
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

        plan = build_fixed_plan(task_id=task_id, input_document=document.as_posix(), model_mode=model_mode)
        write_json(plan_path, plan.model_dump(mode="json"))
        manifest = init_manifest(
            task_id=task_id,
            input_document=document.as_posix(),
            output_dir=output.as_posix(),
            model_mode=model_mode,
        )
        write_json(manifest_path, manifest)

        validated_requirements = []
        final_status = "failed"
        blocks = []
        chunks = []
        accepted_candidates = []
        rejected_items = []
        chunk_errors: list[dict[str, Any]] = []
        model_items_total = 0
        model_call_count = 0
        chunk_index_entries: list[dict[str, Any]] = []
        total_elapsed_ms = 0
        total_response_chars = 0
        requirements = []
        quarantined = []
        aggregation = None
        evidence_index = None
        quote_matches = None
        try:
            if parsed_document is None:
                parsed_document = parse_document(document, document_id="doc_001")
            elif (
                parsed_document.document_id != "doc_001"
                or parsed_document.document_name != document.name
            ):
                raise PipelineValidationError("PREPARSED_DOCUMENT_MISMATCH")
            blocks = parsed_document.blocks
            if not blocks:
                raise PipelineValidationError("NO_EXTRACTABLE_CONTENT")
            evidence_index = ensure_evidence_index(document, parsed_document)
            plan.steps[0].config = {
                "selected_parser": parsed_document.parser_name,
                "source_format": parsed_document.source_format,
                "warnings": parsed_document.warnings,
            }
            write_json(plan_path, plan.model_dump(mode="json"))
            (parsed_dir / "document.md").write_text(parsed_document.text, encoding="utf-8")
            write_json(parsed_dir / "blocks.json", model_list_dump(blocks))
            write_json(
                parsed_dir / "evidence_index.json",
                evidence_index.model_dump(mode="json"),
            )

            model_client = create_model_client(
                model_mode=model_mode,
                model_name=pipeline_config.model_name,
                recorded_fixture=pipeline_config.recorded_fixture,
            )
            profile = _resolve_request_profile(pipeline_config, model_client)

            def request_factory(request_blocks, metadata):
                metadata = dict(metadata)
                metadata["insecure"] = pipeline_config.insecure
                metadata["evidence_policy"] = pipeline_config.evidence_policy
                metadata["prompt_version"] = (
                    CHUNKED_PROMPT_VERSION if metadata.get("chunked") else PROMPT_VERSION
                )
                return ModelRequest(
                    document_text="\n\n".join(block.text for block in request_blocks),
                    blocks=list(request_blocks),
                    document_name=document.name,
                    source_format=parsed_document.source_format,
                    parser_name=parsed_document.parser_name,
                    model_mode=model_mode,
                    model_name=profile.model_name,
                    request_profile=profile,
                    metadata=metadata,
                    evidence_index=evidence_index,
                )

            chunks = SectionAwareChunker().chunk(
                blocks,
                pipeline_config.chunking,
                request_factory=request_factory,
                prompt_renderer=build_reqir_prompt,
            )
            chunked_execution = len(chunks) > 1 or pipeline_config.chunking.mode == "force"
            if (
                model_mode == "recorded"
                and len(chunks) > 1
                and not Path(
                    pipeline_config.recorded_fixture
                    or "fixtures/recorded/sample_srs_reqir_response.json"
                ).is_dir()
            ):
                raise PipelineValidationError("RECORDED_FIXTURE_NOT_CHUNK_AWARE")
            if chunked_execution:
                plan = build_fixed_plan(
                    task_id=task_id,
                    input_document=document.as_posix(),
                    model_mode=model_mode,
                    p4=True,
                )
                plan.steps[0].config = {
                    "selected_parser": parsed_document.parser_name,
                    "source_format": parsed_document.source_format,
                    "warnings": parsed_document.warnings,
                }
                plan.steps[1].config = {
                    **asdict(pipeline_config.chunking),
                    "chunk_count": len(chunks),
                    "oversized_chunk_count": sum(
                        "CHUNK_OVERSIZED_BLOCK" in chunk.warnings for chunk in chunks
                    ),
                }
                write_json(plan_path, plan.model_dump(mode="json"))
            write_json(parsed_dir / "chunks.json", [_chunk_dump(chunk) for chunk in chunks])

            extractor = ReqIRExtractor()
            successful_responses: list[ModelResponse] = []
            table_cell_required_block_ids = (
                set()
                if pipeline_config.evidence_policy == "quote_only"
                else {
                    block.block_id
                    for block in evidence_index.blocks
                    if "table_cell" in block.available_capabilities
                }
            )

            for chunk in chunks:
                chunk_dir = ensure_dir(extracted_dir / "chunk_results" / chunk.chunk_id)
                metadata = {
                    "chunked": chunked_execution,
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.index,
                    "chunk_count": len(chunks),
                    "chunk_index_rendered": f"{chunk.index:08d}",
                    "chunk_count_rendered": f"{len(chunks):08d}",
                    "chunk_fingerprint": chunk.chunk_fingerprint,
                    "new_block_ids": chunk.new_block_ids,
                    "overlap_block_ids": chunk.overlap_block_ids,
                    "context_block_ids": chunk.context_block_ids,
                    "prompt_version": CHUNKED_PROMPT_VERSION if chunked_execution else PROMPT_VERSION,
                }
                request = request_factory(chunk.blocks, metadata)
                prompt = build_reqir_prompt(request)
                if (
                    not {
                        "CHUNK_OVERSIZED_BLOCK",
                        "CHUNK_PROMPT_OVER_BUDGET",
                    }.intersection(chunk.warnings)
                    and len(prompt) > pipeline_config.chunking.max_rendered_prompt_chars
                ):
                    raise ChunkPlanningError("final prompt exceeds configured budget")
                request_fingerprint, sanitized_request = build_request_identity(prompt, profile)
                request.metadata["request_fingerprint"] = request_fingerprint
                write_json(chunk_dir / "request_profile.json", profile.to_dict())
                write_json(chunk_dir / "request.sanitized.json", sanitized_request)
                if pipeline_config.dump_prompt:
                    (chunk_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

                started = time.perf_counter()
                model_call_count += 1
                try:
                    response = model_client.generate(request)
                except (ModelProviderError, ModelResponseParseError) as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    total_elapsed_ms += elapsed_ms
                    error = _chunk_error(chunk.chunk_id, exc, elapsed_ms)
                    write_json(chunk_dir / "error.json", error)
                    chunk_errors.append(error)
                    chunk_index_entries.append(
                        {"chunk_id": chunk.chunk_id, "status": "failed", "error": error}
                    )
                    if pipeline_config.chunking.fail_fast or len(chunks) == 1:
                        raise
                    continue

                elapsed_ms = int((time.perf_counter() - started) * 1000)
                total_elapsed_ms += elapsed_ms
                response_chars = len(
                    response.raw_text or json.dumps(response.payload, ensure_ascii=False)
                )
                total_response_chars += response_chars
                write_json(chunk_dir / "model_response.json", response.payload)
                if response.raw_text:
                    (chunk_dir / "model_response.raw.txt").write_text(
                        response.raw_text, encoding="utf-8"
                    )
                try:
                    batch = extractor.extract_batch(
                        payload=response.payload,
                        blocks=chunk.blocks,
                        document_name=document.name,
                        model_mode=model_mode,
                        chunk_id=chunk.chunk_id if chunked_execution else None,
                        chunk_fingerprint=chunk.chunk_fingerprint if chunked_execution else None,
                        request_fingerprint=request_fingerprint if chunked_execution else None,
                        context_block_ids=set(chunk.context_block_ids),
                        table_cell_required_block_ids=table_cell_required_block_ids,
                    )
                except ModelPayloadContractError as exc:
                    error = _chunk_error(chunk.chunk_id, exc, elapsed_ms)
                    write_json(chunk_dir / "error.json", error)
                    chunk_errors.append(error)
                    chunk_index_entries.append(
                        {"chunk_id": chunk.chunk_id, "status": "failed", "error": error}
                    )
                    if pipeline_config.chunking.fail_fast or len(chunks) == 1:
                        raise
                    continue

                model_items = response.payload["items"]
                batch = _prepare_evidence_batch(
                    batch,
                    evidence_index,
                    model_items,
                )
                successful_responses.append(response)
                model_items_total += len(model_items)
                _stamp_chunk_metadata(batch, chunk.index)
                accepted_candidates.extend(batch.accepted_candidates)
                rejected_items.extend(batch.rejected_items)
                write_json(
                    chunk_dir / "candidates.accepted.json",
                    model_list_dump(batch.accepted_candidates),
                )
                write_json(
                    chunk_dir / "candidates.rejected.json",
                    [_rejected_dump(item) for item in batch.rejected_items],
                )
                chunk_status = "completed_with_warnings" if batch.rejected_items else "completed"
                chunk_index_entries.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "response_path": f"chunk_results/{chunk.chunk_id}/model_response.json",
                        "status": chunk_status,
                        "model_name": response.model_name,
                        "request_fingerprint": request_fingerprint,
                        "accepted_candidate_count": len(batch.accepted_candidates),
                        "rejected_item_count": len(batch.rejected_items),
                        "elapsed_ms": elapsed_ms,
                    }
                )

            write_json(extracted_dir / "model_response.index.json", chunk_index_entries)
            write_json(extracted_dir / "chunk_errors.json", chunk_errors)
            write_json(extracted_dir / "rejected_model_items.json", [_rejected_dump(item) for item in rejected_items])
            if not successful_responses:
                raise PipelineValidationError("ALL_CHUNKS_FAILED")

            if len(chunks) == 1:
                response = successful_responses[0]
                write_json(extracted_dir / "model_response.json", response.payload)
                if response.raw_text:
                    (extracted_dir / "model_response.raw.txt").write_text(response.raw_text, encoding="utf-8")
                if pipeline_config.dump_prompt and response.prompt:
                    (extracted_dir / "prompt.txt").write_text(response.prompt, encoding="utf-8")

            quote_matches = build_quote_match_registry(
                accepted_candidates,
                blocks,
                evidence_fingerprint=evidence_index.evidence_fingerprint,
                evidence_index=evidence_index,
            )
            write_json(
                extracted_dir / "quote_matches.json",
                quote_matches.model_dump(mode="json"),
            )
            SourceEvidenceEnricher().enrich(
                accepted_candidates,
                evidence_index,
                quote_matches,
                blocks,
            )
            aggregation = CandidateAggregator().aggregate(accepted_candidates, blocks)
            requirements = normalize_requirements(aggregation.requirements)
            primary_response = successful_responses[0]
            write_json(
                extracted_dir / "reqir.raw.json",
                reqir_package_dump(
                    requirements,
                    metadata={
                        "model_mode": model_mode,
                        "model_name": primary_response.model_name,
                        "prompt_version": (
                            CHUNKED_PROMPT_VERSION if chunked_execution else PROMPT_VERSION
                        ),
                        "document": document.name,
                        "source_format": parsed_document.source_format,
                        "parser": parsed_document.parser_name,
                        "parser_warnings": parsed_document.warnings,
                        "chunk_count": len(chunks),
                        "evidence_fingerprint": evidence_index.evidence_fingerprint,
                    },
                ),
            )
            write_json(extracted_dir / "duplicate_groups.json", aggregation.duplicate_groups)
            write_json(
                extracted_dir / "aggregation_report.json",
                {
                    "raw_candidates": len(accepted_candidates),
                    "aggregated_requirements": len(requirements),
                    "collapsed_exact_candidates": aggregation.collapsed_exact_candidates,
                    "field_conflict_count": aggregation.field_conflict_count,
                },
            )

            schema_report = SchemaValidator().validate(requirements)
            if not schema_report.valid:
                write_json(extracted_dir / "validation_report.json", schema_report.model_dump(mode="json"))
                raise PipelineValidationError("schema validation failed")

            quote_validated_requirements, source_report = SourceQuoteValidator().validate(
                requirements,
                blocks,
                quote_matches,
            )
            locator_validated_requirements, locator_report, locator_failures = (
                SourceLocatorValidator().validate(
                    requirements,
                    evidence_index,
                    quote_matches,
                    policy=pipeline_config.evidence_policy,
                    document_blocks=blocks,
                )
            )
            quote_valid_ids = {item.id for item in quote_validated_requirements}
            locator_valid_ids = {item.id for item in locator_validated_requirements}
            valid_ids = quote_valid_ids & locator_valid_ids
            validated_requirements = [
                item for item in requirements if item.id in valid_ids
            ]
            quarantined = [item for item in requirements if item.id not in valid_ids]
            ears_report = BasicEARSValidator().validate(validated_requirements)
            validation_report = _merge_reports(
                schema_report,
                source_report,
                locator_report,
                ears_report,
            )
            if rejected_items and not accepted_candidates:
                validation_report.add_issue(
                    ValidationIssue(
                        level="error",
                        code="MODEL_OUTPUT_VALIDATION_FAILED",
                        message="all model items failed ReqIR extraction",
                    )
                )
            for conflict_count in range(aggregation.field_conflict_count):
                validation_report.add_issue(
                    ValidationIssue(
                        level="warning",
                        code="AGGREGATION_FIELD_CONFLICT",
                        message="aggregated candidate contains conflicting structured fields",
                        metadata={"conflict_index": conflict_count},
                    )
                )
            for rejected in rejected_items:
                validation_report.add_issue(
                    ValidationIssue(
                        level="warning",
                        code=rejected.error_code,
                        message=rejected.error_message,
                        metadata={
                            "chunk_id": rejected.chunk_id,
                            "item_index": rejected.item_index,
                        },
                    )
                )
            write_json(extracted_dir / "validation_report.json", validation_report.model_dump(mode="json"))
            write_json(
                extracted_dir / "source_locator_report.json",
                locator_report.model_dump(mode="json"),
            )
            write_json(
                extracted_dir / "source_locator_failures.json",
                locator_failures,
            )
            write_json(
                extracted_dir / "reqir.quarantined.json",
                reqir_package_dump(
                    quarantined,
                    metadata={
                        "validation_state": "quarantined",
                        "evidence_fingerprint": evidence_index.evidence_fingerprint,
                    },
                ),
            )
            if not source_report.valid and pipeline_config.validation_policy == "strict":
                raise PipelineValidationError("source quote validation failed")
            if not locator_report.valid and pipeline_config.validation_policy == "strict":
                raise PipelineValidationError("source locator validation failed")

            warning_codes = []
            zero_result_reason = None
            if not accepted_candidates:
                if model_items_total > 0 and len(rejected_items) == model_items_total:
                    raise PipelineValidationError("NO_VALID_MODEL_ITEMS")
                if chunk_errors:
                    zero_result_reason = "PARTIAL_EXECUTION_EMPTY_RESULT"
                    warning_codes.append("PARTIAL_CHUNK_FAILURE")
                else:
                    zero_result_reason = "NO_REQUIREMENTS_FOUND"
                    warning_codes.append("NO_REQUIREMENTS_FOUND")
            elif not validated_requirements and quarantined:
                zero_result_reason = "ALL_CANDIDATES_QUARANTINED"
                warning_codes.append(zero_result_reason)
            if chunk_errors and "PARTIAL_CHUNK_FAILURE" not in warning_codes:
                warning_codes.append("PARTIAL_CHUNK_FAILURE")
            if rejected_items:
                warning_codes.append("MODEL_ITEMS_REJECTED")
            if quarantined:
                warning_codes.append("CANDIDATES_QUARANTINED")
            if any(issue.level == "warning" for issue in locator_report.issues):
                warning_codes.append("SOURCE_LOCATOR_WARNINGS")
            if aggregation.field_conflict_count:
                warning_codes.append("AGGREGATION_FIELD_CONFLICT")
            if any("CHUNK_PROMPT_OVER_BUDGET" in chunk.warnings for chunk in chunks):
                warning_codes.append("CHUNK_PROMPT_OVER_BUDGET")
            if any("CHUNK_OVERSIZED_BLOCK" in chunk.warnings for chunk in chunks):
                warning_codes.append("CHUNK_OVERSIZED_BLOCK")
            final_status = "completed_with_warnings" if warning_codes else "completed"

            write_json(
                validated_reqir_path,
                reqir_package_dump(
                    validated_requirements,
                    metadata={
                        "validation_state": "validated",
                        "document": document.name,
                        "source_format": parsed_document.source_format,
                        "parser": parsed_document.parser_name,
                        "evidence_fingerprint": evidence_index.evidence_fingerprint,
                    },
                ),
            )
            write_json(extracted_dir / "source_map.json", build_source_map(validated_requirements))
            write_json(review_dir / "review_log.json", collect_review_log(validated_requirements))
            write_json(
                exported_reqir_path,
                reqir_package_dump(
                    validated_requirements,
                    metadata={
                        "export_state": "unreviewed_snapshot",
                        "document": document.name,
                        "source_format": parsed_document.source_format,
                        "parser": parsed_document.parser_name,
                        "evidence_fingerprint": evidence_index.evidence_fingerprint,
                    },
                ),
            )
            export_requirements_xlsx(validated_requirements, xlsx_path)

            counts = {
                "blocks": len(blocks),
                "chunks": len(chunks),
                "chunks_completed": sum(item.get("status") == "completed" for item in chunk_index_entries),
                "chunks_completed_with_warnings": sum(
                    item.get("status") == "completed_with_warnings" for item in chunk_index_entries
                ),
                "chunks_failed": len(chunk_errors),
                "model_items_total": model_items_total,
                "model_call_count": model_call_count,
                "model_items_accepted": len(accepted_candidates),
                "model_items_rejected": len(rejected_items),
                "raw_requirements": len(accepted_candidates),
                "raw_candidates": len(accepted_candidates),
                "collapsed_overlap_duplicates": aggregation.collapsed_exact_candidates,
                "aggregated_requirements": len(requirements),
                "field_conflicts": aggregation.field_conflict_count,
                "validated_requirements": len(validated_requirements),
                "quarantined_requirements": len(quarantined),
                "duplicate_groups": len(aggregation.duplicate_groups),
                "source_quote_passed": len(quote_validated_requirements),
                "source_quote_failed": len(requirements) - len(quote_validated_requirements),
                "source_locator_passed": len(locator_validated_requirements),
                "source_locator_failed": len(requirements) - len(locator_validated_requirements),
            }
            completed_manifest = complete_manifest(
                manifest,
                counts=counts,
                outputs={
                    "document": "parsed/document.md",
                    "blocks": "parsed/blocks.json",
                    "evidence_index": "parsed/evidence_index.json",
                    "chunks": "parsed/chunks.json",
                    "reqir_raw": "extracted/reqir.raw.json",
                    "reqir_validated": "extracted/reqir.validated.json",
                    "reqir_quarantined": "extracted/reqir.quarantined.json",
                    "source_map": "extracted/source_map.json",
                    "quote_matches": "extracted/quote_matches.json",
                    "validation_report": "extracted/validation_report.json",
                    "source_locator_report": "extracted/source_locator_report.json",
                    "source_locator_failures": "extracted/source_locator_failures.json",
                    "review_log": "review/review_log.json",
                    "reqir_export": "exports/reqir.json",
                    "xlsx": "exports/requirements.xlsx",
                },
                status=final_status,
                warning_codes=warning_codes,
                zero_result_reason=zero_result_reason,
            )
            completed_manifest["model"] = {
                "mode": model_mode,
                "name": primary_response.model_name,
                "prompt_version": CHUNKED_PROMPT_VERSION if chunked_execution else PROMPT_VERSION,
                "recorded_fixture": (
                    primary_response.metadata.get("fixture_path") if model_mode == "recorded" else None
                ),
            }
            completed_manifest["request_profile"] = profile.to_dict()
            completed_manifest["parser"] = {
                "source_format": parsed_document.source_format,
                "parser_name": parsed_document.parser_name,
                "warnings": parsed_document.warnings,
            }
            completed_manifest["evidence"] = {
                "schema_version": evidence_index.schema_version,
                "policy": pipeline_config.evidence_policy,
                "source_sha256": evidence_index.source_sha256,
                "evidence_fingerprint": evidence_index.evidence_fingerprint,
                "block_count": len(evidence_index.blocks),
                "table_count": len(evidence_index.tables),
                "cell_count": len(evidence_index.cells),
                "quote_match_count": len(quote_matches.entries),
            }
            completed_manifest["execution"] = {
                "elapsed_ms": total_elapsed_ms,
                "rendered_prompt_chars": sum(chunk.rendered_prompt_chars for chunk in chunks),
                "response_chars": total_response_chars,
                "estimated_tokens": sum(chunk.estimated_tokens for chunk in chunks),
            }
            write_json(manifest_path, completed_manifest)
        except Exception as exc:
            failed = fail_manifest(manifest, str(exc))
            failed["error_code"] = type(exc).__name__
            failed["counts"] = {
                "blocks": len(blocks),
                "chunks": len(chunks),
                "chunks_completed": sum(
                    item.get("status") == "completed" for item in chunk_index_entries
                ),
                "chunks_completed_with_warnings": sum(
                    item.get("status") == "completed_with_warnings"
                    for item in chunk_index_entries
                ),
                "chunks_failed": len(chunk_errors),
                "model_items_total": model_items_total,
                "model_call_count": model_call_count,
                "model_items_accepted": len(accepted_candidates),
                "model_items_rejected": len(rejected_items),
                "raw_candidates": len(accepted_candidates),
                "aggregated_requirements": len(requirements),
                "validated_requirements": len(validated_requirements),
                "quarantined_requirements": len(quarantined),
                "collapsed_overlap_duplicates": (
                    aggregation.collapsed_exact_candidates if aggregation is not None else 0
                ),
                "field_conflicts": (
                    aggregation.field_conflict_count if aggregation is not None else 0
                ),
            }
            failed["execution"] = {
                "elapsed_ms": total_elapsed_ms,
                "rendered_prompt_chars": sum(chunk.rendered_prompt_chars for chunk in chunks),
                "response_chars": total_response_chars,
                "estimated_tokens": sum(chunk.estimated_tokens for chunk in chunks),
            }
            if str(exc) in {
                "NO_VALID_MODEL_ITEMS",
                "ALL_CHUNKS_FAILED",
                "NO_EXTRACTABLE_CONTENT",
            }:
                failed["zero_result_reason"] = (
                    str(exc) if str(exc) != "NO_EXTRACTABLE_CONTENT" else None
                )
                failed["error_code"] = str(exc)
            write_json(manifest_path, failed)
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
            status=final_status,
        )


def _resolve_request_profile(config: PipelineConfig, model_client: Any) -> ModelRequestProfile:
    if config.model_mode == "mock":
        if config.request_profile is not None:
            return config.request_profile
        return ModelRequestProfile("openai_compatible_v1", "mock", "mock-fixture")
    if config.model_mode == "recorded":
        fixture = Path(config.recorded_fixture or "fixtures/recorded/sample_srs_reqir_response.json")
        if fixture.is_dir():
            manifest = read_json(fixture / "manifest.json")
            profile = manifest.get("metadata", {}).get("request_profile")
            if not isinstance(profile, dict):
                raise PipelineValidationError("recorded bundle request_profile missing")
            try:
                bundle_profile = ModelRequestProfile(**profile)
                adapter_for_profile(bundle_profile)
            except ValueError as exc:
                raise PipelineValidationError("RECORDED_PROVIDER_ADAPTER_UNSUPPORTED") from exc
            if config.request_profile is not None and config.request_profile != bundle_profile:
                raise PipelineValidationError("RECORDED_REQUEST_PROFILE_MISMATCH")
            if config.model_name is not None and config.model_name != bundle_profile.model_name:
                raise PipelineValidationError("RECORDED_REQUEST_PROFILE_MISMATCH")
            return bundle_profile
        metadata = read_json(fixture).get("metadata", {}) if fixture.exists() else {}
        fixture_profile = ModelRequestProfile(
            "openai_compatible_v1",
            "recorded",
            config.model_name or metadata.get("model_name", "recorded-fixture"),
        )
        if config.request_profile is not None:
            return config.request_profile
        return fixture_profile
    if isinstance(model_client, OpenAICompatibleModel):
        return model_client.resolve_request_profile(
            config.request_profile,
            insecure=config.insecure,
        )
    if config.request_profile is not None:
        return config.request_profile
    raise PipelineValidationError("unable to resolve model request profile")


def _chunk_dump(chunk: Any) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "index": chunk.index,
        "block_ids": chunk.block_ids,
        "new_block_ids": chunk.new_block_ids,
        "overlap_block_ids": chunk.overlap_block_ids,
        "context_block_ids": chunk.context_block_ids,
        "section_path": chunk.section_path,
        "content_chars": chunk.content_chars,
        "rendered_prompt_chars": chunk.rendered_prompt_chars,
        "estimated_tokens": chunk.estimated_tokens,
        "chunk_fingerprint": chunk.chunk_fingerprint,
        "warnings": chunk.warnings,
    }


def _rejected_dump(item: Any) -> dict[str, Any]:
    return asdict(item)


def _chunk_error(chunk_id: str, exc: Exception, elapsed_ms: int) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "error_code": type(exc).__name__,
        "error_message": str(exc),
        "elapsed_ms": elapsed_ms,
    }


def _stamp_chunk_metadata(batch: ExtractionBatchResult, chunk_index: int) -> None:
    for candidate in batch.accepted_candidates:
        candidate.metadata["chunk_index"] = chunk_index


def _prepare_evidence_batch(
    batch: ExtractionBatchResult,
    evidence_index: EvidenceIndex,
    model_items: list[Any],
) -> ExtractionBatchResult:
    prepared: list[RequirementIR] = []
    rejected = list(batch.rejected_items)
    for candidate in batch.accepted_candidates:
        try:
            canonicalize_source_cell_ids([candidate], evidence_index)
        except EvidenceReferenceError as exc:
            item_index = int(candidate.metadata.get("raw_item_index", -1))
            raw_item = (
                model_items[item_index]
                if 0 <= item_index < len(model_items)
                else None
            )
            rejected.append(
                RejectedModelItem(
                    chunk_id=candidate.metadata.get("chunk_id"),
                    item_index=item_index,
                    raw_item=raw_item,
                    error_code=_evidence_preparation_error_code(exc),
                    error_message=str(exc),
                )
            )
            continue
        prepared.append(candidate)
    return ExtractionBatchResult(
        accepted_candidates=prepared,
        rejected_items=rejected,
    )


def _evidence_preparation_error_code(exc: EvidenceReferenceError) -> str:
    message = str(exc).lower()
    if "row" in message:
        return "MODEL_ITEM_INVALID_TABLE_ROW_INDEX"
    return "MODEL_ITEM_INVALID_CELL_IDS"


def _merge_reports(*reports: ValidationReport) -> ValidationReport:
    merged = ValidationReport(valid=True)
    for report in reports:
        for issue in report.issues:
            merged.add_issue(issue)
    return merged
