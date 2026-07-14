from __future__ import annotations

import os
import shutil
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from spectrail.core.io import read_json, write_json


class TaskTransactionError(ValueError):
    pass


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

    with task_lock(root, operation=operation):
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
    acquired = _acquire_lock_dir(
        lock_dir,
        operation=operation,
        token=token,
        reclaim_stale=reclaim_stale,
    )
    if not acquired:
        raise TaskTransactionError(
            f"TASK_TRANSACTION_LOCKED: {root}; retry after the active task operation finishes"
        )
    try:
        yield
    finally:
        _release_lock_dir(lock_dir, token)


def ensure_task_transaction_clean(task_dir: str | Path) -> None:
    root = Path(task_dir).resolve(strict=False)
    if (root / ".task.lock").exists() and root not in _held_roots():
        raise TaskTransactionError(
            f"TASK_TRANSACTION_LOCKED: {root}; retry after the active task operation finishes"
        )
    staging_root = root / ".migration_tmp"
    if staging_root.exists():
        raise TaskTransactionError(
            "TASK_MIGRATION_INCOMPLETE: run: "
            f"spectrail migrate {root}"
        )


def task_root_for_artifact(path: str | Path) -> Path | None:
    artifact = Path(path).resolve(strict=False)
    for candidate in [artifact.parent, *artifact.parents]:
        if any(
            (candidate / marker).exists()
            for marker in (
                "run_manifest.json",
                "task.json",
                ".migration_tmp",
                ".task.lock",
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
) -> bool:
    for _ in range(2):
        if lock_dir.is_symlink():
            return False
        try:
            lock_dir.mkdir()
        except FileExistsError:
            if not reclaim_stale or not _reclaim_stale_lock(lock_dir):
                return False
            continue
        write_json(
            lock_dir / "owner.json",
            {
                "schema_version": "task_lock_v1",
                "token": token,
                "operation": operation,
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started_at": _now_iso(),
            },
        )
        return True
    return False


def _reclaim_stale_lock(lock_dir: Path) -> bool:
    owner_path = lock_dir / "owner.json"
    try:
        owner = read_json(owner_path)
        host = owner["host"]
        pid = owner["pid"]
        if not isinstance(host, str) or isinstance(pid, bool) or not isinstance(pid, int):
            return False
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return False
    if host != socket.gethostname() or _pid_is_alive(pid):
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


def _held_roots() -> dict[Path, int]:
    held = getattr(_local, "held_roots", None)
    if held is None:
        held = {}
        _local.held_roots = held
    return held


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
