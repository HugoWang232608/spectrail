from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from spectrail.core.io import read_json
from spectrail.evidence.fingerprint import sha256_file


REVIEW_TRANSACTION_DIRECTORY = ".review_transaction"
REVIEW_TRANSACTION_RECOVERY_REQUIRED = "TASK_REVIEW_RECOVERY_REQUIRED"
_REVIEW_PREPARATION_PATTERN = re.compile(r"^\.review_prepare_[0-9a-f]{32}$")
_REVIEW_CLEANUP_PATTERN = re.compile(
    r"^\.review_(?:committed|recovered)_[0-9a-f]{32}$"
)
_REVIEW_TARGETS = {
    "review/review_log.json",
    "exports/reqir.json",
    "exports/requirements.xlsx",
}


class ReviewTransactionRecoveryError(RuntimeError):
    pass


class ReviewTransactionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Literal[
        "review/review_log.json",
        "exports/reqir.json",
        "exports/requirements.xlsx",
    ]
    existed: StrictBool
    old_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    new_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_old_identity(self) -> "ReviewTransactionTarget":
        if self.existed != (self.old_sha256 is not None):
            raise ValueError(
                "review target existence does not match its old hash"
            )
        return self


class ReviewTransactionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["review_transaction_v1"]
    transaction_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal["prepared", "committing", "committed"]
    targets: list[ReviewTransactionTarget]

    @model_validator(mode="after")
    def validate_target_set(self) -> "ReviewTransactionState":
        paths = [target.path for target in self.targets]
        if len(paths) != len(set(paths)) or set(paths) != _REVIEW_TARGETS:
            raise ValueError(
                "review transaction must contain the complete target set"
            )
        return self


def publish_review_transaction(
    task_root: str | Path,
    preparation_root: str | Path,
    artifacts: list[tuple[Path, Path]],
) -> None:
    root = Path(task_root).resolve(strict=False)
    preparation = _path_within_root(root, Path(preparation_root))
    marker = root / REVIEW_TRANSACTION_DIRECTORY
    if marker.exists() or marker.is_symlink():
        raise ReviewTransactionRecoveryError(
            "an unfinished review transaction already exists"
        )

    transaction_id = _preparation_transaction_id(preparation)
    targets: list[ReviewTransactionTarget] = []
    backup_root = preparation / "backup"
    backup_root.mkdir()
    seen_paths: set[str] = set()
    for staged, target in artifacts:
        staged_path = _path_within_root(preparation, staged)
        relative = _review_target_relative_path(root, target)
        if relative in seen_paths:
            raise ValueError("review transaction target paths must be unique")
        seen_paths.add(relative)
        target_path = _review_target_path(root, relative)
        existed = target_path.exists()
        old_sha256 = sha256_file(target_path) if existed else None
        backup = _transaction_artifact_path(
            preparation,
            "backup",
            relative,
        )
        if existed:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_path, backup)
            _fsync_file(backup)
            if sha256_file(backup) != old_sha256:
                raise OSError(
                    f"review target changed while backing it up: {relative}"
                )
        _fsync_file(staged_path)
        targets.append(
            ReviewTransactionTarget(
                path=relative,
                existed=existed,
                old_sha256=old_sha256,
                new_sha256=sha256_file(staged_path),
            )
        )

    state = ReviewTransactionState(
        schema_version="review_transaction_v1",
        transaction_id=transaction_id,
        status="prepared",
        targets=targets,
    )
    _write_state_atomic(preparation / "transaction.json", state)
    _fsync_directory_tree(preparation)
    os.replace(preparation, marker)
    _fsync_directory(root)

    commit_recorded = False
    try:
        state.status = "committing"
        _write_state_atomic(marker / "transaction.json", state)
        for target_state in state.targets:
            staged = _transaction_artifact_path(
                marker,
                "staged",
                target_state.path,
            )
            target = _review_target_path(root, target_state.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
            _fsync_file(target)
            _fsync_directory(target.parent)
        _validate_published_targets(root, state)
        state.status = "committed"
        _write_state_atomic(marker / "transaction.json", state)
        commit_recorded = True
        _finish_transaction(root, marker, state, outcome="committed")
    except BaseException as publication_error:
        try:
            recover_review_transaction(root)
        except Exception as recovery_error:
            raise ReviewTransactionRecoveryError(
                "review publication failed and persistent recovery is required"
            ) from recovery_error
        if commit_recorded:
            return
        raise publication_error


def cleanup_review_transaction_artifacts(task_root: str | Path) -> None:
    root = Path(task_root).resolve(strict=False)
    if not root.exists():
        return
    removed = False
    for candidate in root.iterdir():
        if (
            _REVIEW_PREPARATION_PATTERN.fullmatch(candidate.name) is None
            and _REVIEW_CLEANUP_PATTERN.fullmatch(candidate.name) is None
        ):
            continue
        _remove_internal_path(candidate)
        removed = True
    if removed:
        _fsync_directory(root)


def recover_review_transaction(task_root: str | Path) -> None:
    root = Path(task_root).resolve(strict=False)
    marker = root / REVIEW_TRANSACTION_DIRECTORY
    if not marker.exists() and not marker.is_symlink():
        return
    if marker.is_symlink() or not marker.is_dir():
        raise ReviewTransactionRecoveryError(
            "review transaction marker is not a trusted directory"
        )
    try:
        state = ReviewTransactionState.model_validate(
            read_json(marker / "transaction.json")
        )
    except Exception as exc:
        raise ReviewTransactionRecoveryError(
            "review transaction state is invalid"
        ) from exc

    if state.status == "committed":
        try:
            _validate_published_targets(root, state)
            _finish_transaction(root, marker, state, outcome="committed")
        except Exception as exc:
            raise ReviewTransactionRecoveryError(
                "committed review transaction content is invalid"
            ) from exc
        return

    try:
        restore_root = marker / "restore"
        restore_root.mkdir(exist_ok=True)
        for target_state in state.targets:
            target = _review_target_path(root, target_state.path)
            if target_state.existed:
                backup = _transaction_artifact_path(
                    marker,
                    "backup",
                    target_state.path,
                )
                if (
                    backup.is_symlink()
                    or not backup.is_file()
                    or sha256_file(backup) != target_state.old_sha256
                ):
                    raise ReviewTransactionRecoveryError(
                        f"review backup is invalid: {target_state.path}"
                    )
                restore = _transaction_artifact_path(
                    marker,
                    "restore",
                    target_state.path,
                )
                restore.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, restore)
                _fsync_file(restore)
                os.replace(restore, target)
                _fsync_file(target)
                _fsync_directory(target.parent)
            elif target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    raise ReviewTransactionRecoveryError(
                        f"review target became a directory: {target_state.path}"
                    )
                target.unlink()
                _fsync_directory(target.parent)
        _validate_restored_targets(root, state)
        _finish_transaction(root, marker, state, outcome="recovered")
    except ReviewTransactionRecoveryError:
        raise
    except Exception as exc:
        raise ReviewTransactionRecoveryError(
            "review transaction rollback failed"
        ) from exc


def _validate_published_targets(
    root: Path,
    state: ReviewTransactionState,
) -> None:
    for target_state in state.targets:
        target = _review_target_path(root, target_state.path)
        if (
            target.is_symlink()
            or not target.is_file()
            or sha256_file(target) != target_state.new_sha256
        ):
            raise ReviewTransactionRecoveryError(
                f"published review target is invalid: {target_state.path}"
            )


def _validate_restored_targets(
    root: Path,
    state: ReviewTransactionState,
) -> None:
    for target_state in state.targets:
        target = _review_target_path(root, target_state.path)
        if target_state.existed:
            if (
                target.is_symlink()
                or not target.is_file()
                or sha256_file(target) != target_state.old_sha256
            ):
                raise ReviewTransactionRecoveryError(
                    f"restored review target is invalid: {target_state.path}"
                )
        elif target.exists() or target.is_symlink():
            raise ReviewTransactionRecoveryError(
                f"new review target was not removed: {target_state.path}"
            )


def _finish_transaction(
    root: Path,
    marker: Path,
    state: ReviewTransactionState,
    *,
    outcome: Literal["committed", "recovered"],
) -> None:
    cleanup = root / f".review_{outcome}_{state.transaction_id}"
    if cleanup.exists() or cleanup.is_symlink():
        _remove_internal_path(cleanup)
    os.replace(marker, cleanup)
    _fsync_directory(root)
    shutil.rmtree(cleanup)
    _fsync_directory(root)


def _preparation_transaction_id(preparation: Path) -> str:
    match = _REVIEW_PREPARATION_PATTERN.fullmatch(preparation.name)
    if match is None:
        raise ValueError("review preparation directory name is invalid")
    return preparation.name.removeprefix(".review_prepare_")


def _review_target_relative_path(root: Path, target: Path) -> str:
    resolved = _path_within_root(root, target)
    relative = resolved.relative_to(root).as_posix()
    if relative not in _REVIEW_TARGETS:
        raise ValueError(f"unsupported review transaction target: {relative}")
    return relative


def _review_target_path(root: Path, relative: str) -> Path:
    if relative not in _REVIEW_TARGETS:
        raise ReviewTransactionRecoveryError(
            "review transaction target is not allowed"
        )
    return _path_within_root(root, root / relative)


def _transaction_artifact_path(
    transaction_root: Path,
    category: Literal["staged", "backup", "restore"],
    relative: str,
) -> Path:
    if relative not in _REVIEW_TARGETS:
        raise ReviewTransactionRecoveryError(
            "review transaction artifact path is not allowed"
        )
    return _path_within_root(
        transaction_root,
        transaction_root / category / relative,
    )


def _path_within_root(root: Path, path: Path) -> Path:
    resolved_root = root.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ReviewTransactionRecoveryError(
            "review transaction path escapes its task directory"
        ) from exc
    return resolved_path


def _write_state_atomic(
    path: Path,
    state: ReviewTransactionState,
) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(
            state.model_dump(mode="json"),
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory_tree(root: Path) -> None:
    directories = [root, *(path for path in root.rglob("*") if path.is_dir())]
    for directory in sorted(
        directories,
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _remove_internal_path(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)
