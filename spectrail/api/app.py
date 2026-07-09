from __future__ import annotations

from fastapi import FastAPI

from spectrail.api.routes import exports, review, tasks
from spectrail.tasks import LocalTaskStore


def create_app(task_store: LocalTaskStore | None = None) -> FastAPI:
    app = FastAPI(title="SpecTrail API", version="0.1.0")
    app.state.task_store = task_store or LocalTaskStore()

    app.include_router(tasks.router, prefix="/api")
    app.include_router(review.router, prefix="/api")
    app.include_router(exports.router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
