from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spectrail.core.io import ensure_dir, read_json, write_json
from spectrail.parsers import SUPPORTED_DOCUMENT_SUFFIXES
from spectrail.task_transactions import TaskTransactionError, task_operation
from spectrail.tasks.ids import new_task_id


class TaskStoreError(Exception):
    pass


class TaskNotFoundError(TaskStoreError):
    pass


class InvalidDocumentError(TaskStoreError):
    pass


class TaskNotReadyError(TaskStoreError):
    pass


class TaskTransactionInProgressError(TaskStoreError):
    def __init__(self, cause: TaskTransactionError) -> None:
        self.cause = cause
        self.code = cause.code
        self.message = cause.message
        self.retryable = cause.retryable
        super().__init__(str(cause))


class BlocksNotFoundError(TaskStoreError):
    pass


READABLE_TASK_STATUSES = {"completed", "completed_with_warnings"}


class LocalTaskStore:
    def __init__(self, root: str | Path = "outputs/tasks") -> None:
        self.root = Path(root)

    def create_task(
        self,
        goal: str = "extract_requirements",
        model_mode: str = "mock",
        pipeline_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ensure_dir(self.root)
        task_id = new_task_id()
        task_dir = self.root / task_id
        while task_dir.exists():
            task_id = new_task_id()
            task_dir = self.root / task_id

        ensure_dir(task_dir / "input")
        now = _now_iso()
        task = {
            "task_id": task_id,
            "goal": goal,
            "model_mode": model_mode,
            "pipeline_config": pipeline_config or {},
            "status": "created",
            "created_at": now,
            "updated_at": now,
            "input_document": None,
            "original_filename": None,
            "output_dir": task_dir.as_posix(),
        }
        write_json(task_dir / "task.json", task)
        return task

    def get_task_dir(self, task_id: str) -> Path:
        task_dir = self.root / task_id
        if not (task_dir / "task.json").exists():
            raise TaskNotFoundError(f"task not found: {task_id}")
        return task_dir

    def get_task(self, task_id: str) -> dict[str, Any]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "task_read"):
                return read_json(task_dir / "task.json")
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def read_status_snapshot(
        self,
        task_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "task_status_snapshot"):
                task = read_json(task_dir / "task.json")
                manifest_path = task_dir / "run_manifest.json"
                manifest = read_json(manifest_path) if manifest_path.exists() else None
                return task, manifest
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def save_document(self, task_id: str, filename: str, content: bytes) -> Path:
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_DOCUMENT_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_DOCUMENT_SUFFIXES))
            raise InvalidDocumentError(f"only {supported} files are supported")

        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "document_write"):
                input_dir = ensure_dir(task_dir / "input")
                document_path = input_dir / f"original{suffix}"
                document_path.write_bytes(content)
                self.update_task(
                    task_id,
                    status="uploaded",
                    input_document=document_path.relative_to(task_dir).as_posix(),
                    original_filename=Path(filename).name,
                    input_format=_input_format(suffix),
                )
                return document_path
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def copy_document(self, task_id: str, source: str | Path) -> Path:
        source_path = Path(source)
        document_path = self.save_document(task_id, source_path.name, source_path.read_bytes())
        return document_path

    def get_input_document(self, task_id: str) -> Path:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "document_read"):
                task = self.get_task(task_id)
                input_document = task.get("input_document")
                if not input_document:
                    raise TaskNotReadyError(f"task has no uploaded document: {task_id}")
                document_path = task_dir / input_document
                if not document_path.exists():
                    raise TaskNotReadyError(f"uploaded document missing: {task_id}")
                return document_path
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def update_task(self, task_id: str, **patch: object) -> dict[str, Any]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "task_update"):
                task = read_json(task_dir / "task.json")
                task.update(patch)
                task["updated_at"] = _now_iso()
                write_json(task_dir / "task.json", task)
                return task
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def read_manifest(self, task_id: str) -> dict[str, Any] | None:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "manifest_read"):
                manifest_path = task_dir / "run_manifest.json"
                if not manifest_path.exists():
                    return None
                return read_json(manifest_path)
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def read_reqir(self, task_id: str) -> dict[str, Any]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "reqir_read"):
                self.require_readable_task(task_id)
                reqir_path = task_dir / "exports" / "reqir.json"
                if not reqir_path.exists():
                    raise TaskNotReadyError(f"reqir export missing: {task_id}")
                return read_json(reqir_path)
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def read_blocks(self, task_id: str) -> list[dict[str, Any]]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "blocks_read"):
                self.require_readable_task(task_id)
                blocks_path = task_dir / "parsed" / "blocks.json"
                if not blocks_path.exists():
                    raise BlocksNotFoundError(f"blocks not found: {task_id}")
                return [_normalize_block(block) for block in read_json(blocks_path)]
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def read_chunks(self, task_id: str) -> list[dict[str, Any]]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "chunks_read"):
                self.require_readable_task(task_id)
                chunks_path = task_dir / "parsed" / "chunks.json"
                if not chunks_path.exists():
                    raise BlocksNotFoundError(f"chunks not found: {task_id}")
                return read_json(chunks_path)
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def read_quarantined(self, task_id: str) -> dict[str, Any]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "quarantine_read"):
                self.require_readable_task(task_id)
                path = task_dir / "extracted" / "reqir.quarantined.json"
                if not path.exists():
                    raise TaskNotReadyError(f"quarantined ReqIR artifact missing: {task_id}")
                return read_json(path)
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def require_readable_task(self, task_id: str) -> dict[str, Any]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "task_readability_check"):
                task = self.get_task(task_id)
                if task.get("status") not in READABLE_TASK_STATUSES:
                    raise TaskNotReadyError(f"task is not completed: {task_id}")
                return task
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def get_export_path(self, task_id: str, filename: str) -> Path:
        if filename not in {"reqir.json", "requirements.xlsx"}:
            raise FileNotFoundError(filename)
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "export_read"):
                return task_dir / "exports" / filename
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def read_export(self, task_id: str, filename: str) -> bytes:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "export_read"):
                self.require_readable_task(task_id)
                path = self.get_export_path(task_id, filename)
                if not path.exists():
                    raise FileNotFoundError(filename)
                return path.read_bytes()
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def reset_output_from_pipeline(self, task_id: str) -> None:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "pipeline_reset"):
                for name in ["parsed", "extracted", "review", "exports"]:
                    target = task_dir / name
                    if target.exists():
                        shutil.rmtree(target)
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_block(block: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(block)
    order_index = normalized.pop("order_index", None)
    if "order" not in normalized and order_index is not None:
        normalized["order"] = order_index
    return normalized


def _input_format(suffix: str) -> str:
    if suffix in {".md", ".markdown"}:
        return "markdown"
    return suffix.lstrip(".")
