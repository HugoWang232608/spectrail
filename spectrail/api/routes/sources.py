from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from spectrail.api.deps import get_task_store
from spectrail.evidence import TableEvidenceView
from spectrail.tasks import LocalTaskStore, TaskNotFoundError
from spectrail.tasks.store import (
    BlocksNotFoundError,
    PagePreviewNotFoundError,
    PagePreviewUnavailableError,
    TableEvidenceNotFoundError,
    TableEvidenceUnavailableError,
    TaskNotReadyError,
)


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


@router.get(
    "/tasks/{task_id}/tables/{table_id}/blocks/{block_id}/evidence",
    response_model=TableEvidenceView,
)
def get_table_evidence(
    task_id: str,
    table_id: str,
    block_id: str,
    response: Response,
    store: LocalTaskStore = Depends(get_task_store),
) -> TableEvidenceView:
    try:
        table_evidence = store.read_table_evidence(
            task_id,
            table_id=table_id,
            block_id=block_id,
        )
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except TableEvidenceNotFoundError as exc:
        raise _error(404, "TABLE_EVIDENCE_NOT_FOUND", str(exc)) from exc
    except TableEvidenceUnavailableError as exc:
        raise _error(409, "TABLE_EVIDENCE_UNAVAILABLE", str(exc)) from exc

    response.headers["Cache-Control"] = "private, no-store"
    return table_evidence


@router.get("/tasks/{task_id}/pages/{page_number}/preview.png")
def get_page_preview(
    task_id: str,
    page_number: int,
    store: LocalTaskStore = Depends(get_task_store),
) -> Response:
    try:
        content, width, height = store.render_pdf_page_preview(
            task_id,
            page_number,
        )
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except PagePreviewNotFoundError as exc:
        raise _error(404, "PAGE_PREVIEW_NOT_FOUND", str(exc)) from exc
    except PagePreviewUnavailableError as exc:
        raise _error(409, "PAGE_PREVIEW_UNAVAILABLE", str(exc)) from exc

    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Spectrail-Preview-Width": str(width),
            "X-Spectrail-Preview-Height": str(height),
        },
    )


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
