import json
from pathlib import Path

from fastapi.testclient import TestClient


def _create_and_run_agent_task(api_client: TestClient) -> tuple[str, Path]:
    created = api_client.post(
        "/api/tasks",
        json={
            "model_mode": "mock",
            "orchestration_mode": "agent",
            "planner_mode": "recorded",
            "planner_fixture": "sample_srs_api_agent_full.json",
        },
    )
    task_id = created.json()["task_id"]
    sample = Path("docs/sample_srs.md")
    api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": (sample.name, sample.read_bytes(), "text/markdown")},
    )
    run = api_client.post(f"/api/tasks/{task_id}/run")
    assert run.status_code == 200
    return task_id, api_client.app.state.task_store.get_task_dir(task_id)


def test_agent_trace_api_reads_generation_bound_authoritative_snapshot(
    api_client: TestClient,
):
    task_id, task_dir = _create_and_run_agent_task(api_client)
    trace_path = task_dir / "agent" / "trace.jsonl"
    trace_path.write_text("rebuildable projection is not authoritative\n", encoding="utf-8")

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-spectrail-run-generation"] == "1"
    payload = response.json()
    assert payload["schema_version"] == "agent_trace_snapshot_v1"
    assert payload["run_generation"] == 1
    assert [event["sequence"] for event in payload["events"]] == list(
        range(1, len(payload["events"]) + 1)
    )
    assert payload["attempts"][0]["pipeline_status"] == "completed"
    assert payload["final_state"]["outcome"] == "completed"
    assert trace_path.read_text(encoding="utf-8") == (
        "rebuildable projection is not authoritative\n"
    )


def test_agent_trace_api_rejects_stale_generation(api_client: TestClient):
    task_id, _ = _create_and_run_agent_task(api_client)
    rerun = api_client.post(f"/api/tasks/{task_id}/run")
    assert rerun.status_code == 200

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUN_GENERATION_CHANGED"


def test_agent_trace_api_rejects_corrupt_event_sequence(
    api_client: TestClient,
):
    task_id, task_dir = _create_and_run_agent_task(api_client)
    events = task_dir / "agent" / "events"
    (events / "000002.json").rename(events / "000099.json")

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "AGENT_TRACE_RECOVERY_REQUIRED"
    )


def test_agent_trace_api_rejects_cross_artifact_counter_mismatch(
    api_client: TestClient,
):
    task_id, task_dir = _create_and_run_agent_task(api_client)
    final_state_path = task_dir / "agent" / "final_state.json"
    final_state = json.loads(final_state_path.read_text(encoding="utf-8"))
    final_state["pipeline_attempts"] = 0
    final_state_path.write_text(
        json.dumps(final_state),
        encoding="utf-8",
    )

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "AGENT_TRACE_RECOVERY_REQUIRED"
    )


def test_agent_trace_api_rejects_terminal_outcome_mismatch(
    api_client: TestClient,
):
    task_id, task_dir = _create_and_run_agent_task(api_client)
    event_path = sorted((task_dir / "agent" / "events").glob("*.json"))[-1]
    terminal_event = json.loads(event_path.read_text(encoding="utf-8"))
    terminal_event["payload"]["outcome"] = "failed"
    event_path.write_text(json.dumps(terminal_event), encoding="utf-8")

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "AGENT_TRACE_RECOVERY_REQUIRED"
    )


def test_agent_trace_api_rejects_error_terminal_with_completed_state(
    api_client: TestClient,
):
    task_id, task_dir = _create_and_run_agent_task(api_client)
    event_path = sorted((task_dir / "agent" / "events").glob("*.json"))[-1]
    terminal_event = json.loads(event_path.read_text(encoding="utf-8"))
    terminal_event["event_type"] = "error"
    terminal_event["payload"] = {"error_code": "AGENT_RUNNER_FAILED"}
    event_path.write_text(json.dumps(terminal_event), encoding="utf-8")

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "AGENT_TRACE_RECOVERY_REQUIRED"
    )


def test_agent_trace_api_rejects_final_pipeline_status_mismatch(
    api_client: TestClient,
):
    task_id, task_dir = _create_and_run_agent_task(api_client)
    final_state_path = task_dir / "agent" / "final_state.json"
    final_state = json.loads(final_state_path.read_text(encoding="utf-8"))
    final_state["final_pipeline_status"] = "failed"
    final_state_path.write_text(json.dumps(final_state), encoding="utf-8")

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "AGENT_TRACE_RECOVERY_REQUIRED"
    )


def test_agent_trace_api_rejects_symlinked_agent_root(
    api_client: TestClient,
):
    task_id, task_dir = _create_and_run_agent_task(api_client)
    agent_root = task_dir / "agent"
    real_root = task_dir / "agent-real"
    agent_root.rename(real_root)
    agent_root.symlink_to(real_root.name, target_is_directory=True)

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "AGENT_TRACE_RECOVERY_REQUIRED"
    )


def test_fixed_task_has_no_agent_trace(api_client: TestClient):
    created = api_client.post("/api/tasks", json={"model_mode": "mock"})
    task_id = created.json()["task_id"]
    sample = Path("docs/sample_srs.md")
    api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": (sample.name, sample.read_bytes(), "text/markdown")},
    )
    assert api_client.post(f"/api/tasks/{task_id}/run").status_code == 200

    response = api_client.get(
        f"/api/tasks/{task_id}/agent/trace?expected_run_generation=1"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "AGENT_TRACE_NOT_FOUND"
