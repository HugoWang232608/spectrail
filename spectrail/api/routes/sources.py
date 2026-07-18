from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from spectrail.api.deps import get_task_store
from spectrail.evidence import TableEvidenceView
from spectrail.tasks import (
    LocalTaskStore,
    RunGenerationChangedError,
    TaskNotFoundError,
)
from spectrail.tasks.store import (
    BlocksNotFoundError,
    BlocksUnavailableError,
    EvidenceVersionChangedError,
    LegacyEvidenceContinuationRebuildRequiredError,
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
    response: Response,
    expected_evidence_fingerprint: str = Query(
        pattern=r"^[0-9a-f]{64}$",
    ),
    expected_run_generation: int = Query(ge=0),
    store: LocalTaskStore = Depends(get_task_store),
) -> dict:
    try:
        run_generation, evidence_fingerprint, blocks = store.read_blocks(
            task_id,
            expected_evidence_fingerprint=expected_evidence_fingerprint,
            expected_run_generation=expected_run_generation,
        )
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except BlocksNotFoundError as exc:
        raise _error(404, "BLOCKS_NOT_FOUND", str(exc)) from exc
    except BlocksUnavailableError as exc:
        raise _error(409, "BLOCKS_UNAVAILABLE", str(exc)) from exc
    except LegacyEvidenceContinuationRebuildRequiredError as exc:
        raise _error(
            409,
            "EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED",
            str(exc),
        ) from exc
    except EvidenceVersionChangedError as exc:
        raise _error(409, "EVIDENCE_VERSION_CHANGED", str(exc)) from exc
    except RunGenerationChangedError as exc:
        raise _error(409, "RUN_GENERATION_CHANGED", str(exc)) from exc

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Spectrail-Run-Generation"] = str(run_generation)
    return {
        "task_id": task_id,
        "run_generation": run_generation,
        "evidence_fingerprint": evidence_fingerprint,
        "items": blocks,
    }


@router.get(
    "/tasks/{task_id}/tables/{table_id}/blocks/{block_id}/evidence",
    response_model=TableEvidenceView,
)
def get_table_evidence(
    task_id: str,
    table_id: str,
    block_id: str,
    response: Response,
    expected_evidence_fingerprint: str = Query(
        pattern=r"^[0-9a-f]{64}$",
    ),
    expected_run_generation: int = Query(ge=0),
    store: LocalTaskStore = Depends(get_task_store),
) -> TableEvidenceView:
    try:
        run_generation, table_evidence = store.read_table_evidence(
            task_id,
            table_id=table_id,
            block_id=block_id,
            expected_evidence_fingerprint=expected_evidence_fingerprint,
            expected_run_generation=expected_run_generation,
        )
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except TableEvidenceNotFoundError as exc:
        raise _error(404, "TABLE_EVIDENCE_NOT_FOUND", str(exc)) from exc
    except TableEvidenceUnavailableError as exc:
        raise _error(409, "TABLE_EVIDENCE_UNAVAILABLE", str(exc)) from exc
    except LegacyEvidenceContinuationRebuildRequiredError as exc:
        raise _error(
            409,
            "EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED",
            str(exc),
        ) from exc
    except EvidenceVersionChangedError as exc:
        raise _error(409, "EVIDENCE_VERSION_CHANGED", str(exc)) from exc
    except RunGenerationChangedError as exc:
        raise _error(409, "RUN_GENERATION_CHANGED", str(exc)) from exc

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Spectrail-Run-Generation"] = str(run_generation)
    return table_evidence


@router.get("/tasks/{task_id}/pages/{page_number}/preview.png")
def get_page_preview(
    task_id: str,
    page_number: int,
    expected_evidence_fingerprint: str = Query(
        pattern=r"^[0-9a-f]{64}$",
    ),
    expected_run_generation: int = Query(ge=0),
    store: LocalTaskStore = Depends(get_task_store),
) -> Response:
    try:
        content, width, height, evidence_fingerprint, run_generation = (
            store.render_pdf_page_preview(
                task_id,
                page_number,
                expected_evidence_fingerprint=expected_evidence_fingerprint,
                expected_run_generation=expected_run_generation,
            )
        )
    except TaskNotFoundError as exc:
        raise _error(404, "TASK_NOT_FOUND", str(exc)) from exc
    except TaskNotReadyError as exc:
        raise _error(409, "TASK_NOT_COMPLETED", str(exc)) from exc
    except PagePreviewNotFoundError as exc:
        raise _error(404, "PAGE_PREVIEW_NOT_FOUND", str(exc)) from exc
    except PagePreviewUnavailableError as exc:
        raise _error(409, "PAGE_PREVIEW_UNAVAILABLE", str(exc)) from exc
    except LegacyEvidenceContinuationRebuildRequiredError as exc:
        raise _error(
            409,
            "EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED",
            str(exc),
        ) from exc
    except EvidenceVersionChangedError as exc:
        raise _error(409, "EVIDENCE_VERSION_CHANGED", str(exc)) from exc
    except RunGenerationChangedError as exc:
        raise _error(409, "RUN_GENERATION_CHANGED", str(exc)) from exc

    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Spectrail-Preview-Width": str(width),
            "X-Spectrail-Preview-Height": str(height),
            "X-Spectrail-Evidence-Fingerprint": evidence_fingerprint,
            "X-Spectrail-Run-Generation": str(run_generation),
        },
    )


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
