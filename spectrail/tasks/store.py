from __future__ import annotations

import shutil
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from spectrail.core.io import ensure_dir, read_json, write_json
from spectrail.core.models import DocumentBlock
from spectrail.evidence import (
    EvidenceIndex,
    TableEvidenceView,
    TableEvidenceViewNotFoundError,
    build_table_evidence_view,
    sha256_file,
    validate_evidence_fingerprint,
)
from spectrail.evidence.pdf_preview import (
    PdfPagePreviewNotFoundError,
    PdfPagePreviewUnavailableError,
    render_pdf_page,
)
from spectrail.evidence.index_builder import (
    validate_evidence_index_against_parsed_document,
)
from spectrail.parsers import ParsedDocument, SUPPORTED_DOCUMENT_SUFFIXES
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


class BlocksUnavailableError(TaskStoreError):
    pass


class PagePreviewUnavailableError(TaskStoreError):
    pass


class PagePreviewNotFoundError(TaskStoreError):
    pass


class TableEvidenceUnavailableError(TaskStoreError):
    pass


class TableEvidenceNotFoundError(TaskStoreError):
    pass


class EvidenceVersionChangedError(TaskStoreError):
    pass


class LegacyEvidenceContinuationRebuildRequiredError(TaskStoreError):
    pass


# Backward-compatible import name for callers of the first table-only API.
TableEvidenceVersionChangedError = EvidenceVersionChangedError


READABLE_TASK_STATUSES = {"completed", "completed_with_warnings"}
DEFAULT_EVIDENCE_CACHE_MAX_TASKS = 16


@dataclass
class _TaskEvidenceCacheEntry:
    file_signature: tuple[int, int, int, int, int]
    index: EvidenceIndex
    blocks_file_signature: tuple[int, int, int, int, int] | None = None
    blocks: tuple[DocumentBlock, ...] | None = None
    source_file_signature: tuple[int, int, int, int, int] | None = None
    source_sha256: str | None = None
    projections: dict[tuple[str, str], TableEvidenceView] = field(
        default_factory=dict
    )


class LocalTaskStore:
    def __init__(
        self,
        root: str | Path = "outputs/tasks",
        *,
        evidence_cache_max_tasks: int = DEFAULT_EVIDENCE_CACHE_MAX_TASKS,
    ) -> None:
        if evidence_cache_max_tasks < 1:
            raise ValueError("evidence_cache_max_tasks must be positive")
        self.root = Path(root)
        self._evidence_cache_max_tasks = evidence_cache_max_tasks
        self._evidence_cache: OrderedDict[str, _TaskEvidenceCacheEntry] = (
            OrderedDict()
        )
        self._evidence_cache_lock = RLock()

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
                self._invalidate_evidence_cache(task_id)
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

    def read_blocks(
        self,
        task_id: str,
        *,
        expected_evidence_fingerprint: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "blocks_read"):
                self.require_readable_task(task_id)
                blocks_path = task_dir / "parsed" / "blocks.json"
                if not blocks_path.exists():
                    raise BlocksNotFoundError(f"blocks not found: {task_id}")
                try:
                    cache_entry = self._validated_evidence_cache_entry(
                        task_id,
                        task_dir,
                    )
                    self._require_evidence_version(
                        cache_entry,
                        expected_evidence_fingerprint,
                    )
                    blocks_signature = _file_signature(blocks_path)
                    if (
                        cache_entry.blocks is None
                        or cache_entry.blocks_file_signature != blocks_signature
                    ):
                        blocks = [
                            DocumentBlock.model_validate(_normalize_block(block))
                            for block in read_json(blocks_path)
                        ]
                        parsed_document = ParsedDocument(
                            document_id=cache_entry.index.document_id,
                            document_name=cache_entry.index.document_name,
                            source_format=cache_entry.index.source_format,
                            parser_name=(
                                cache_entry.index.parser_identity.parser_name
                            ),
                            text="\n\n".join(block.text for block in blocks),
                            blocks=blocks,
                            parser_identity=cache_entry.index.parser_identity,
                        )
                        validate_evidence_index_against_parsed_document(
                            cache_entry.index,
                            parsed_document,
                        )
                        cache_entry.blocks = tuple(blocks)
                        cache_entry.blocks_file_signature = blocks_signature
                    cached_blocks = cache_entry.blocks
                    if cached_blocks is None:
                        raise BlocksUnavailableError(
                            f"validated blocks are unavailable: {task_id}"
                        )
                    return (
                        cache_entry.index.evidence_fingerprint,
                        [
                            block.model_dump(mode="json")
                            for block in cached_blocks
                        ],
                    )
                except EvidenceVersionChangedError:
                    raise
                except BlocksUnavailableError:
                    raise
                except (OSError, TypeError, ValueError) as exc:
                    raise BlocksUnavailableError(
                        f"blocks do not match the task EvidenceIndex: {task_id}"
                    ) from exc
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def read_table_evidence(
        self,
        task_id: str,
        *,
        table_id: str,
        block_id: str,
        expected_evidence_fingerprint: str,
    ) -> TableEvidenceView:
        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "table_evidence_read"):
                self.require_readable_task(task_id)
                try:
                    cache_entry = self._validated_evidence_cache_entry(
                        task_id,
                        task_dir,
                    )
                except (OSError, TypeError, ValueError) as exc:
                    raise TableEvidenceUnavailableError(
                        f"EvidenceIndex is invalid: {task_id}"
                    ) from exc
                self._require_evidence_version(
                    cache_entry,
                    expected_evidence_fingerprint,
                )
                try:
                    projection_key = (table_id, block_id)
                    with self._evidence_cache_lock:
                        projection = cache_entry.projections.get(projection_key)
                    if projection is None:
                        projection = build_table_evidence_view(
                            cache_entry.index,
                            task_id=task_id,
                            table_id=table_id,
                            block_id=block_id,
                        )
                        with self._evidence_cache_lock:
                            cache_entry.projections[projection_key] = projection
                    return projection
                except TableEvidenceViewNotFoundError as exc:
                    raise TableEvidenceNotFoundError(str(exc)) from exc
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def render_pdf_page_preview(
        self,
        task_id: str,
        page_number: int,
        *,
        expected_evidence_fingerprint: str,
    ) -> tuple[bytes, int, int, str]:
        if page_number < 1:
            raise PagePreviewNotFoundError("page number must be 1-based")

        task_dir = self.get_task_dir(task_id)
        try:
            with task_operation(task_dir, "page_preview_read"):
                task = read_json(task_dir / "task.json")
                if task.get("status") not in READABLE_TASK_STATUSES:
                    raise TaskNotReadyError(f"task is not completed: {task_id}")
                input_document = task.get("input_document")
                if not isinstance(input_document, str) or not input_document:
                    raise PagePreviewUnavailableError(
                        f"task has no uploaded document: {task_id}"
                    )
                document_path = (task_dir / input_document).resolve(strict=False)
                try:
                    document_path.relative_to(task_dir.resolve(strict=False))
                except ValueError as exc:
                    raise PagePreviewUnavailableError(
                        "task input document is outside the task directory"
                    ) from exc
                if document_path.suffix.lower() != ".pdf":
                    raise PagePreviewUnavailableError(
                        "page preview is available only for PDF tasks"
                    )
                if not document_path.is_file():
                    raise PagePreviewUnavailableError(
                        f"uploaded PDF is missing: {task_id}"
                    )
                try:
                    cache_entry = self._validated_evidence_cache_entry(
                        task_id,
                        task_dir,
                    )
                except (OSError, TypeError, ValueError) as exc:
                    raise PagePreviewUnavailableError(
                        f"EvidenceIndex is invalid: {task_id}"
                    ) from exc
                self._require_evidence_version(
                    cache_entry,
                    expected_evidence_fingerprint,
                )
                if cache_entry.index.source_format != "pdf":
                    raise PagePreviewUnavailableError(
                        "page preview EvidenceIndex is not for a PDF"
                    )
                (
                    current_source_sha256,
                    validated_source_signature,
                ) = self._cached_source_sha256(
                    cache_entry,
                    document_path,
                    task_id=task_id,
                )
                if current_source_sha256 != cache_entry.index.source_sha256:
                    raise EvidenceVersionChangedError(
                        "current PDF does not match the task EvidenceIndex"
                    )
                try:
                    content, width, height = render_pdf_page(
                        document_path,
                        page_number,
                    )
                    try:
                        rendered_source_signature = _file_signature(
                            document_path
                        )
                    except OSError as exc:
                        self._invalidate_cached_source(cache_entry)
                        raise PagePreviewUnavailableError(
                            f"failed to inspect rendered PDF source: {task_id}"
                        ) from exc
                    if (
                        rendered_source_signature
                        != validated_source_signature
                    ):
                        self._invalidate_cached_source(cache_entry)
                        raise PagePreviewUnavailableError(
                            f"PDF preview source changed while rendering: "
                            f"{task_id}"
                        )
                    return (
                        content,
                        width,
                        height,
                        cache_entry.index.evidence_fingerprint,
                    )
                except PdfPagePreviewNotFoundError as exc:
                    raise PagePreviewNotFoundError(str(exc)) from exc
                except PdfPagePreviewUnavailableError as exc:
                    raise PagePreviewUnavailableError(str(exc)) from exc
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
                self._invalidate_evidence_cache(task_id)
                for name in ["parsed", "extracted", "review", "exports"]:
                    target = task_dir / name
                    if target.exists():
                        shutil.rmtree(target)
        except TaskTransactionError as exc:
            raise TaskTransactionInProgressError(exc) from exc

    def _validated_evidence_cache_entry(
        self,
        task_id: str,
        task_dir: Path,
    ) -> _TaskEvidenceCacheEntry:
        evidence_path = task_dir / "parsed" / "evidence_index.json"
        if not evidence_path.exists():
            self._invalidate_evidence_cache(task_id)
            raise ValueError(f"EvidenceIndex not found: {task_id}")
        file_signature = _file_signature(evidence_path)
        with self._evidence_cache_lock:
            cache_entry = self._evidence_cache.get(task_id)
            if (
                cache_entry is not None
                and cache_entry.file_signature == file_signature
            ):
                self._evidence_cache.move_to_end(task_id)
                return cache_entry

        try:
            payload = read_json(evidence_path)
            if _requires_legacy_continuation_rebuild(payload):
                raise LegacyEvidenceContinuationRebuildRequiredError(
                    "legacy PDF table continuation Evidence must be rebuilt "
                    "with the current parser"
                )
            index = EvidenceIndex.model_validate(payload)
            validate_evidence_fingerprint(index)
        except (
            LegacyEvidenceContinuationRebuildRequiredError,
            OSError,
            TypeError,
            ValueError,
        ):
            self._invalidate_evidence_cache(task_id)
            raise
        cache_entry = _TaskEvidenceCacheEntry(
            file_signature=file_signature,
            index=index,
        )
        with self._evidence_cache_lock:
            self._evidence_cache[task_id] = cache_entry
            self._evidence_cache.move_to_end(task_id)
            while len(self._evidence_cache) > self._evidence_cache_max_tasks:
                self._evidence_cache.popitem(last=False)
        return cache_entry

    @staticmethod
    def _require_evidence_version(
        cache_entry: _TaskEvidenceCacheEntry,
        expected_evidence_fingerprint: str,
    ) -> None:
        if (
            cache_entry.index.evidence_fingerprint
            != expected_evidence_fingerprint
        ):
            raise EvidenceVersionChangedError(
                "ReqIR Evidence fingerprint does not match the current task "
                "EvidenceIndex"
            )

    def _invalidate_evidence_cache(self, task_id: str) -> None:
        with self._evidence_cache_lock:
            self._evidence_cache.pop(task_id, None)

    def _cached_source_sha256(
        self,
        cache_entry: _TaskEvidenceCacheEntry,
        document_path: Path,
        *,
        task_id: str,
    ) -> tuple[str, tuple[int, int, int, int, int]]:
        try:
            signature_before = _file_signature(document_path)
        except OSError as exc:
            raise PagePreviewUnavailableError(
                f"failed to inspect PDF preview source: {task_id}"
            ) from exc
        with self._evidence_cache_lock:
            if (
                cache_entry.source_file_signature == signature_before
                and cache_entry.source_sha256 is not None
            ):
                return cache_entry.source_sha256, signature_before
        try:
            source_sha256 = sha256_file(document_path)
            signature_after = _file_signature(document_path)
        except OSError as exc:
            raise PagePreviewUnavailableError(
                f"failed to hash PDF preview source: {task_id}"
            ) from exc
        if signature_after != signature_before:
            self._invalidate_cached_source(cache_entry)
            raise PagePreviewUnavailableError(
                f"PDF preview source changed while hashing: {task_id}"
            )
        with self._evidence_cache_lock:
            cache_entry.source_file_signature = signature_after
            cache_entry.source_sha256 = source_sha256
        return source_sha256, signature_after

    def _invalidate_cached_source(
        self,
        cache_entry: _TaskEvidenceCacheEntry,
    ) -> None:
        with self._evidence_cache_lock:
            cache_entry.source_file_signature = None
            cache_entry.source_sha256 = None


def _requires_legacy_continuation_rebuild(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    tables = payload.get("tables")
    if not isinstance(tables, list):
        return False
    return any(
        isinstance(table, dict)
        and table.get("continuation_role") in {"start", "continuation"}
        and table.get("continuation_basis") is None
        for table in tables
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


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
