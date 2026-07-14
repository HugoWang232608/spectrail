from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from spectrail.core.io import (
    read_reqir_package,
    read_reqir_items,
    reqir_package_dump,
    write_json,
)
from spectrail.core.models import ReqIRPackage, RequirementIR
from spectrail.exporters.xlsx_exporter import export_requirements_xlsx
from spectrail.review.review_log import collect_review_log
from spectrail.review.review_state import apply_review_action
from spectrail.task_transactions import task_operation, task_root_for_artifact


ReqListAdapter = TypeAdapter(list[RequirementIR])


def load_requirements(path: str | Path) -> list[RequirementIR]:
    return ReqListAdapter.validate_python(read_reqir_items(path))


def load_requirement_package(path: str | Path) -> ReqIRPackage:
    return read_reqir_package(path)


def refresh_review_package(
    reqir_path: str | Path,
    review_log_path: str | Path,
    xlsx_path: str | Path,
    requirements: list[RequirementIR],
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    task_root = task_root_for_artifact(reqir_path)
    if task_root is None:
        return _refresh_review_package_locked(
            reqir_path,
            review_log_path,
            xlsx_path,
            requirements,
            metadata=metadata,
        )
    with task_operation(task_root, "review_refresh"):
        return _refresh_review_package_locked(
            reqir_path,
            review_log_path,
            xlsx_path,
            requirements,
            metadata=metadata,
        )


def _refresh_review_package_locked(
    reqir_path: str | Path,
    review_log_path: str | Path,
    xlsx_path: str | Path,
    requirements: list[RequirementIR],
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    write_json(review_log_path, collect_review_log(requirements))
    write_json(
        reqir_path,
        reqir_package_dump(
            requirements,
            metadata={
                **(metadata or {}),
                "export_state": "review_snapshot",
            },
        ),
    )
    export_requirements_xlsx(requirements, xlsx_path)


def apply_review_to_package(
    reqir_path: str | Path,
    review_log_path: str | Path,
    xlsx_path: str | Path,
    requirement_id: str,
    action: str,
    patch: dict[str, Any] | None = None,
    reviewer: str | None = None,
    reason: str | None = None,
) -> RequirementIR:
    task_root = task_root_for_artifact(reqir_path)
    if task_root is None:
        return _apply_review_to_package_locked(
            reqir_path=reqir_path,
            review_log_path=review_log_path,
            xlsx_path=xlsx_path,
            requirement_id=requirement_id,
            action=action,
            patch=patch,
            reviewer=reviewer,
            reason=reason,
        )
    with task_operation(task_root, "review_apply"):
        return _apply_review_to_package_locked(
            reqir_path=reqir_path,
            review_log_path=review_log_path,
            xlsx_path=xlsx_path,
            requirement_id=requirement_id,
            action=action,
            patch=patch,
            reviewer=reviewer,
            reason=reason,
        )


def _apply_review_to_package_locked(
    reqir_path: str | Path,
    review_log_path: str | Path,
    xlsx_path: str | Path,
    requirement_id: str,
    action: str,
    patch: dict[str, Any] | None = None,
    reviewer: str | None = None,
    reason: str | None = None,
) -> RequirementIR:
    package = load_requirement_package(reqir_path)
    requirements = package.items
    target = next((req for req in requirements if req.id == requirement_id), None)
    if target is None:
        raise ValueError(f"requirement not found: {requirement_id}")

    apply_review_action(
        target,
        action,
        patch=patch,
        reviewer=reviewer,
        reason=reason,
    )
    refresh_review_package(
        reqir_path,
        review_log_path,
        xlsx_path,
        requirements,
        metadata=package.metadata,
    )
    return target
