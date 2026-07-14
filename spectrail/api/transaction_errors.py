from __future__ import annotations

from fastapi import HTTPException

from spectrail.task_transactions import TaskTransactionError
from spectrail.tasks import TaskTransactionInProgressError


TaskTransactionException = TaskTransactionError | TaskTransactionInProgressError


def task_transaction_error_detail(
    error: TaskTransactionException,
) -> dict[str, object]:
    return {
        "code": error.code,
        "message": str(error),
        "retryable": error.code == "TASK_TRANSACTION_LOCKED",
    }


def task_transaction_http_error(
    error: TaskTransactionException,
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=task_transaction_error_detail(error),
    )
