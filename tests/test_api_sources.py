from fastapi.testclient import TestClient

from spectrail.core.io import read_json, write_json


def test_api_blocks_returns_completed_task_blocks(api_client: TestClient, completed_api_task: dict):
    task_id = completed_api_task["task_id"]

    response = api_client.get(f"/api/tasks/{task_id}/blocks")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert len(payload["items"]) > 0
    first = payload["items"][0]
    assert {"block_id", "document_id", "type", "text", "section_path", "order", "metadata"} <= set(first)
    assert "order_index" not in first


def test_api_blocks_before_completion_returns_409(api_client: TestClient):
    created = api_client.post("/api/tasks", json={})
    task_id = created.json()["task_id"]

    response = api_client.get(f"/api/tasks/{task_id}/blocks")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_NOT_COMPLETED"


def test_api_blocks_missing_task_returns_404(api_client: TestClient):
    response = api_client.get("/api/tasks/task_missing/blocks")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TASK_NOT_FOUND"


def test_api_blocks_missing_blocks_file_returns_404(api_client: TestClient, completed_api_task: dict):
    task_id = completed_api_task["task_id"]
    task_dir = api_client.app.state.task_store.get_task_dir(task_id)
    (task_dir / "parsed" / "blocks.json").unlink()

    response = api_client.get(f"/api/tasks/{task_id}/blocks")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "BLOCKS_NOT_FOUND"


def test_api_blocks_converts_order_index_to_order(api_client: TestClient, completed_api_task: dict):
    task_id = completed_api_task["task_id"]
    task_dir = api_client.app.state.task_store.get_task_dir(task_id)
    blocks_path = task_dir / "parsed" / "blocks.json"
    blocks = read_json(blocks_path)
    blocks[0]["order_index"] = blocks[0].pop("order")
    write_json(blocks_path, blocks)

    response = api_client.get(f"/api/tasks/{task_id}/blocks")

    assert response.status_code == 200
    first = response.json()["items"][0]
    assert first["order"] == blocks[0]["order_index"]
    assert "order_index" not in first
