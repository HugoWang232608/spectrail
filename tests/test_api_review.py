import pytest
from fastapi.testclient import TestClient

import spectrail.review.service as review_service
from spectrail.core.io import read_json


def test_api_review_actions_refresh_outputs(api_client: TestClient, completed_api_task: dict):
    task_id = completed_api_task["task_id"]

    approved = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={
            "requirement_id": "REQ-0001",
            "expected_run_generation": 1,
            "action": "approve",
            "reviewer": "local",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["run_generation"] == 1
    assert approved.json()["review_status"] == "approved"

    edited_tags = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={
            "requirement_id": "REQ-0001",
            "expected_run_generation": 1,
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
            "expected_run_generation": 1,
            "action": "edit",
            "patch": {"statement": "系统应记录完整的用户账号状态变更审计信息。"},
            "reviewer": "local",
        },
    )
    assert edited_statement.status_code == 200
    assert edited_statement.json()["review_status"] == "needs_recheck"

    rejected = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={
            "requirement_id": "REQ-0002",
            "expected_run_generation": 1,
            "action": "reject",
            "reviewer": "local",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["review_status"] == "rejected"

    invalid_approve = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={
            "requirement_id": "REQ-0002",
            "expected_run_generation": 1,
            "action": "approve",
            "reviewer": "local",
        },
    )
    assert invalid_approve.status_code == 400
    assert invalid_approve.json()["detail"]["code"] == "INVALID_REVIEW_ACTION"

    restored = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={
            "requirement_id": "REQ-0002",
            "expected_run_generation": 1,
            "action": "restore",
            "reviewer": "local",
        },
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
        json={
            "requirement_id": "REQ-0001",
            "expected_run_generation": 1,
            "action": "edit",
            "patch": {"sources": []},
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REVIEW_ACTION"


def test_api_review_rejects_stale_generation_without_writing_artifacts(
    api_client: TestClient,
    completed_api_task: dict,
):
    task_id = completed_api_task["task_id"]
    store = api_client.app.state.task_store
    task_dir = store.get_task_dir(task_id)
    store.begin_run(task_id)
    store.update_task(task_id, status="completed")
    artifact_paths = [
        task_dir / "exports" / "reqir.json",
        task_dir / "exports" / "requirements.xlsx",
        task_dir / "review" / "review_log.json",
    ]
    before = {
        path: path.read_bytes() if path.exists() else None
        for path in artifact_paths
    }

    response = api_client.post(
        f"/api/tasks/{task_id}/review",
        json={
            "requirement_id": "REQ-0001",
            "expected_run_generation": 1,
            "action": "approve",
            "reviewer": "stale-client",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUN_GENERATION_CHANGED"
    assert {
        path: path.read_bytes() if path.exists() else None
        for path in artifact_paths
    } == before


def test_api_review_xlsx_failure_leaves_all_artifacts_unchanged(
    api_client: TestClient,
    completed_api_task: dict,
    monkeypatch,
):
    task_id = completed_api_task["task_id"]
    task_dir = api_client.app.state.task_store.get_task_dir(task_id)
    artifact_paths = [
        task_dir / "exports" / "reqir.json",
        task_dir / "exports" / "requirements.xlsx",
        task_dir / "review" / "review_log.json",
    ]
    before = {
        path: path.read_bytes() if path.exists() else None
        for path in artifact_paths
    }

    def fail_xlsx_export(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("injected XLSX generation failure")

    monkeypatch.setattr(
        review_service,
        "export_requirements_xlsx",
        fail_xlsx_export,
    )

    with pytest.raises(
        RuntimeError,
        match="injected XLSX generation failure",
    ):
        api_client.post(
            f"/api/tasks/{task_id}/review",
            json={
                "requirement_id": "REQ-0001",
                "expected_run_generation": 1,
                "action": "approve",
                "reviewer": "failure-injection",
            },
        )

    assert {
        path: path.read_bytes() if path.exists() else None
        for path in artifact_paths
    } == before
    assert not list(task_dir.glob(".review_prepare_*"))


def test_api_review_publication_failure_rolls_back_all_artifacts(
    api_client: TestClient,
    completed_api_task: dict,
    monkeypatch,
):
    task_id = completed_api_task["task_id"]
    task_dir = api_client.app.state.task_store.get_task_dir(task_id)
    artifact_paths = [
        task_dir / "exports" / "reqir.json",
        task_dir / "exports" / "requirements.xlsx",
        task_dir / "review" / "review_log.json",
    ]
    before = {
        path: path.read_bytes() if path.exists() else None
        for path in artifact_paths
    }
    real_replace = review_service.os.replace
    failure_injected = False

    def fail_second_publication(source, target):
        nonlocal failure_injected
        source_path = review_service.Path(source)
        if (
            not failure_injected
            and source_path.name == "reqir.json"
            and source_path.parent.name.startswith(".review_prepare_")
        ):
            failure_injected = True
            raise OSError("injected review publication failure")
        return real_replace(source, target)

    monkeypatch.setattr(review_service.os, "replace", fail_second_publication)

    with pytest.raises(
        OSError,
        match="injected review publication failure",
    ):
        api_client.post(
            f"/api/tasks/{task_id}/review",
            json={
                "requirement_id": "REQ-0001",
                "expected_run_generation": 1,
                "action": "approve",
                "reviewer": "failure-injection",
            },
        )

    assert failure_injected is True
    assert {
        path: path.read_bytes() if path.exists() else None
        for path in artifact_paths
    } == before
    assert not list(task_dir.glob(".review_prepare_*"))
