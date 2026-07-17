from types import SimpleNamespace

import fitz
import pytest
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


def test_api_pdf_page_preview_returns_bounded_png(api_client: TestClient):
    task_id = _create_completed_pdf_task(
        api_client,
        width=4000,
        height=1000,
    )

    response = api_client.get(f"/api/tasks/{task_id}/pages/1/preview.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.headers["x-spectrail-preview-width"] == "2000"
    assert response.headers["x-spectrail-preview-height"] == "500"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_api_pdf_page_preview_rejects_missing_page(api_client: TestClient):
    task_id = _create_completed_pdf_task(api_client)

    response = api_client.get(f"/api/tasks/{task_id}/pages/2/preview.png")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PAGE_PREVIEW_NOT_FOUND"


def test_api_pdf_page_preview_preserves_not_found_when_close_fails(
    api_client: TestClient,
    monkeypatch,
):
    task_id = _create_completed_pdf_task(api_client)

    class MissingPageDocument:
        page_count = 0

        def close(self):
            raise RuntimeError("close failed")

    monkeypatch.setattr(fitz, "open", lambda path: MissingPageDocument())

    response = api_client.get(f"/api/tasks/{task_id}/pages/1/preview.png")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "PAGE_PREVIEW_NOT_FOUND",
        "message": "PDF page does not exist: 1",
    }


@pytest.mark.parametrize(
    ("rotation", "expected_size"),
    [
        (0, ("800", "600")),
        (90, ("600", "800")),
        (180, ("800", "600")),
        (270, ("600", "800")),
    ],
)
def test_api_pdf_page_preview_uses_rotated_page_dimensions(
    api_client: TestClient,
    rotation: int,
    expected_size: tuple[str, str],
):
    task_id = _create_completed_pdf_task(
        api_client,
        width=400,
        height=300,
        rotation=rotation,
    )

    response = api_client.get(f"/api/tasks/{task_id}/pages/1/preview.png")

    assert response.status_code == 200
    assert response.headers["x-spectrail-preview-width"] == expected_size[0]
    assert response.headers["x-spectrail-preview-height"] == expected_size[1]


def test_api_pdf_page_preview_maps_render_failure_to_structured_error(
    api_client: TestClient,
    monkeypatch,
):
    task_id = _create_completed_pdf_task(api_client)

    class BrokenPage:
        rect = SimpleNamespace(width=400, height=300)

        def get_pixmap(self, **kwargs):
            del kwargs
            raise RuntimeError("broken page resource")

    class BrokenDocument:
        page_count = 1

        def __getitem__(self, index):
            assert index == 0
            return BrokenPage()

        def close(self):
            return None

    monkeypatch.setattr(fitz, "open", lambda path: BrokenDocument())

    response = api_client.get(f"/api/tasks/{task_id}/pages/1/preview.png")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "PAGE_PREVIEW_UNAVAILABLE",
        "message": "failed to render PDF page preview: 1",
    }


def test_api_page_preview_rejects_non_pdf_task(
    api_client: TestClient,
    completed_api_task: dict,
):
    task_id = completed_api_task["task_id"]

    response = api_client.get(f"/api/tasks/{task_id}/pages/1/preview.png")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PAGE_PREVIEW_UNAVAILABLE"


def test_api_page_preview_before_completion_returns_409(api_client: TestClient):
    created = api_client.post("/api/tasks", json={})
    task_id = created.json()["task_id"]

    response = api_client.get(f"/api/tasks/{task_id}/pages/1/preview.png")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_NOT_COMPLETED"


def _create_completed_pdf_task(
    api_client: TestClient,
    *,
    width: float = 400,
    height: float = 300,
    rotation: int = 0,
) -> str:
    created = api_client.post("/api/tasks", json={})
    assert created.status_code == 200
    task_id = created.json()["task_id"]

    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.insert_text((30, 40), "Preview evidence")
    page.set_rotation(rotation)
    content = document.tobytes()
    document.close()
    uploaded = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": ("preview.pdf", content, "application/pdf")},
    )
    assert uploaded.status_code == 200
    api_client.app.state.task_store.update_task(task_id, status="completed")
    return task_id
