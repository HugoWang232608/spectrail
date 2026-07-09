from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook


def test_api_exports_download_files(api_client: TestClient, completed_api_task: dict):
    task_id = completed_api_task["task_id"]

    reqir = api_client.get(f"/api/tasks/{task_id}/exports/reqir.json")
    assert reqir.status_code == 200
    assert reqir.headers["content-type"].startswith("application/json")
    assert len(reqir.json()["items"]) >= 14

    xlsx = api_client.get(f"/api/tasks/{task_id}/exports/requirements.xlsx")
    assert xlsx.status_code == 200
    workbook = load_workbook(BytesIO(xlsx.content))
    assert workbook["Requirements"].max_row >= 15


def test_api_export_before_completion_returns_409(api_client: TestClient):
    created = api_client.post("/api/tasks", json={})
    task_id = created.json()["task_id"]

    response = api_client.get(f"/api/tasks/{task_id}/exports/requirements.xlsx")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_NOT_COMPLETED"
