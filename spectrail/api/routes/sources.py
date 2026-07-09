from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from spectrail.api.deps import get_task_store
from spectrail.tasks import LocalTaskStore, TaskNotFoundError
from spectrail.tasks.store import BlocksNotFoundError, TaskNotReadyError


router = APIRouter(tags=["sources"])


@router.get("/tasks/{task_id}/blocks")
def get_blocks(
    task_id: str,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        blocks = store.read_blocks(task_id)
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except BlocksNotFoundError as exc:
        raise _error(404, "BLOCKS_NOT_FOUND", str(exc)) from exc

    return {"task_id": task_id, "items": blocks}


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
