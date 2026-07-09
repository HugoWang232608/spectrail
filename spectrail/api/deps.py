from __future__ import annotations

from fastapi import Request

from spectrail.tasks import LocalTaskStore


def get_task_store(request: Request) -> LocalTaskStore:
    return request.app.state.task_store
