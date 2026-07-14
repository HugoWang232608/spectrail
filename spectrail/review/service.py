from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from spectrail.core.io import (
    read_reqir_items,
    reqir_package_dump,
    write_json,
)
from spectrail.core.models import RequirementIR
from spectrail.exporters.xlsx_exporter import export_requirements_xlsx
from spectrail.review.review_log import collect_review_log
from spectrail.review.review_state import apply_review_action


ReqListAdapter = TypeAdapter(list[RequirementIR])


def load_requirements(path: str | Path) -> list[RequirementIR]:
    return ReqListAdapter.validate_python(read_reqir_items(path))


def refresh_review_package(
    reqir_path: str | Path,
    review_log_path: str | Path,
    xlsx_path: str | Path,
    requirements: list[RequirementIR],
) -> None:
    write_json(review_log_path, collect_review_log(requirements))
    write_json(
        reqir_path,
        reqir_package_dump(
            requirements,
            metadata={"export_state": "review_snapshot"},
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
    requirements = load_requirements(reqir_path)
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
    refresh_review_package(reqir_path, review_log_path, xlsx_path, requirements)
    return target
