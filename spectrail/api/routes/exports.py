from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from spectrail.api.deps import get_task_store
from spectrail.tasks import LocalTaskStore, TaskNotFoundError


router = APIRouter(tags=["exports"])


@router.get("/tasks/{task_id}/exports/reqir.json")
def download_reqir(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> FileResponse:
    return _download_export(task_id, "reqir.json", "application/json", store)


@router.get("/tasks/{task_id}/exports/requirements.xlsx")
def download_xlsx(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> FileResponse:
    return _download_export(
        task_id,
        "requirements.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        store,
    )


def _download_export(
    task_id: str,
    filename: str,
    media_type: str,
    store: LocalTaskStore,
) -> FileResponse:
    try:
        path = store.get_export_path(task_id, filename)
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except FileNotFoundError as exc:
        raise _error(404, "EXPORT_NOT_FOUND", str(exc)) from exc
    if not path.exists():
        raise _error(404, "EXPORT_NOT_FOUND", f"export not found: {filename}")
    return FileResponse(path, media_type=media_type, filename=filename)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
