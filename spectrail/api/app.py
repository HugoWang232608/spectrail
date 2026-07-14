from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from spectrail.api.routes import exports, review, sources, tasks
from spectrail.tasks import LocalTaskStore, TaskTransactionInProgressError


def create_app(task_store: LocalTaskStore | None = None) -> FastAPI:
    app = FastAPI(title="SpecTrail API", version="0.1.0")
    app.state.task_store = task_store or LocalTaskStore()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(TaskTransactionInProgressError)
    async def task_transaction_in_progress(
        request: Request,
        exc: TaskTransactionInProgressError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "TASK_MIGRATION_INCOMPLETE",
                    "message": str(exc),
                }
            },
        )

    app.include_router(tasks.router, prefix="/api")
    app.include_router(review.router, prefix="/api")
    app.include_router(exports.router, prefix="/api")
    app.include_router(sources.router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
