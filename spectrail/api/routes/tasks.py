from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from spectrail.api.deps import get_task_store
from spectrail.api.schemas import (
    DocumentUploadResponse,
    TaskCreateRequest,
    TaskResponse,
    TaskRunResponse,
    TaskStatusResponse,
)
from spectrail.parsers import DocumentParseError, UnsupportedDocumentTypeError
from spectrail.pipeline import PipelineValidationError, PipelineRunner, UnsupportedModelModeError
from spectrail.tasks import LocalTaskStore, TaskNotFoundError
from spectrail.tasks.store import InvalidDocumentError, TaskNotReadyError


router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=TaskResponse)
def create_task(
    request: TaskCreateRequest,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    task = store.create_task(goal=request.goal, model_mode=request.model_mode)
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
        task = store.get_task(task_id)
        document = store.get_input_document(task_id)
        task_dir = store.get_task_dir(task_id)
        store.update_task(task_id, status="running")
        store.reset_output_from_pipeline(task_id)
        result = PipelineRunner().extract(
            document_path=document,
            output_dir=task_dir,
            model_mode=task["model_mode"],
        )
        manifest = store.read_manifest(task_id) or {}
        store.update_task(task_id, status=manifest.get("status", "completed"))
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "DOCUMENT_NOT_UPLOADED", str(exc)) from exc
    except UnsupportedModelModeError as exc:
        _mark_task_failed(store, task_id)
        raise _error(400, "INVALID_MODEL_MODE", str(exc)) from exc
    except UnsupportedDocumentTypeError as exc:
        _mark_task_failed(store, task_id)
        raise _error(400, "INVALID_DOCUMENT", str(exc)) from exc
    except DocumentParseError as exc:
        _mark_task_failed(store, task_id)
        raise _error(422, "DOCUMENT_PARSE_FAILED", str(exc)) from exc
    except PipelineValidationError as exc:
        _mark_task_failed(store, task_id)
        raise _error(422, "PIPELINE_VALIDATION_FAILED", str(exc)) from exc
    except Exception as exc:
        try:
            store.update_task(task_id, status="failed")
            manifest = store.read_manifest(task_id) or {"status": "failed", "error": str(exc)}
        except Exception:
            manifest = {"status": "failed", "error": str(exc)}
        raise _error(500, "PIPELINE_FAILED", str(exc)) from exc

    del result
    return {"task_id": task_id, "status": manifest["status"], "manifest": manifest}


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        task = store.get_task(task_id)
        manifest = store.read_manifest(task_id)
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    return {"task_id": task_id, "status": task["status"], "task": task, "manifest": manifest}


@router.get("/tasks/{task_id}/reqir")
def get_reqir(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        return store.read_reqir(task_id)
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


def _mark_task_failed(store: LocalTaskStore, task_id: str) -> None:
    try:
        manifest = store.read_manifest(task_id)
        status = manifest.get("status", "failed") if manifest else "failed"
        store.update_task(task_id, status=status)
    except Exception:
        return


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
