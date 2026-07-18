from pathlib import Path

from fastapi.testclient import TestClient


def test_api_run_recorded_task_completed(api_client: TestClient):
    created = api_client.post("/api/tasks", json={"model_mode": "recorded"})
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
    assert run.json()["manifest"]["counts"]["validated_requirements"] == 2

    reqir = api_client.get(
        f"/api/tasks/{task_id}/reqir?expected_run_generation=1"
    )
    assert reqir.status_code == 200
    assert len(reqir.json()["items"]) == 2
