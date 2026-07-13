from pathlib import Path

import pytest

from spectrail.tasks import LocalTaskStore, TaskNotFoundError
from spectrail.tasks.store import InvalidDocumentError, TaskNotReadyError


def test_task_store_creates_task_and_saves_document(tmp_path: Path):
    store = LocalTaskStore(tmp_path / "tasks")
    task = store.create_task()

    assert task["task_id"].startswith("task_")
    assert Path(task["output_dir"]).exists()
    assert store.get_task(task["task_id"])["status"] == "created"

    document = store.save_document(task["task_id"], "sample.markdown", b"# SRS\n")
    assert document.name == "original.markdown"
    assert document.read_text(encoding="utf-8") == "# SRS\n"

    updated = store.get_task(task["task_id"])
    assert updated["status"] == "uploaded"
    assert updated["input_document"] == "input/original.markdown"
    assert updated["original_filename"] == "sample.markdown"
    assert updated["input_format"] == "markdown"
    assert store.get_input_document(task["task_id"]) == document


def test_task_store_saves_supported_document_suffixes(tmp_path: Path):
    store = LocalTaskStore(tmp_path / "tasks")
    task = store.create_task()

    docx = store.save_document(task["task_id"], "sample.docx", b"docx bytes")
    assert docx.name == "original.docx"
    assert store.get_task(task["task_id"])["input_format"] == "docx"

    pdf = store.save_document(task["task_id"], "sample.pdf", b"pdf bytes")
    assert pdf.name == "original.pdf"
    assert store.get_task(task["task_id"])["input_document"] == "input/original.pdf"
    assert store.get_task(task["task_id"])["input_format"] == "pdf"


def test_task_store_rejects_unsupported_upload(tmp_path: Path):
    store = LocalTaskStore(tmp_path / "tasks")
    task = store.create_task()

    with pytest.raises(InvalidDocumentError):
        store.save_document(task["task_id"], "sample.rtf", b"{\\rtf1}")


def test_task_store_missing_task_and_unready_state(tmp_path: Path):
    store = LocalTaskStore(tmp_path / "tasks")

    with pytest.raises(TaskNotFoundError):
        store.get_task("task_missing")

    task = store.create_task()
    assert store.read_manifest(task["task_id"]) is None
    with pytest.raises(TaskNotReadyError):
        store.get_input_document(task["task_id"])


def test_task_store_completed_with_warnings_is_readable(tmp_path: Path):
    store = LocalTaskStore(tmp_path / "tasks")
    task = store.create_task()
    task_dir = store.get_task_dir(task["task_id"])
    (task_dir / "exports").mkdir()
    (task_dir / "exports" / "reqir.json").write_text(
        '{"metadata": {}, "items": []}', encoding="utf-8"
    )
    store.update_task(task["task_id"], status="completed_with_warnings")

    assert store.read_reqir(task["task_id"])["items"] == []
