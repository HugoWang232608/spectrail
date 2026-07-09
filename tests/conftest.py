from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spectrail.api.app import create_app
from spectrail.tasks import LocalTaskStore


@pytest.fixture
def api_client(tmp_path: Path) -> TestClient:
    app = create_app(task_store=LocalTaskStore(tmp_path / "tasks"))
    return TestClient(app)


@pytest.fixture
def completed_api_task(api_client: TestClient) -> dict:
    created = api_client.post("/api/tasks", json={"goal": "extract_requirements", "model_mode": "mock"})
    assert created.status_code == 200
    task_id = created.json()["task_id"]

    sample = Path("docs/sample_srs.md")
    uploaded = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": (sample.name, sample.read_bytes(), "text/markdown")},
    )
    assert uploaded.status_code == 200

    run = api_client.post(f"/api/tasks/{task_id}/run")
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    return {"task_id": task_id, "run": run.json()}
