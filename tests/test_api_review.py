from fastapi.testclient import TestClient

from spectrail.core.io import read_json


def test_api_review_actions_refresh_outputs(api_client: TestClient, completed_api_task: dict):
    task_id = completed_api_task["task_id"]

    approved = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={"requirement_id": "REQ-0001", "action": "approve", "reviewer": "local"},
    )
    assert approved.status_code == 200
    assert approved.json()["review_status"] == "approved"

    edited_tags = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={
            "requirement_id": "REQ-0001",
            "action": "edit",
            "patch": {"tags": ["user-management", "reviewed"]},
            "reviewer": "local",
        },
    )
    assert edited_tags.status_code == 200
    assert edited_tags.json()["review_status"] == "approved"

    edited_statement = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={
            "requirement_id": "REQ-0003",
            "action": "edit",
            "patch": {"statement": "系统应记录完整的用户账号状态变更审计信息。"},
            "reviewer": "local",
        },
    )
    assert edited_statement.status_code == 200
    assert edited_statement.json()["review_status"] == "needs_recheck"

    rejected = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={"requirement_id": "REQ-0002", "action": "reject", "reviewer": "local"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["review_status"] == "rejected"

    invalid_approve = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={"requirement_id": "REQ-0002", "action": "approve", "reviewer": "local"},
    )
    assert invalid_approve.status_code == 400
    assert invalid_approve.json()["detail"]["code"] == "INVALID_REVIEW_ACTION"

    restored = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={"requirement_id": "REQ-0002", "action": "restore", "reviewer": "local"},
    )
    assert restored.status_code == 200
    assert restored.json()["review_status"] == "pending"

    reqir_path = read_json(completed_api_task["run"]["manifest"]["output_dir"] + "/exports/reqir.json")
    req_0001 = next(item for item in reqir_path["items"] if item["id"] == "REQ-0001")
    assert req_0001["tags"] == ["user-management", "reviewed"]


def test_api_review_rejects_unsupported_patch(api_client: TestClient, completed_api_task: dict):
    task_id = completed_api_task["task_id"]

    response = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={"requirement_id": "REQ-0001", "action": "edit", "patch": {"sources": []}},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REVIEW_ACTION"
