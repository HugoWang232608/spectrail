from __future__ import annotations

import errno
import os
import shutil
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from spectrail.core.io import read_json, write_json

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


TASK_TRANSACTION_LOCKED = "TASK_TRANSACTION_LOCKED"
TASK_MIGRATION_INCOMPLETE = "TASK_MIGRATION_INCOMPLETE"
TASK_REVIEW_RECOVERY_REQUIRED = "TASK_REVIEW_RECOVERY_REQUIRED"
_MALFORMED_LOCK_STALE_AFTER_SECONDS = 300


class TaskTransactionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        self.retryable = code == TASK_TRANSACTION_LOCKED
        super().__init__(f"{code}: {message}")


_local = threading.local()


@contextmanager
def task_operation(
    task_dir: str | Path,
    operation: str,
) -> Iterator[None]:
    root = Path(task_dir).resolve(strict=False)
    held = _held_roots()
    if root in held:
        held[root] += 1
        try:
            ensure_task_transaction_clean(root)
            yield
        finally:
            held[root] -= 1
        return

    with task_lock(root, operation=operation, reclaim_stale=True):
        held[root] = 1
        try:
            ensure_task_transaction_clean(root)
            yield
        finally:
            held.pop(root, None)


@contextmanager
def task_lock(
    task_dir: str | Path,
    *,
    operation: str,
    reclaim_stale: bool = False,
) -> Iterator[None]:
    root = Path(task_dir).resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    lock_dir = root / ".task.lock"
    token = uuid4().hex
    with _task_advisory_lock(root) as advisory_acquired:
        if not advisory_acquired:
            raise TaskTransactionError(
                TASK_TRANSACTION_LOCKED,
                f"{root}; retry after the active task operation finishes",
            )
        acquired = _acquire_lock_dir(
            lock_dir,
            operation=operation,
            token=token,
            reclaim_stale=reclaim_stale,
            lock_protocol=(
                "advisory_v1"
                if fcntl is not None or msvcrt is not None
                else "pid_v1"
            ),
        )
        if not acquired:
            raise TaskTransactionError(
                TASK_TRANSACTION_LOCKED,
                f"{root}; retry after the active task operation finishes",
            )
        try:
            yield
        finally:
            _release_lock_dir(lock_dir, token)


def ensure_task_transaction_clean(task_dir: str | Path) -> None:
    root = Path(task_dir).resolve(strict=False)
    if (root / ".task.lock").exists() and root not in _held_roots():
        raise TaskTransactionError(
            TASK_TRANSACTION_LOCKED,
            f"{root}; retry after the active task operation finishes",
        )
    staging_root = root / ".migration_tmp"
    if staging_root.exists():
        raise TaskTransactionError(
            TASK_MIGRATION_INCOMPLETE,
            f"run: spectrail migrate {root}",
        )
    from spectrail.review.transaction import (
        ReviewTransactionRecoveryError,
        cleanup_review_transaction_artifacts,
        recover_review_transaction,
    )

    try:
        cleanup_review_transaction_artifacts(root)
        recover_review_transaction(root)
    except ReviewTransactionRecoveryError as exc:
        raise TaskTransactionError(
            TASK_REVIEW_RECOVERY_REQUIRED,
            f"{root}; {exc}",
        ) from exc


def task_root_for_artifact(path: str | Path) -> Path | None:
    artifact = Path(path).resolve(strict=False)
    for candidate in [artifact.parent, *artifact.parents]:
        if any(
            (candidate / marker).exists()
            for marker in (
                "run_manifest.json",
                "task.json",
                ".migration_tmp",
                ".review_transaction",
                ".task.lock",
                ".task.lock.guard",
            )
        ):
            return candidate
    if artifact.parent.name in {"exports", "extracted", "parsed", "review"}:
        return artifact.parent.parent
    return None


def _acquire_lock_dir(
    lock_dir: Path,
    *,
    operation: str,
    token: str,
    reclaim_stale: bool,
    lock_protocol: str,
) -> bool:
    for _ in range(2):
        if lock_dir.is_symlink():
            return False
        temporary_lock_dir = lock_dir.with_name(f"{lock_dir.name}.{token}")
        shutil.rmtree(temporary_lock_dir, ignore_errors=True)
        try:
            temporary_lock_dir.mkdir()
            owner_path = temporary_lock_dir / "owner.json"
            write_json(
                owner_path,
                {
                    "schema_version": "task_lock_v2",
                    "token": token,
                    "operation": operation,
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "started_at": _now_iso(),
                    "lock_protocol": lock_protocol,
                },
            )
            _fsync_file(owner_path)
            _fsync_directory(temporary_lock_dir)
            os.rename(temporary_lock_dir, lock_dir)
            _fsync_directory(lock_dir.parent)
            return True
        except OSError:
            shutil.rmtree(temporary_lock_dir, ignore_errors=True)
            if not lock_dir.exists():
                raise
            if not reclaim_stale or not _reclaim_stale_lock(lock_dir):
                return False
            continue
        except Exception:
            shutil.rmtree(temporary_lock_dir, ignore_errors=True)
            raise
    return False


def _reclaim_stale_lock(lock_dir: Path) -> bool:
    owner_path = lock_dir / "owner.json"
    try:
        owner = read_json(owner_path)
        schema_version = owner["schema_version"]
        host = owner["host"]
        pid = owner["pid"]
        if schema_version == "task_lock_v1":
            lock_protocol = "pid_v1"
        elif schema_version == "task_lock_v2":
            lock_protocol = owner["lock_protocol"]
        else:
            raise ValueError("task lock schema version is invalid")
        if not isinstance(host, str) or isinstance(pid, bool) or not isinstance(pid, int):
            raise ValueError("task lock owner is invalid")
        if lock_protocol not in {"pid_v1", "advisory_v1"}:
            raise ValueError("task lock protocol is invalid")
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        if not _malformed_lock_is_stale(lock_dir):
            return False
        host = socket.gethostname()
        pid = -1
        lock_protocol = "pid_v1"
    if lock_protocol != "advisory_v1" and (
        host != socket.gethostname() or _pid_is_alive(pid)
    ):
        return False
    stale_dir = lock_dir.with_name(f".task.lock.stale.{uuid4().hex}")
    try:
        lock_dir.replace(stale_dir)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    shutil.rmtree(stale_dir, ignore_errors=True)
    return True


def _release_lock_dir(lock_dir: Path, token: str) -> None:
    try:
        owner = read_json(lock_dir / "owner.json")
    except (FileNotFoundError, TypeError, ValueError):
        return
    if owner.get("token") == token:
        shutil.rmtree(lock_dir, ignore_errors=True)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _malformed_lock_is_stale(lock_dir: Path) -> bool:
    try:
        age = time.time() - lock_dir.stat().st_mtime
    except OSError:
        return False
    return age >= _MALFORMED_LOCK_STALE_AFTER_SECONDS


@contextmanager
def _task_advisory_lock(task_root: Path) -> Iterator[bool]:
    guard_path = task_root / ".task.lock.guard"
    if guard_path.is_symlink():
        yield False
        return
    handle = guard_path.open("a+b")
    acquired = True
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                handle.close()
                raise
            acquired = False
    elif msvcrt is not None:  # pragma: no cover - Windows
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                handle.close()
                raise
            acquired = False
    try:
        yield acquired
    finally:
        if acquired and fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        elif acquired and msvcrt is not None:  # pragma: no cover - Windows
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


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


def _held_roots() -> dict[Path, int]:
    held = getattr(_local, "held_roots", None)
    if held is None:
        held = {}
        _local.held_roots = held
    return held


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
