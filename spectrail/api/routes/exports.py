from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from spectrail.api.deps import get_task_store
from spectrail.tasks import (
    LocalTaskStore,
    RunGenerationChangedError,
    TaskNotFoundError,
)
from spectrail.tasks.store import TaskNotReadyError


router = APIRouter(tags=["exports"])


@router.get("/tasks/{task_id}/exports/reqir.json")
def download_reqir(
    task_id: str,
    expected_run_generation: int = Query(ge=0),
    store: LocalTaskStore = Depends(get_task_store),
) -> Response:
    return _download_export(
        task_id,
        "reqir.json",
        "application/json",
        expected_run_generation,
        store,
    )


@router.get("/tasks/{task_id}/exports/requirements.xlsx")
def download_xlsx(
    task_id: str,
    expected_run_generation: int = Query(ge=0),
    store: LocalTaskStore = Depends(get_task_store),
) -> Response:
    return _download_export(
        task_id,
        "requirements.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        expected_run_generation,
        store,
    )


def _download_export(
    task_id: str,
    filename: str,
    media_type: str,
    expected_run_generation: int,
    store: LocalTaskStore,
) -> Response:
    try:
        run_generation, content = store.read_export(
            task_id,
            filename,
            expected_run_generation=expected_run_generation,
        )
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except RunGenerationChangedError as exc:
        raise _error(409, "RUN_GENERATION_CHANGED", str(exc)) from exc
    except FileNotFoundError as exc:
        raise _error(404, "EXPORT_NOT_FOUND", str(exc)) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Spectrail-Run-Generation": str(run_generation),
        },
    )


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
