from pathlib import Path

from fastapi.testclient import TestClient


def test_api_task_flow(api_client: TestClient):
    assert api_client.get("/api/health").json() == {"status": "ok"}

    created = api_client.post("/api/tasks", json={"goal": "extract_requirements", "model_mode": "mock"})
    assert created.status_code == 200
    task = created.json()
    task_id = task["task_id"]
    assert task["status"] == "created"

    sample = Path("docs/sample_srs.md")
    uploaded = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": (sample.name, sample.read_bytes(), "text/markdown")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "uploaded"
    assert uploaded.json()["filename"] == sample.name

    run = api_client.post(f"/api/tasks/{task_id}/run")
    assert run.status_code == 200
    payload = run.json()
    assert payload["status"] == "completed"
    assert payload["manifest"]["counts"]["validated_requirements"] >= 14

    status = api_client.get(f"/api/tasks/{task_id}")
    assert status.status_code == 200
    assert status.json()["manifest"]["status"] == "completed"

    reqir = api_client.get(f"/api/tasks/{task_id}/reqir")
    assert reqir.status_code == 200
    assert len(reqir.json()["items"]) >= 14


def test_api_task_requires_uploaded_document(api_client: TestClient):
    created = api_client.post("/api/tasks", json={})
    task_id = created.json()["task_id"]

    run = api_client.post(f"/api/tasks/{task_id}/run")
    assert run.status_code == 409
    assert run.json()["detail"]["code"] == "DOCUMENT_NOT_UPLOADED"


def test_api_upload_accepts_docx_and_pdf(api_client: TestClient):
    created = api_client.post("/api/tasks", json={})
    task_id = created.json()["task_id"]

    uploaded_docx = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={
            "file": (
                "sample.docx",
                b"docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert uploaded_docx.status_code == 200
    assert uploaded_docx.json()["filename"] == "sample.docx"

    uploaded_pdf = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": ("sample.pdf", b"%PDF", "application/pdf")},
    )
    assert uploaded_pdf.status_code == 200
    assert uploaded_pdf.json()["filename"] == "sample.pdf"


def test_api_upload_rejects_unsupported_extension(api_client: TestClient):
    created = api_client.post("/api/tasks", json={})
    task_id = created.json()["task_id"]

    uploaded = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": ("sample.rtf", b"{\\rtf1}", "application/rtf")},
    )
    assert uploaded.status_code == 400
    assert uploaded.json()["detail"]["code"] == "INVALID_DOCUMENT"


def test_api_run_marks_task_failed_when_model_mode_is_rejected(api_client: TestClient):
    created = api_client.post("/api/tasks", json={})
    task_id = created.json()["task_id"]

    sample = Path("docs/sample_srs.md")
    uploaded = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": (sample.name, sample.read_bytes(), "text/markdown")},
    )
    assert uploaded.status_code == 200

    api_client.app.state.task_store.update_task(task_id, model_mode="recorded")

    run = api_client.post(f"/api/tasks/{task_id}/run")
    assert run.status_code == 400
    assert run.json()["detail"]["code"] == "INVALID_MODEL_MODE"

    status = api_client.get(f"/api/tasks/{task_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "failed"
