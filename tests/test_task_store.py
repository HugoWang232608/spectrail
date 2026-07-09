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
    assert document.name == "original.md"
    assert document.read_text(encoding="utf-8") == "# SRS\n"

    updated = store.get_task(task["task_id"])
    assert updated["status"] == "uploaded"
    assert updated["input_document"] == "input/original.md"
    assert updated["original_filename"] == "sample.markdown"
    assert store.get_input_document(task["task_id"]) == document


def test_task_store_rejects_non_markdown_upload(tmp_path: Path):
    store = LocalTaskStore(tmp_path / "tasks")
    task = store.create_task()

    with pytest.raises(InvalidDocumentError):
        store.save_document(task["task_id"], "sample.pdf", b"%PDF")


def test_task_store_missing_task_and_unready_state(tmp_path: Path):
    store = LocalTaskStore(tmp_path / "tasks")

    with pytest.raises(TaskNotFoundError):
        store.get_task("task_missing")

    task = store.create_task()
    assert store.read_manifest(task["task_id"]) is None
    with pytest.raises(TaskNotReadyError):
        store.get_input_document(task["task_id"])
