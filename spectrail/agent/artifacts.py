from __future__ import annotations

import shutil
from pathlib import Path

from spectrail.task_transactions import task_operation_is_held


PIPELINE_MANAGED_DIRECTORIES = ("parsed", "extracted", "review", "exports")
PIPELINE_MANAGED_FILES = ("plan.json", "run_manifest.json")
AGENT_MANAGED_DIRECTORY = "agent"


class AgentArtifactResetError(ValueError):
    pass


def reset_pipeline_artifacts_for_agent_retry(task_dir: str | Path) -> None:
    root = _require_transaction(task_dir)
    _remove_allowlisted(root, include_agent=False)


def prepare_new_agent_generation(task_dir: str | Path) -> None:
    root = _require_transaction(task_dir)
    _remove_allowlisted(root, include_agent=True)


def _require_transaction(task_dir: str | Path) -> Path:
    root = Path(task_dir).resolve(strict=False)
    if not task_operation_is_held(root):
        raise AgentArtifactResetError("AGENT_ARTIFACT_RESET_TRANSACTION_REQUIRED")
    return root


def _remove_allowlisted(root: Path, *, include_agent: bool) -> None:
    directory_names = [*PIPELINE_MANAGED_DIRECTORIES]
    if include_agent:
        directory_names.append(AGENT_MANAGED_DIRECTORY)
    targets = [
        *[(root / name, "directory") for name in directory_names],
        *[(root / name, "file") for name in PIPELINE_MANAGED_FILES],
    ]
    for target, expected_type in targets:
        _validate_target(root, target, expected_type=expected_type)
    for target, expected_type in targets:
        if not target.exists():
            continue
        if expected_type == "directory":
            shutil.rmtree(target)
        else:
            target.unlink()


def _validate_target(root: Path, target: Path, *, expected_type: str) -> None:
    if target.parent != root or target.name in {"", ".", ".."}:
        raise AgentArtifactResetError("AGENT_ARTIFACT_RESET_TARGET_OUTSIDE_ROOT")
    if target.is_symlink():
        raise AgentArtifactResetError(
            f"AGENT_ARTIFACT_RESET_SYMLINK: {target.name}"
        )
    if not target.exists():
        return
    if expected_type == "directory" and not target.is_dir():
        raise AgentArtifactResetError(
            f"AGENT_ARTIFACT_RESET_TYPE_MISMATCH: {target.name}"
        )
    if expected_type == "file" and not target.is_file():
        raise AgentArtifactResetError(
            f"AGENT_ARTIFACT_RESET_TYPE_MISMATCH: {target.name}"
        )
