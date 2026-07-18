from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from spectrail.api.deps import get_task_store
from spectrail.api.schemas import ReviewRequest, ReviewResponse
from spectrail.api.transaction_errors import task_transaction_http_error
from spectrail.review.service import apply_review_to_package
from spectrail.task_transactions import TaskTransactionError, task_operation
from spectrail.tasks import (
    LocalTaskStore,
    RunGenerationChangedError,
    TaskNotFoundError,
)
from spectrail.tasks.store import TaskNotReadyError


router = APIRouter(tags=["review"])


@router.post("/tasks/{task_id}/review", response_model=ReviewResponse)
def review_requirement(
    task_id: str,
    request: ReviewRequest,
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        task_dir = store.get_task_dir(task_id)
        with task_operation(task_dir, "api_review_apply"):
            run_generation = store.require_readable_generation(
                task_id,
                expected_run_generation=request.expected_run_generation,
            )
            updated = apply_review_to_package(
                reqir_path=task_dir / "exports" / "reqir.json",
                review_log_path=task_dir / "review" / "review_log.json",
                xlsx_path=task_dir / "exports" / "requirements.xlsx",
                requirement_id=request.requirement_id,
                action=request.action,
                patch=request.patch,
                reviewer=request.reviewer,
                reason=request.reason,
            )
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except RunGenerationChangedError as exc:
        raise _error(409, "RUN_GENERATION_CHANGED", str(exc)) from exc
    except FileNotFoundError as exc:
        raise _error(404, "EXPORT_NOT_FOUND", str(exc)) from exc
    except TaskTransactionError as exc:
        raise task_transaction_http_error(exc) from exc
    except ValueError as exc:
        message = str(exc)
        code = "REQUIREMENT_NOT_FOUND" if message.startswith("requirement not found") else "INVALID_REVIEW_ACTION"
        status_code = 404 if code == "REQUIREMENT_NOT_FOUND" else 400
        raise _error(status_code, code, message) from exc

    return {
        "task_id": task_id,
        "run_generation": run_generation,
        "requirement_id": updated.id,
        "action": request.action,
        "review_status": updated.review_status,
    }


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
