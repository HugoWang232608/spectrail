from io import BytesIO
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient
from docx import Document
from docx.oxml import OxmlElement

from spectrail.core.io import read_json, write_json
from spectrail.parsers.docx_parser import DocxParserV2


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


def test_api_table_evidence_returns_block_scoped_logical_grid(
    api_client: TestClient,
):
    task_id, parsed = _create_completed_docx_table_task(api_client)
    assert parsed.evidence_index is not None
    table = parsed.evidence_index.tables[0]
    block = parsed.evidence_index.blocks[0]

    response = api_client.get(
        f"/api/tasks/{task_id}/tables/{table.table_id}"
        f"/blocks/{block.block_id}/evidence"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert payload["schema_version"] == "table_evidence_view_v1"
    assert payload["task_id"] == task_id
    assert payload["evidence_fingerprint"] == (
        parsed.evidence_index.evidence_fingerprint
    )
    assert payload["table_id"] == table.table_id
    assert payload["block_id"] == block.block_id
    assert payload["row_count"] == 2
    assert payload["column_count"] == 3
    assert payload["primary_row_start"] == 1
    assert payload["primary_row_end"] == 2
    assert [row["physical_row_index"] for row in payload["rows"]] == [1, 2]
    header = payload["rows"][0]["cells"][0]
    assert header["cell_id"] == "cell_00000001_r0001_c0001"
    assert header["column_span"] == 2
    assert header["text"] == "Header"
    assert header["occurrences"][0]["occurrence_role"] == "original"


def test_api_table_evidence_preserves_repeated_header_projection(
    api_client: TestClient,
):
    task_id, parsed = _create_completed_docx_table_task(
        api_client,
        large=True,
    )
    assert parsed.evidence_index is not None
    table = parsed.evidence_index.tables[0]
    second_block_id = table.block_ids[1]

    response = api_client.get(
        f"/api/tasks/{task_id}/tables/{table.table_id}"
        f"/blocks/{second_block_id}/evidence"
    )

    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [row["physical_row_index"] for row in rows] == [1, 21, 22]
    assert rows[0]["repeated_header"] is True
    assert rows[0]["cells"][0]["cell_id"] == "cell_00000001_r0001_c0001"
    assert rows[0]["cells"][0]["occurrences"][0]["occurrence_role"] == (
        "repeated_header"
    )
    assert rows[1]["physical_row_index"] == 21


def test_api_table_evidence_rejects_foreign_block(api_client: TestClient):
    task_id, parsed = _create_completed_docx_table_task(api_client)
    assert parsed.evidence_index is not None
    table_id = parsed.evidence_index.tables[0].table_id

    response = api_client.get(
        f"/api/tasks/{task_id}/tables/{table_id}"
        "/blocks/blk_9999/evidence"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TABLE_EVIDENCE_NOT_FOUND"


def test_api_table_evidence_rejects_stale_fingerprint(api_client: TestClient):
    task_id, parsed = _create_completed_docx_table_task(api_client)
    assert parsed.evidence_index is not None
    task_dir = api_client.app.state.task_store.get_task_dir(task_id)
    evidence_path = task_dir / "parsed" / "evidence_index.json"
    payload = read_json(evidence_path)
    payload["warnings"].append("tampered")
    write_json(evidence_path, payload)
    table = parsed.evidence_index.tables[0]

    response = api_client.get(
        f"/api/tasks/{task_id}/tables/{table.table_id}"
        f"/blocks/{table.block_ids[0]}/evidence"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TABLE_EVIDENCE_UNAVAILABLE"


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


def test_api_pdf_page_preview_preserves_render_error_when_close_fails(
    api_client: TestClient,
    monkeypatch,
    caplog,
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
            raise RuntimeError("close failed")

    monkeypatch.setattr(fitz, "open", lambda path: BrokenDocument())
    caplog.set_level("WARNING", logger="spectrail.evidence.pdf_preview")

    response = api_client.get(f"/api/tasks/{task_id}/pages/1/preview.png")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "PAGE_PREVIEW_UNAVAILABLE",
        "message": "failed to render PDF page preview: 1",
    }
    assert "failed to close PDF page preview source 1 after" in caplog.text


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


def _create_completed_docx_table_task(
    api_client: TestClient,
    *,
    large: bool = False,
):
    document = Document()
    if large:
        table = document.add_table(rows=22, cols=2)
        table.cell(0, 0).text = "Key"
        table.cell(0, 1).text = "Requirement"
        _mark_repeating_header(table.rows[0]._tr)
        for row_index in range(1, 22):
            table.cell(row_index, 0).text = f"R{row_index}"
            table.cell(row_index, 1).text = f"Requirement {row_index}"
    else:
        table = document.add_table(rows=2, cols=3)
        table.cell(0, 0).merge(table.cell(0, 1)).text = "Header"
        table.cell(0, 2).text = "Status"
        table.cell(1, 0).text = "REQ-1"
        table.cell(1, 1).text = "The system shall respond."
        table.cell(1, 2).text = "Approved"
    content = BytesIO()
    document.save(content)

    created = api_client.post("/api/tasks", json={})
    task_id = created.json()["task_id"]
    uploaded = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={
            "file": (
                "table.docx",
                content.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert uploaded.status_code == 200

    task_dir = api_client.app.state.task_store.get_task_dir(task_id)
    parsed = DocxParserV2().parse(task_dir / "input" / "original.docx")
    assert parsed.evidence_index is not None
    write_json(
        task_dir / "parsed" / "blocks.json",
        [block.model_dump(mode="json") for block in parsed.blocks],
    )
    write_json(
        task_dir / "parsed" / "evidence_index.json",
        parsed.evidence_index.model_dump(mode="json"),
    )
    api_client.app.state.task_store.update_task(task_id, status="completed")
    return task_id, parsed


def _mark_repeating_header(row) -> None:
    table_header = OxmlElement("w:tblHeader")
    table_header.set(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val",
        "true",
    )
    row.get_or_add_trPr().append(table_header)
