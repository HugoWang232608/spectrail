from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile

from spectrail.api.deps import get_task_store
from spectrail.api.schemas import (
    DocumentUploadResponse,
    TaskCreateRequest,
    TaskResponse,
    TaskRunResponse,
    TaskStatusResponse,
)
from spectrail.api.transaction_errors import task_transaction_http_error
from spectrail.core.manifest import fail_manifest, init_manifest
from spectrail.llm.errors import (
    ModelConfigurationError,
    ModelError,
    ModelPayloadContractError,
    ModelProviderError,
    ModelResponseParseError,
)
from spectrail.parsers import DocumentParseError, UnsupportedDocumentTypeError
from spectrail.pipeline import PipelineValidationError, PipelineRunner, UnsupportedModelModeError
from spectrail.tasks import (
    LocalTaskStore,
    RunGenerationChangedError,
    TaskNotFoundError,
    TaskTransactionInProgressError,
)
from spectrail.tasks.store import InvalidDocumentError, TaskNotReadyError
from spectrail.task_transactions import TaskTransactionError, task_operation


router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=TaskResponse)
def create_task(
    request: TaskCreateRequest,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    task = store.create_task(
        goal=request.goal,
        model_mode=request.model_mode,
        pipeline_config={
            "chunking_mode": request.chunking_mode,
            "max_rendered_prompt_chars": request.max_rendered_prompt_chars,
            "overlap_blocks": request.overlap_blocks,
            "validation_policy": request.validation_policy,
            "evidence_policy": request.evidence_policy,
            "fail_fast": request.fail_fast,
        },
    )
    return _task_response(task)


@router.post("/tasks/{task_id}/documents", response_model=DocumentUploadResponse)
async def upload_document(
    task_id: str,
    file: UploadFile = File(...),
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        content = await file.read()
        store.get_task(task_id)
        store.save_document(task_id, file.filename or "document.md", content)
        task = store.get_task(task_id)
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except InvalidDocumentError as exc:
        raise _error(400, "INVALID_DOCUMENT", str(exc)) from exc

    return {
        "task_id": task_id,
        "status": task["status"],
        "filename": task["original_filename"],
    }


@router.post("/tasks/{task_id}/run", response_model=TaskRunResponse)
def run_task(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        task_dir = store.get_task_dir(task_id)
        with task_operation(task_dir, "api_pipeline_run"):
            return _run_task_locked(task_id, store)
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskTransactionError as exc:
        raise task_transaction_http_error(exc) from exc


def _run_task_locked(task_id: str, store: LocalTaskStore) -> dict:
    run_generation: int | None = None
    try:
        task = store.get_task(task_id)
        document = store.get_input_document(task_id)
        task_dir = store.get_task_dir(task_id)
        task = store.begin_run(task_id)
        run_generation = task["run_generation"]
        store.reset_output_from_pipeline(task_id)
        config = task.get("pipeline_config", {})
        result = PipelineRunner().extract(
            document_path=document,
            output_dir=task_dir,
            model_mode=task["model_mode"],
            chunking_mode=config.get("chunking_mode", "auto"),
            max_rendered_prompt_chars=config.get("max_rendered_prompt_chars", 16000),
            overlap_blocks=config.get("overlap_blocks", 1),
            validation_policy=config.get("validation_policy", "strict"),
            evidence_policy=config.get(
                "evidence_policy", "structured_if_available"
            ),
            fail_fast=config.get("fail_fast", False),
            run_generation=run_generation,
        )
        manifest = store.read_manifest(task_id) or {}
        store.update_task(task_id, status=manifest.get("status", "completed"))
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "DOCUMENT_NOT_UPLOADED", str(exc)) from exc
    except UnsupportedModelModeError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(400, "INVALID_MODEL_MODE", str(exc)) from exc
    except ModelConfigurationError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(400, "INVALID_MODEL_CONFIGURATION", str(exc)) from exc
    except ModelResponseParseError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(422, "MODEL_RESPONSE_PARSE_FAILED", str(exc)) from exc
    except ModelPayloadContractError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(422, "MODEL_PAYLOAD_CONTRACT_FAILED", str(exc)) from exc
    except ModelProviderError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(502, "MODEL_PROVIDER_FAILED", str(exc)) from exc
    except ModelError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(500, "MODEL_FAILED", str(exc)) from exc
    except UnsupportedDocumentTypeError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(400, "INVALID_DOCUMENT", str(exc)) from exc
    except DocumentParseError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(422, "DOCUMENT_PARSE_FAILED", str(exc)) from exc
    except PipelineValidationError as exc:
        _mark_task_failed(store, task_id, run_generation, exc)
        raise _error(422, "PIPELINE_VALIDATION_FAILED", str(exc)) from exc
    except TaskTransactionInProgressError as exc:
        raise task_transaction_http_error(exc) from exc
    except Exception as exc:
        try:
            _mark_task_failed(store, task_id, run_generation, exc)
            manifest = store.read_manifest(task_id) or {"status": "failed", "error": str(exc)}
        except Exception:
            manifest = {"status": "failed", "error": str(exc)}
        raise _error(500, "PIPELINE_FAILED", str(exc)) from exc

    del result
    return {
        "task_id": task_id,
        "status": manifest["status"],
        "run_generation": run_generation,
        "manifest": manifest,
    }


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        task, manifest = store.read_status_snapshot(task_id)
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    return {
        "task_id": task_id,
        "status": task["status"],
        "run_generation": task["run_generation"],
        "task": task,
        "manifest": manifest,
    }


@router.get("/tasks/{task_id}/reqir")
def get_reqir(
    task_id: str,
    response: Response,
    expected_run_generation: int = Query(ge=0),
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        run_generation, reqir = store.read_reqir(
            task_id,
            expected_run_generation=expected_run_generation,
        )
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except RunGenerationChangedError as exc:
        raise _error(409, "RUN_GENERATION_CHANGED", str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Spectrail-Run-Generation"] = str(run_generation)
    return reqir


@router.get("/tasks/{task_id}/chunks")
def get_chunks(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> list[dict]:
    try:
        return store.read_chunks(task_id)
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc


@router.get("/tasks/{task_id}/quarantined")
def get_quarantined(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        return store.read_quarantined(task_id)
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc


def _task_response(task: dict) -> dict:
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "output_dir": task["output_dir"],
    }


def _mark_task_failed(
    store: LocalTaskStore,
    task_id: str,
    run_generation: int | None,
    error: Exception,
) -> None:
    try:
        manifest = store.read_manifest(task_id)
        if run_generation is not None and (
            manifest is None
            or manifest.get("run_generation") != run_generation
        ):
            task = store.get_task(task_id)
            manifest = fail_manifest(
                init_manifest(
                    task_id=task_id,
                    input_document=str(task.get("input_document") or ""),
                    output_dir=str(task["output_dir"]),
                    model_mode=str(task["model_mode"]),
                    run_generation=run_generation,
                ),
                str(error),
            )
            manifest["error_code"] = type(error).__name__
            store.write_manifest(task_id, manifest)
        elif manifest is not None and manifest.get("status") != "failed":
            manifest = fail_manifest(manifest, str(error))
            manifest["error_code"] = type(error).__name__
            store.write_manifest(task_id, manifest)
        store.update_task(task_id, status="failed")
    except Exception:
        return


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
