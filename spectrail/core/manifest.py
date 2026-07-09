from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def init_manifest(
    task_id: str,
    input_document: str,
    output_dir: str,
    model_mode: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": "running",
        "input_document": input_document,
        "output_dir": output_dir,
        "model_mode": model_mode,
        "started_at": _now_iso(),
        "completed_at": None,
        "counts": {},
        "outputs": {},
        "error": None,
    }


def complete_manifest(
    manifest: dict[str, Any],
    counts: dict[str, int],
    outputs: dict[str, str],
) -> dict[str, Any]:
    updated = dict(manifest)
    updated.update(
        {
            "status": "completed",
            "completed_at": _now_iso(),
            "counts": counts,
            "outputs": outputs,
            "error": None,
        }
    )
    return updated


def fail_manifest(manifest: dict[str, Any], error: str) -> dict[str, Any]:
    updated = dict(manifest)
    updated.update(
        {
            "status": "failed",
            "completed_at": _now_iso(),
            "error": error,
        }
    )
    return updated


def relative_output(path: str | Path, output_dir: str | Path) -> str:
    return Path(path).relative_to(output_dir).as_posix()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
